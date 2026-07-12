"""기대치·편차 탐지 → flags 테이블 (모듈④, STEP 3)

기대치 = peer 중위수(50%) + 자사 전년도(50%) 가중 결합 (둘 중 하나만 있으면 그것만 사용)
편차   = (실제 - 기대치) / peer IQR (Q75-Q25)
종합 스코어 = |편차| 상위 3개 비율의 평균
임계값 = 종합 스코어 상위 10% -> is_flagged = 1

실행: python detect_flags.py
"""
import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL
from peer_group import get_peer_group

RATIO_COLS = [
    "receivables_turnover", "inventory_turnover", "gp_margin", "operating_margin",
    "sga_ratio", "debt_ratio", "interest_coverage", "total_accruals_ratio",
    "oi_cfo_gap_ratio", "revenue_growth",
]

OWN_WEIGHT = 0.5          # 기대치 중 자사 전년도 가중치
PEER_WEIGHT = 0.5         # 기대치 중 peer 중위수 가중치
MIN_PEER_VALUES = 5       # peer 중위수/IQR을 신뢰하기 위한 최소 유효 peer 수
TOP_N_CONTRIBUTORS = 3    # 종합 스코어에 반영할 상위 기여 비율 개수
FLAG_THRESHOLD_PCT = 90   # 상위 10% = 90th percentile 이상

# 분모(전년 매출 등)가 0에 가까워 비율이 비현실적으로 폭발하는 경우 미사용 처리
# (LIMITATIONS.md §9 참고). |값| > 10 (=1000%) 이면 분모 왜소화로 판단.
SANITY_BOUNDS = {"revenue_growth": 10.0}

DDL = """
CREATE TABLE IF NOT EXISTS flags (
    corp_code        VARCHAR(8) NOT NULL,
    bsns_year        INT NOT NULL,
    composite_score  NUMERIC,
    n_ratios_used    INT NOT NULL,
    is_flagged       INT NOT NULL,
    top1_ratio       VARCHAR(40), top1_deviation NUMERIC, top1_direction VARCHAR(4),
    top2_ratio       VARCHAR(40), top2_deviation NUMERIC, top2_direction VARCHAR(4),
    top3_ratio       VARCHAR(40), top3_deviation NUMERIC, top3_direction VARCHAR(4),
    PRIMARY KEY (corp_code, bsns_year)
);
"""


def build_lookup(ratios: pd.DataFrame) -> dict:
    """(corp_code, bsns_year) -> {ratio_col: value} 딕셔너리로 변환 (반복 조회용).
    SANITY_BOUNDS를 벗어나는 값은 분모 왜소화로 인한 극단값으로 보고 None 처리한다."""
    lookup = {}
    for row in ratios.itertuples(index=False):
        d = row._asdict()
        key = (d["corp_code"], int(d["bsns_year"]))
        values = {}
        for c in RATIO_COLS:
            v = d[c]
            bound = SANITY_BOUNDS.get(c)
            if v is not None and not pd.isna(v) and bound is not None and abs(v) > bound:
                v = None
            values[c] = v
        lookup[key] = values
    return lookup


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = pd.Series(values)
    return float(s.quantile(q))


def compute_expectation(own_prior, peer_median):
    if own_prior is not None and peer_median is not None:
        return OWN_WEIGHT * own_prior + PEER_WEIGHT * peer_median
    if peer_median is not None:
        return peer_median
    if own_prior is not None:
        return own_prior
    return None


def compute_row_deviations(lookup: dict, corp_code: str, year: int, peers: list[str]) -> dict:
    """비율별 {"deviation": ..., "direction": ...} 딕셔너리. 계산 불가한 비율은 제외."""
    cur = lookup.get((corp_code, year), {})
    prior = lookup.get((corp_code, year - 1), {})

    result = {}
    for ratio in RATIO_COLS:
        actual = cur.get(ratio)
        if actual is None or pd.isna(actual):
            continue

        own_prior = prior.get(ratio)
        if own_prior is not None and pd.isna(own_prior):
            own_prior = None

        peer_values = []
        for p in peers:
            v = lookup.get((p, year), {}).get(ratio)
            if v is not None and not pd.isna(v):
                peer_values.append(float(v))

        peer_median = quantile(peer_values, 0.5) if len(peer_values) >= MIN_PEER_VALUES else None
        peer_iqr = None
        if len(peer_values) >= MIN_PEER_VALUES:
            q75, q25 = quantile(peer_values, 0.75), quantile(peer_values, 0.25)
            peer_iqr = q75 - q25

        expectation = compute_expectation(own_prior, peer_median)
        if expectation is None or peer_iqr is None or peer_iqr == 0:
            continue

        deviation = (float(actual) - expectation) / peer_iqr
        direction = "증가" if actual >= expectation else "감소"
        result[ratio] = {"deviation": deviation, "direction": direction}

    return result


def main():
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text(DDL))

    with engine.connect() as conn:
        ratios = pd.read_sql(
            text(f"SELECT corp_code, bsns_year, {', '.join(RATIO_COLS)} FROM ratios"), conn
        )
        universe = pd.read_sql(text("SELECT corp_code FROM analysis_universe"), conn)

    lookup = build_lookup(ratios)

    # peer group은 업종코드 기준(연도 무관)이라 기업당 한 번만 계산해 재사용
    peer_cache = {cc: get_peer_group(cc, engine)["peers"] for cc in universe["corp_code"]}

    records = []
    for row in ratios[["corp_code", "bsns_year"]].itertuples(index=False):
        corp_code, year = row.corp_code, int(row.bsns_year)
        peers = peer_cache.get(corp_code, [])
        devs = compute_row_deviations(lookup, corp_code, year, peers)

        rec = {
            "corp_code": corp_code, "bsns_year": year,
            "composite_score": None, "n_ratios_used": len(devs), "is_flagged": 0,
        }
        for i in range(1, TOP_N_CONTRIBUTORS + 1):
            rec[f"top{i}_ratio"] = None
            rec[f"top{i}_deviation"] = None
            rec[f"top{i}_direction"] = None

        if devs:
            ranked = sorted(devs.items(), key=lambda kv: abs(kv[1]["deviation"]), reverse=True)
            top = ranked[:TOP_N_CONTRIBUTORS]
            rec["composite_score"] = sum(abs(v["deviation"]) for _, v in top) / len(top)
            for i, (ratio, v) in enumerate(top, start=1):
                rec[f"top{i}_ratio"] = ratio
                rec[f"top{i}_deviation"] = v["deviation"]
                rec[f"top{i}_direction"] = v["direction"]

        records.append(rec)

    result_df = pd.DataFrame(records)

    scored = result_df["composite_score"].dropna()
    if not scored.empty:
        threshold = scored.quantile(FLAG_THRESHOLD_PCT / 100)
        result_df.loc[result_df["composite_score"] >= threshold, "is_flagged"] = 1

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM flags"))
        result_df.to_sql("flags", conn, if_exists="append", index=False)

    n_total = len(result_df)
    n_scored = len(scored)
    n_flagged = int(result_df["is_flagged"].sum())
    print(f"flags 테이블 생성 완료: {n_total:,}개 (기업x연도)")
    print(f"종합 스코어 계산 가능(유효 비율 1개 이상): {n_scored:,}개 ({n_scored / n_total * 100:.1f}%)")
    if not scored.empty:
        print(f"임계값(상위 10%, {FLAG_THRESHOLD_PCT}th percentile): {threshold:.3f}")
    print(f"플래그 처리(is_flagged=1): {n_flagged:,}개")

    coverage = result_df["n_ratios_used"].value_counts().sort_index()
    print("\n기업x연도별 유효 비율 개수 분포:")
    for n, cnt in coverage.items():
        print(f"  {n}개 비율: {cnt:,}건")


if __name__ == "__main__":
    main()

"""실무 배포용 당기(미확정) 실제값 입력 경로 (LIMITATIONS.md §11)

detect_flags.py는 STEP 4 백테스트 목적으로 "당기 실제값"도 DART 확정 공시를 그대로
쓴다. 하지만 실제 감사에서 분석 대상 "당기" 자료는 피감사회사가 아직 DART에 내지
않은 원본(raw) 자료다 — 그 자료는 회계사가 직접 입력해야 한다.

peer 그룹·자사 전년도는 기존과 동일하게 DART 확정 데이터(ratios 테이블)를 그대로
쓰고, "당기" 값만 사용자가 입력한 원본 계정값으로 대체한다. 편차 산식(기대치
가중결합·IQR 표준화·상위 3개 종합 스코어)은 detect_flags.py와 완전히 동일한
함수를 공유한다.
"""
import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL
from xbrl_mapping import ACCOUNT_CANDIDATES
from compute_ratios import safe_div
from peer_group import get_peer_group
from detect_flags import RATIO_COLS, SANITY_BOUNDS, deviation_for_value, rank_top_contributors

ACCOUNT_FIELDS = list(ACCOUNT_CANDIDATES.keys())

ACCOUNT_LABELS = {
    "revenue": "매출액", "cogs": "매출원가", "operating_income": "영업이익",
    "sga": "판매비와관리비", "interest_expense": "이자비용", "net_income": "당기순이익",
    "trade_receivables": "매출채권", "inventory": "재고자산", "total_assets": "자산총계",
    "total_liabilities": "부채총계", "total_equity": "자본총계", "operating_cf": "영업활동현금흐름",
}


def compute_live_ratios(accounts: dict, prev_accounts: dict) -> dict:
    """당기 입력 계정값 + 전기 DART 계정값(평균잔액용)으로 10개 비율 계산.
    compute_ratios.compute_row()와 동일한 산식(회전율 평균잔액, 발생액 등)."""

    def avg_balance(field):
        this_v, prev_v = accounts.get(field), prev_accounts.get(field)
        if this_v is None:
            return None
        if prev_v is None:
            return this_v
        return (this_v + prev_v) / 2

    revenue = accounts.get("revenue")
    cogs = accounts.get("cogs")
    operating_income = accounts.get("operating_income")
    sga = accounts.get("sga")
    interest_expense = accounts.get("interest_expense")
    net_income = accounts.get("net_income")
    total_assets = accounts.get("total_assets")
    total_liabilities = accounts.get("total_liabilities")
    total_equity = accounts.get("total_equity")
    operating_cf = accounts.get("operating_cf")
    prev_revenue = prev_accounts.get("revenue")

    return {
        "receivables_turnover": safe_div(revenue, avg_balance("trade_receivables")),
        "inventory_turnover": safe_div(cogs, avg_balance("inventory")),
        "gp_margin": safe_div(
            (revenue - cogs) if revenue is not None and cogs is not None else None, revenue
        ),
        "operating_margin": safe_div(operating_income, revenue),
        "sga_ratio": safe_div(sga, revenue),
        "debt_ratio": safe_div(total_liabilities, total_equity),
        "interest_coverage": safe_div(operating_income, interest_expense),
        "total_accruals_ratio": safe_div(
            (net_income - operating_cf) if net_income is not None and operating_cf is not None else None,
            total_assets,
        ),
        "oi_cfo_gap_ratio": safe_div(
            (operating_income - operating_cf) if operating_income is not None and operating_cf is not None else None,
            total_assets,
        ),
        "revenue_growth": safe_div(
            (revenue - prev_revenue) if revenue is not None and prev_revenue is not None else None,
            prev_revenue,
        ),
    }


def load_prev_accounts(corp_code: str, bsns_year: int, engine) -> dict:
    """전기(bsns_year-1) DART 확정 계정값 (평균잔액·매출증가율 계산용).
    compute_ratios.load_fields()와 동일한 fallback 우선순위 로직."""
    with engine.connect() as conn:
        raw = pd.read_sql(
            text(
                "SELECT sj_div, account_id, thstrm_amount FROM financial_statements "
                "WHERE corp_code = :cc AND bsns_year = :yr AND fs_div = 'CFS'"
            ),
            conn, params={"cc": corp_code, "yr": bsns_year - 1},
        )
    result = {}
    for field, candidates in ACCOUNT_CANDIDATES.items():
        for sj_div, account_id in candidates:
            matched = raw[(raw["sj_div"] == sj_div) & (raw["account_id"] == account_id)]
            if not matched.empty:
                result[field] = matched.iloc[0]["thstrm_amount"]
                break
    return result


def load_peer_ratios(peers: list[str], bsns_year: int, engine) -> pd.DataFrame:
    """peer들의 bsns_year·bsns_year-1 비율. 당기(bsns_year)는 모든 peer가 아직
    DART에 신고 전일 수 있으므로(감사 대상과 같은 12월 결산 가정, §3), 회사별로
    당기 값이 있으면 그것을, 없으면 전기 값을 사용하도록 뒤에서 coalesce한다."""
    if not peers:
        return pd.DataFrame(columns=["corp_code", "bsns_year", *RATIO_COLS])
    placeholders = ",".join(f":p{i}" for i in range(len(peers)))
    params = {f"p{i}": p for i, p in enumerate(peers)}
    params["y1"] = bsns_year
    params["y2"] = bsns_year - 1
    with engine.connect() as conn:
        return pd.read_sql(
            text(
                f"SELECT corp_code, bsns_year, {', '.join(RATIO_COLS)} FROM ratios "
                f"WHERE bsns_year IN (:y1, :y2) AND corp_code IN ({placeholders})"
            ),
            conn, params=params,
        )


def coalesced_peer_values(peer_ratios: pd.DataFrame, ratio: str, bsns_year: int) -> list[float]:
    """peer 회사별로 당기(bsns_year) 값이 있으면 그 값을, 없으면 전기 값을 채택."""
    if peer_ratios.empty:
        return []
    sub = peer_ratios[["corp_code", "bsns_year", ratio]].dropna(subset=[ratio])
    if sub.empty:
        return []
    sub = sub.sort_values("bsns_year", ascending=False)  # 당기가 전기보다 먼저 오도록
    picked = sub.drop_duplicates(subset=["corp_code"], keep="first")
    return picked[ratio].astype(float).tolist()


def compute_live_deviations(corp_code: str, bsns_year: int, live_accounts: dict, engine=None) -> dict:
    """당기 입력값 vs (peer 기대치+자사 전년도) 편차. 반환값은 detect_flags.py의
    compute_row_deviations()와 동일한 형태: {ratio: {"deviation", "direction"}}."""
    engine = engine or create_engine(DB_URL)

    prev_accounts = load_prev_accounts(corp_code, bsns_year, engine)
    live_ratios = compute_live_ratios(live_accounts, prev_accounts)

    with engine.connect() as conn:
        own_prior_row = pd.read_sql(
            text(f"SELECT {', '.join(RATIO_COLS)} FROM ratios WHERE corp_code = :cc AND bsns_year = :yr"),
            conn, params={"cc": corp_code, "yr": bsns_year - 1},
        )
    own_prior = own_prior_row.iloc[0].to_dict() if not own_prior_row.empty else {}

    peers = get_peer_group(corp_code, engine)["peers"]
    peer_ratios = load_peer_ratios(peers, bsns_year, engine)

    result = {}
    for ratio in RATIO_COLS:
        actual = live_ratios.get(ratio)
        if actual is None:
            continue
        bound = SANITY_BOUNDS.get(ratio)
        if bound is not None and abs(actual) > bound:
            continue

        peer_values = coalesced_peer_values(peer_ratios, ratio, bsns_year)
        dev = deviation_for_value(actual, own_prior.get(ratio), peer_values)
        if dev is not None:
            result[ratio] = dev

    return result

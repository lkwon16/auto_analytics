"""매출 성장 스크리너 — 감사 파이프라인(모듈①~⑦)과 같은 DB를 재사용하는 신규
비즈니스 라인(스핀오프)의 첫 기능. "판단"이 아니라 "조건에 맞는 기업 목록 추출"이
목적이라, 감사 모집단 제한(analysis_universe — 금융업 제외·12월 결산·CFS 3개년
이상)에 얽매이지 않고 상장사 전체(corp_master)를 대상으로 한다.

핵심 지표: revenue_growth = (당기매출 - 전기매출) / 전기매출
극단값(전기매출이 0에 가까운 초기 기업 등으로 증가율이 폭발하는 경우)은
detect_flags.py(§9, SANITY_BOUNDS)처럼 계산에서 제외하지 않고 is_extreme 플래그만
붙여 그대로 보여준다 — 스크리닝 목적에서는 걸러내는 것보다 사용자가 직접 보고
판단하는 편이 맞다는 판단.

업종 분류는 peer_group.py(모듈②)의 KSIC 대분류 로직을 그대로 재사용한다(분류
기준을 감사 모듈과 통일). 향후 세부 분류(중분류)나 규모 구간 등 추가 스크리닝
기준을 더 붙일 수 있도록 compute_revenue_growth()는 필터 없이 전체를 계산해
반환하고, 필터링은 호출부(대시보드 탭 등)에서 담당한다.
"""
import sys

import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL
from xbrl_mapping import ACCOUNT_CANDIDATES
from peer_group import get_division, get_section

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REVENUE_CANDIDATES = ACCOUNT_CANDIDATES["revenue"]

# KSIC(10차) 대분류 코드 -> 한글 이름. 구간 정의(peer_group.KSIC_SECTIONS) 자체는
# 코드에서 가져오므로 여기서는 이름만 붙인다.
KSIC_SECTION_NAMES = {
    "A": "농업·임업·어업", "B": "광업", "C": "제조업",
    "D": "전기·가스·증기·공기조절 공급업", "E": "수도·하수·폐기물처리·원료재생업",
    "F": "건설업", "G": "도매·소매업", "H": "운수·창고업", "I": "숙박·음식점업",
    "J": "정보통신업", "K": "금융·보험업", "L": "부동산업",
    "M": "전문·과학·기술서비스업", "N": "사업시설관리·사업지원·임대서비스업",
    "O": "공공행정·국방·사회보장행정", "P": "교육서비스업",
    "Q": "보건업·사회복지서비스업", "R": "예술·스포츠·여가관련서비스업",
    "S": "협회·단체·수리·개인서비스업", "T": "가구내 고용활동·자가소비 생산활동",
    "U": "국제·외국기관",
}

# 전년 매출 대비 분모 왜소화로 증가율이 폭발하는 경우의 판정 기준.
# detect_flags.py의 SANITY_BOUNDS(LIMITATIONS.md §9)와 동일값(|증가율|>10=1000%)을
# 재사용해 "무엇을 극단값으로 보는가"의 정의를 감사 모듈과 통일한다.
EXTREME_GROWTH_BOUND = 10.0

RESULT_COLUMNS = [
    "corp_code", "corp_name", "industry_code", "section", "section_name",
    "bsns_year", "revenue", "prev_revenue", "growth", "is_extreme",
]


def section_of(industry_code: str | None) -> str | None:
    return get_section(get_division(industry_code))


def compute_revenue_growth(engine=None) -> pd.DataFrame:
    """상장사 전체(corp_master.stock_code IS NOT NULL) 대상 연도별 매출액증가율.
    감사 모집단(analysis_universe) 제한을 받지 않는다 — 이 모듈의 설계 원칙(위 docstring)."""
    engine = engine or create_engine(DB_URL)

    cand_sql = " OR ".join(
        f"(sj_div = :sj{i} AND account_id = :ac{i})" for i in range(len(REVENUE_CANDIDATES))
    )
    params = {}
    for i, (sj, ac) in enumerate(REVENUE_CANDIDATES):
        params[f"sj{i}"] = sj
        params[f"ac{i}"] = ac

    with engine.connect() as conn:
        raw = pd.read_sql(
            text(
                f"SELECT corp_code, bsns_year, sj_div, account_id, thstrm_amount "
                f"FROM financial_statements WHERE fs_div = 'CFS' AND ({cand_sql})"
            ),
            conn, params=params,
        )
        corps = pd.read_sql(
            text("SELECT corp_code, corp_name, industry_code FROM corp_master WHERE stock_code IS NOT NULL"),
            conn,
        )

    if raw.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    # fallback 우선순위(ACCOUNT_CANDIDATES 순서) 첫 매치만 채택
    priority = {(sj, ac): i for i, (sj, ac) in enumerate(REVENUE_CANDIDATES)}
    raw["_priority"] = raw.apply(lambda r: priority.get((r["sj_div"], r["account_id"]), 999), axis=1)
    raw = raw.sort_values("_priority").drop_duplicates(subset=["corp_code", "bsns_year"], keep="first")
    rev_df = raw[["corp_code", "bsns_year", "thstrm_amount"]]

    records = []
    for y in sorted(rev_df["bsns_year"].unique()):
        cur_y = rev_df[rev_df["bsns_year"] == y][["corp_code", "thstrm_amount"]].rename(
            columns={"thstrm_amount": "revenue"}
        )
        prev_y = rev_df[rev_df["bsns_year"] == y - 1][["corp_code", "thstrm_amount"]].rename(
            columns={"thstrm_amount": "prev_revenue"}
        )
        merged = cur_y.merge(prev_y, on="corp_code", how="inner")
        merged = merged[merged["prev_revenue"] != 0]
        if merged.empty:
            continue
        merged["growth"] = (merged["revenue"] - merged["prev_revenue"]) / merged["prev_revenue"]
        merged["bsns_year"] = y
        records.append(merged)

    if not records:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = pd.concat(records, ignore_index=True)
    result["is_extreme"] = result["growth"].abs() > EXTREME_GROWTH_BOUND
    result = result.merge(corps, on="corp_code", how="left")
    result["section"] = result["industry_code"].apply(section_of)
    result["section_name"] = result["section"].map(KSIC_SECTION_NAMES)
    return result[RESULT_COLUMNS]


if __name__ == "__main__":
    engine = create_engine(DB_URL)
    df = compute_revenue_growth(engine)
    latest = int(df["bsns_year"].max())
    top = df[(df["bsns_year"] == latest) & (~df["is_extreme"])].sort_values("growth", ascending=False).head(10)
    print(f"{latest}년 매출 증가율 상위 10개사 (극단값 제외):")
    for r in top.itertuples():
        print(f"  {r.corp_name} ({r.section_name}): {r.growth * 100:.1f}%")

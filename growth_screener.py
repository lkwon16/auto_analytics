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

**추가 스크리닝 조건 (2026-07-21)**:
- 연속 성장(consecutive_growth_years): 당해년도까지 growth>0이 몇 개년 연속인지.
  연도 데이터에 공백이 있는 기업은 공백 이전 구간이 끊긴 것으로 취급한다(같은 corp_code
  내에서 collect_financials.py가 수집한 bsns_year가 사실상 연속적이라는 전제 — 결측
  연도가 있으면 스트릭이 그 지점에서 리셋되는 게 아니라 "그 해는 growth 자체가 없어서
  행이 없는" 형태이므로 groupby 스트릭 계산이 자연히 끊는다).
- operating_margin(영업이익률)과 margin_improved(전년대비 영업이익률 개선 여부):
  ratios 테이블(analysis_universe 한정)에 기대지 않고 xbrl_mapping의 operating_income
  후보를 revenue와 같은 방식으로 직접 추출해 계산 — 상장사 전체 대상 원칙을 유지하기
  위함.
"""
import sys

import pandas as pd
from sqlalchemy import create_engine

from config import DB_URL
from xbrl_mapping import ACCOUNT_CANDIDATES
from screening_common import fetch_account, fetch_listed_corps, with_section

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REVENUE_CANDIDATES = ACCOUNT_CANDIDATES["revenue"]
OPERATING_INCOME_CANDIDATES = ACCOUNT_CANDIDATES["operating_income"]

# 전년 매출 대비 분모 왜소화로 증가율이 폭발하는 경우의 판정 기준.
# detect_flags.py의 SANITY_BOUNDS(LIMITATIONS.md §9)와 동일값(|증가율|>10=1000%)을
# 재사용해 "무엇을 극단값으로 보는가"의 정의를 감사 모듈과 통일한다.
EXTREME_GROWTH_BOUND = 10.0

# 매출 규모 구간(단위: 원). 대시보드 필터용 — 라벨은 UI 쪽에서 붙인다.
REVENUE_SCALE_BANDS = [
    ("100억 미만", 0, 10_000_000_000),
    ("100억~500억", 10_000_000_000, 50_000_000_000),
    ("500억~1,000억", 50_000_000_000, 100_000_000_000),
    ("1,000억~5,000억", 100_000_000_000, 500_000_000_000),
    ("5,000억~1조", 500_000_000_000, 1_000_000_000_000),
    ("1조 이상", 1_000_000_000_000, float("inf")),
]

RESULT_COLUMNS = [
    "corp_code", "corp_name", "industry_code", "section", "section_name",
    "bsns_year", "revenue", "prev_revenue", "growth", "is_extreme",
    "consecutive_growth_years", "operating_margin", "prev_operating_margin",
    "margin_improved",
]


def _consecutive_growth_streak(growth: pd.Series) -> pd.Series:
    """연도순으로 정렬된 growth 시리즈에서 당해년도까지 growth>0이 몇 개년
    연속인지 계산한다 (호출부에서 corp_code별 groupby 후 연도순 정렬해 넘길 것)."""
    streak = 0
    out = []
    for g in growth:
        streak = streak + 1 if g > 0 else 0
        out.append(streak)
    return pd.Series(out, index=growth.index)


def compute_revenue_growth(engine=None) -> pd.DataFrame:
    """상장사 전체(corp_master.stock_code IS NOT NULL) 대상 연도별 매출액증가율 +
    연속 성장 개년수 + 영업이익률(전년대비 개선 여부 포함).
    감사 모집단(analysis_universe) 제한을 받지 않는다 — 이 모듈의 설계 원칙(위 docstring)."""
    engine = engine or create_engine(DB_URL)

    rev_df = fetch_account(engine, REVENUE_CANDIDATES, "revenue")
    if rev_df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    oi_df = fetch_account(engine, OPERATING_INCOME_CANDIDATES, "operating_income")
    corps = fetch_listed_corps(engine)

    records = []
    for y in sorted(rev_df["bsns_year"].unique()):
        cur_y = rev_df[rev_df["bsns_year"] == y][["corp_code", "revenue"]]
        prev_y = rev_df[rev_df["bsns_year"] == y - 1][["corp_code", "revenue"]].rename(
            columns={"revenue": "prev_revenue"}
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

    # 연속 성장 개년수: corp_code별로 연도순 정렬 후 스트릭 계산
    result = result.sort_values(["corp_code", "bsns_year"])
    result["consecutive_growth_years"] = (
        result.groupby("corp_code")["growth"].transform(_consecutive_growth_streak)
    )

    # 영업이익률 = 영업이익 / 매출 (당기·전기 모두). 매출과 별개로 결측 가능 — 없으면 NaN.
    if not oi_df.empty:
        result = result.merge(oi_df, on=["corp_code", "bsns_year"], how="left")
        result["operating_margin"] = result["operating_income"] / result["revenue"]
        prev_oi = oi_df.rename(columns={"bsns_year": "_py", "operating_income": "prev_operating_income"})
        result = result.merge(
            prev_oi, left_on=["corp_code", result["bsns_year"] - 1], right_on=["corp_code", "_py"], how="left"
        ).drop(columns=["_py"])
        result["prev_operating_margin"] = result["prev_operating_income"] / result["prev_revenue"]
        result["margin_improved"] = result["operating_margin"] > result["prev_operating_margin"]
        result = result.drop(columns=["operating_income", "prev_operating_income"])
    else:
        result["operating_margin"] = pd.NA
        result["prev_operating_margin"] = pd.NA
        result["margin_improved"] = pd.NA

    result = result.merge(corps, on="corp_code", how="left")
    result = with_section(result)
    return result[RESULT_COLUMNS]


if __name__ == "__main__":
    engine = create_engine(DB_URL)
    df = compute_revenue_growth(engine)
    latest = int(df["bsns_year"].max())
    top = df[(df["bsns_year"] == latest) & (~df["is_extreme"])].sort_values("growth", ascending=False).head(10)
    print(f"{latest}년 매출 증가율 상위 10개사 (극단값 제외):")
    for r in top.itertuples():
        margin_note = f", 영업이익률 {r.operating_margin * 100:.1f}%" if pd.notna(r.operating_margin) else ""
        print(f"  {r.corp_name} ({r.section_name}): {r.growth * 100:.1f}%, "
              f"연속성장 {r.consecutive_growth_years}년{margin_note}")

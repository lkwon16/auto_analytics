"""흑자전환 스크리너 — 스크리닝 도구(⑧)의 조건 2. growth_screener.py와 같은 설계
원칙(감사 모집단 제한 없이 상장사 전체 대상, screening_common.py 공용 헬퍼 재사용).

핵심 조건: 전기 영업손실(operating_income <= 0) -> 당기 영업이익(operating_income > 0)
전환. 투자자들이 "턴어라운드주"를 찾을 때 흔히 쓰는 조건이다.
"""
import sys

import pandas as pd
from sqlalchemy import create_engine

from config import DB_URL
from xbrl_mapping import ACCOUNT_CANDIDATES
from screening_common import fetch_account, fetch_listed_corps, with_section

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OPERATING_INCOME_CANDIDATES = ACCOUNT_CANDIDATES["operating_income"]
REVENUE_CANDIDATES = ACCOUNT_CANDIDATES["revenue"]

RESULT_COLUMNS = [
    "corp_code", "corp_name", "industry_code", "section", "section_name",
    "bsns_year", "revenue", "operating_income", "prev_operating_income",
    "operating_margin", "is_turnaround",
]


def compute_turnaround(engine=None) -> pd.DataFrame:
    """상장사 전체 대상 연도별 영업이익 흑자전환 여부.
    감사 모집단(analysis_universe) 제한을 받지 않는다."""
    engine = engine or create_engine(DB_URL)

    oi_df = fetch_account(engine, OPERATING_INCOME_CANDIDATES, "operating_income")
    if oi_df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)
    rev_df = fetch_account(engine, REVENUE_CANDIDATES, "revenue")
    corps = fetch_listed_corps(engine)

    records = []
    for y in sorted(oi_df["bsns_year"].unique()):
        cur_y = oi_df[oi_df["bsns_year"] == y][["corp_code", "operating_income"]]
        prev_y = oi_df[oi_df["bsns_year"] == y - 1][["corp_code", "operating_income"]].rename(
            columns={"operating_income": "prev_operating_income"}
        )
        merged = cur_y.merge(prev_y, on="corp_code", how="inner")
        if merged.empty:
            continue
        merged["bsns_year"] = y
        records.append(merged)

    if not records:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = pd.concat(records, ignore_index=True)
    result["is_turnaround"] = (result["prev_operating_income"] <= 0) & (result["operating_income"] > 0)

    result = result.merge(rev_df, on=["corp_code", "bsns_year"], how="left")
    result["operating_margin"] = result["operating_income"] / result["revenue"]
    result.loc[result["revenue"].isna() | (result["revenue"] == 0), "operating_margin"] = pd.NA

    result = result.merge(corps, on="corp_code", how="left")
    result = with_section(result)
    return result[RESULT_COLUMNS]


if __name__ == "__main__":
    engine = create_engine(DB_URL)
    df = compute_turnaround(engine)
    latest = int(df["bsns_year"].max())
    top = df[(df["bsns_year"] == latest) & df["is_turnaround"]].sort_values("operating_income", ascending=False)
    print(f"{latest}년 흑자전환 기업 {len(top)}개사:")
    for r in top.head(10).itertuples():
        margin_note = f", 영업이익률 {r.operating_margin * 100:.1f}%" if pd.notna(r.operating_margin) else ""
        print(f"  {r.corp_name} ({r.section_name}): 영업이익 {r.operating_income:,.0f}"
              f"(전기 {r.prev_operating_income:,.0f}){margin_note}")

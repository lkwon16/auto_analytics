"""이익의 질(현금창출력) 스크리너 — 스크리닝 도구(⑧)의 조건 4. growth_screener.py와
같은 설계 원칙(감사 모집단 제한 없이 상장사 전체 대상, screening_common.py 공용
헬퍼 재사용).

영업활동현금흐름(operating_cf)이 영업이익(operating_income)보다 큰 기업을 "이익의
질이 좋다"고 본다 — 회계상 이익은 있는데 실제 현금은 못 들어오는("흑자도산" 위험)
기업을 걸러내려는 투자자들이 자주 쓰는 조건이다. 감사 모듈의 oi_cfo_gap_ratio(모듈④,
편차 탐지용)와 원천 데이터는 같지만 목적이 다르다 — 여기서는 peer 대비 편차가 아니라
절대적으로 "현금창출력이 이익을 뒷받침하는가"만 본다.
"""
import sys

import pandas as pd
from sqlalchemy import create_engine

from config import DB_URL
from xbrl_mapping import ACCOUNT_CANDIDATES
from screening_common import fetch_account, fetch_listed_corps, with_section

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OPERATING_INCOME_CANDIDATES = ACCOUNT_CANDIDATES["operating_income"]
OPERATING_CF_CANDIDATES = ACCOUNT_CANDIDATES["operating_cf"]

RESULT_COLUMNS = [
    "corp_code", "corp_name", "industry_code", "section", "section_name",
    "bsns_year", "operating_income", "operating_cf", "cfo_oi_ratio", "is_good_quality",
]


def compute_cash_quality(engine=None) -> pd.DataFrame:
    """상장사 전체 대상 연도별 영업활동현금흐름/영업이익 배율.
    감사 모집단(analysis_universe) 제한을 받지 않는다."""
    engine = engine or create_engine(DB_URL)

    oi_df = fetch_account(engine, OPERATING_INCOME_CANDIDATES, "operating_income")
    cfo_df = fetch_account(engine, OPERATING_CF_CANDIDATES, "operating_cf")
    if oi_df.empty or cfo_df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = oi_df.merge(cfo_df, on=["corp_code", "bsns_year"], how="inner")
    corps = fetch_listed_corps(engine)

    # 영업이익이 0 이하면 배율이 의미 없어짐(부호 왜곡) — 영업이익 > 0인 경우만 배율 계산.
    result["cfo_oi_ratio"] = pd.NA
    positive_oi = result["operating_income"] > 0
    result.loc[positive_oi, "cfo_oi_ratio"] = (
        result.loc[positive_oi, "operating_cf"] / result.loc[positive_oi, "operating_income"]
    )
    result["is_good_quality"] = positive_oi & (result["operating_cf"] > result["operating_income"])

    result = result.merge(corps, on="corp_code", how="left")
    result = with_section(result)
    return result[RESULT_COLUMNS]


if __name__ == "__main__":
    engine = create_engine(DB_URL)
    df = compute_cash_quality(engine)
    latest = int(df["bsns_year"].max())
    top = df[(df["bsns_year"] == latest) & df["is_good_quality"]].sort_values("cfo_oi_ratio", ascending=False)
    print(f"{latest}년 이익의 질 우수 기업 {len(top)}개사 (상위 10개):")
    for r in top.head(10).itertuples():
        print(f"  {r.corp_name} ({r.section_name}): 영업이익 {r.operating_income:,.0f} / "
              f"영업CF {r.operating_cf:,.0f} (배율 {r.cfo_oi_ratio:.1f}배)")

"""부채비율 급변 스크리너 — 스크리닝 도구(⑧)의 조건 3. growth_screener.py와 같은
설계 원칙(감사 모집단 제한 없이 상장사 전체 대상, screening_common.py 공용 헬퍼 재사용).

부채비율(debt_ratio = 총부채/자기자본) 전년대비 변화(%p)를 계산한다. 감사 모듈
(compute_ratios.py)의 debt_ratio와 같은 정의를 쓰지만, 목적은 peer 대비 이상 편차
탐지가 아니라 "최근 재무건전성이 뚜렷하게 개선/악화된 기업"을 직접 찾는 것이다.
"""
import sys

import pandas as pd
from sqlalchemy import create_engine

from config import DB_URL
from xbrl_mapping import ACCOUNT_CANDIDATES
from screening_common import fetch_account, fetch_listed_corps, with_section

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

LIABILITIES_CANDIDATES = ACCOUNT_CANDIDATES["total_liabilities"]
EQUITY_CANDIDATES = ACCOUNT_CANDIDATES["total_equity"]

# 자기자본이 0에 가까운(자본잠식 직전 등) 경우 부채비율이 부호는 정상이어도 크기가
# 비현실적으로 폭발할 수 있다 — growth_screener.py의 EXTREME_GROWTH_BOUND와 같은
# 발상으로 절대값 기준을 하나 더 둔다(|부채비율| > 2000%).
EXTREME_DEBT_RATIO_BOUND = 20.0

RESULT_COLUMNS = [
    "corp_code", "corp_name", "industry_code", "section", "section_name",
    "bsns_year", "debt_ratio", "prev_debt_ratio", "debt_ratio_change_pp", "is_extreme",
]


def compute_debt_ratio_change(engine=None) -> pd.DataFrame:
    """상장사 전체 대상 연도별 부채비율(총부채/자기자본)과 전년대비 변화(%p).
    감사 모집단(analysis_universe) 제한을 받지 않는다."""
    engine = engine or create_engine(DB_URL)

    liab_df = fetch_account(engine, LIABILITIES_CANDIDATES, "total_liabilities")
    equity_df = fetch_account(engine, EQUITY_CANDIDATES, "total_equity")
    if liab_df.empty or equity_df.empty:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    bs = liab_df.merge(equity_df, on=["corp_code", "bsns_year"], how="inner")
    bs = bs[bs["total_equity"] != 0]
    bs["debt_ratio"] = bs["total_liabilities"] / bs["total_equity"]
    corps = fetch_listed_corps(engine)

    records = []
    for y in sorted(bs["bsns_year"].unique()):
        cur_y = bs[bs["bsns_year"] == y][["corp_code", "debt_ratio", "total_equity"]]
        prev_y = bs[bs["bsns_year"] == y - 1][["corp_code", "debt_ratio", "total_equity"]].rename(
            columns={"debt_ratio": "prev_debt_ratio", "total_equity": "prev_total_equity"}
        )
        merged = cur_y.merge(prev_y, on="corp_code", how="inner")
        if merged.empty:
            continue
        merged["bsns_year"] = y
        records.append(merged)

    if not records:
        return pd.DataFrame(columns=RESULT_COLUMNS)

    result = pd.concat(records, ignore_index=True)
    # %p 단위(비율 차이 * 100)로 변화폭을 표현 — 부채비율은 보통 %로 표기하는 관행 반영
    result["debt_ratio_change_pp"] = (result["debt_ratio"] - result["prev_debt_ratio"]) * 100
    # 자기자본이 음수(자본잠식)면 부채비율 부호가 뒤집혀 %가 직관과 반대로 읽힌다.
    # growth_screener.py의 is_extreme과 같은 방침 — 걸러내지 않고 플래그만 표시
    # (자본잠식 자체는 재무건전성 악화 신호로서 오히려 중요한 정보이므로).
    result["is_extreme"] = (
        (result["total_equity"] <= 0) | (result["prev_total_equity"] <= 0)
        | (result["debt_ratio"].abs() > EXTREME_DEBT_RATIO_BOUND)
        | (result["prev_debt_ratio"].abs() > EXTREME_DEBT_RATIO_BOUND)
    )
    result = result.drop(columns=["total_equity", "prev_total_equity"])

    result = result.merge(corps, on="corp_code", how="left")
    result = with_section(result)
    return result[RESULT_COLUMNS]


if __name__ == "__main__":
    engine = create_engine(DB_URL)
    df = compute_debt_ratio_change(engine)
    latest = int(df["bsns_year"].max())
    cur = df[(df["bsns_year"] == latest) & (~df["is_extreme"])]
    improved = cur.sort_values("debt_ratio_change_pp").head(5)
    worsened = cur.sort_values("debt_ratio_change_pp", ascending=False).head(5)
    print(f"{latest}년 부채비율 개선 상위 5개사 (자본잠식 등 극단값 제외):")
    for r in improved.itertuples():
        print(f"  {r.corp_name} ({r.section_name}): {r.prev_debt_ratio * 100:.1f}% -> "
              f"{r.debt_ratio * 100:.1f}% ({r.debt_ratio_change_pp:+.1f}%p)")
    print(f"\n{latest}년 부채비율 악화 상위 5개사 (자본잠식 등 극단값 제외):")
    for r in worsened.itertuples():
        print(f"  {r.corp_name} ({r.section_name}): {r.prev_debt_ratio * 100:.1f}% -> "
              f"{r.debt_ratio * 100:.1f}% ({r.debt_ratio_change_pp:+.1f}%p)")

"""§17 파일럿: 정정 전 원본 XBRL 재구성 (look-ahead bias 실측 검증)

대상: backtest_labels에서 is_flagged=1 AND is_restated_next_year=1인 (corp_code, bsns_year)
      — 플래그됐고 실제로 다음 해 재무 관련 정정이 발생한 97건. look-ahead 가설을
      직접 검증할 수 있는 유일한 대상군.

STEP 1 (이 파일): 각 (corp_code, bsns_year)에 대해 disclosures에서 원본(정정 전)
사업보고서 rcept_no를 찾는다. report_nm이 "사업보고서 (YYYY.12)" 형태로 사업연도를
그대로 담고 있어 정확 매칭 가능함을 확인함(정정본은 "[기재정정]사업보고서 ..."로
접두사가 붙어 자동으로 제외됨).

실행: py pilot_original_xbrl.py
"""
import sys

import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        targets = pd.read_sql(
            text(
                "SELECT corp_code, bsns_year FROM backtest_labels "
                "WHERE is_flagged = 1 AND is_restated_next_year = 1"
            ),
            conn,
        )
        disclosures = pd.read_sql(
            text("SELECT corp_code, report_nm, rcept_no, rcept_dt FROM disclosures"), conn
        )

    print(f"대상: {len(targets)}건")

    targets["report_nm_expected"] = "사업보고서 (" + targets["bsns_year"].astype(str) + ".12)"

    merged = targets.merge(
        disclosures,
        left_on=["corp_code", "report_nm_expected"],
        right_on=["corp_code", "report_nm"],
        how="left",
    )

    matched = merged[merged["rcept_no"].notna()].copy()
    unmatched = merged[merged["rcept_no"].isna()].copy()

    print(f"매칭 성공: {len(matched)}건")
    print(f"매칭 실패: {len(unmatched)}건")
    if not unmatched.empty:
        print("실패 목록:")
        print(unmatched[["corp_code", "bsns_year"]].to_string(index=False))

    dup = matched.groupby(["corp_code", "bsns_year"]).size()
    dup = dup[dup > 1]
    if not dup.empty:
        print(f"\n경고: 중복 매칭 {len(dup)}건 (동일 corp_code+bsns_year에 원본이 2개 이상 잡힘)")
        print(dup)

    out = matched[["corp_code", "bsns_year", "rcept_no", "rcept_dt"]].drop_duplicates(
        subset=["corp_code", "bsns_year"]
    )
    out.to_csv("pilot_original_rcept_no.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: pilot_original_rcept_no.csv ({len(out)}건)")


if __name__ == "__main__":
    main()

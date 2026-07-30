"""§17 파일럿: 정정 전 원본 XBRL 재구성 (look-ahead bias 실측 검증)

대상: backtest_labels에서 is_flagged=1 AND is_restated_next_year=1인 (corp_code, bsns_year)
      — 플래그됐고 실제로 다음 해 재무 관련 정정이 발생한 97건. look-ahead 가설을
      직접 검증할 수 있는 유일한 대상군.

STEP 1 (이 파일): 각 (corp_code, bsns_year)에 대해 disclosures에서 원본(정정 전)
사업보고서 rcept_no를 찾는다. report_nm이 "사업보고서 (YYYY.12)" 형태로 사업연도를
그대로 담고 있어 정확 매칭 가능함을 확인함(정정본은 "[기재정정]사업보고서 ..."로
접두사가 붙어 자동으로 제외됨).

**보강(1차 실행 후 재확인 중 발견)**: 정확 매칭 97건 중 2건이 실패했는데, 두 회사
모두 사업보고서제출기한연장 이후 최초 제출본이 평범한 "사업보고서 (YYYY.12)"가
아니라 "[첨부추가]사업보고서 (YYYY.12)"였다 — 정정([기재정정])은 아니지만 접두사가
붙어 정확 매칭에서 빠졌던 것. "사업보고서제출기한연장신고서 (YYYY.12)"(재무 내용
없는 신고서)까지 잘못 잡히지 않도록, "…사업보고서 (YYYY.12)"로 끝나되 "[기재정정]"은
아닌 것만 fallback으로 허용하고 가장 이른 rcept_dt를 채택한다.

실행: py pilot_original_xbrl.py
"""
import re
import sys

import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL

FALLBACK_SUFFIX_RE = re.compile(r"(^|\])사업보고서 \(\d{4}\.12\)$")

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

    print(f"정확 매칭 성공: {len(matched)}건")
    print(f"정확 매칭 실패: {len(unmatched)}건 → fallback(접두사 붙은 사업보고서, [기재정정] 제외) 시도")

    fallback_rows = []
    still_unmatched = []
    for _, row in unmatched.iterrows():
        cand = disclosures[
            (disclosures["corp_code"] == row["corp_code"])
            & disclosures["report_nm"].apply(
                lambda s: bool(FALLBACK_SUFFIX_RE.search(s)) and "[기재정정]" not in s
            )
            & disclosures["report_nm"].str.contains(f"({row['bsns_year']}.12)", regex=False)
        ].sort_values("rcept_dt")
        if cand.empty:
            still_unmatched.append((row["corp_code"], row["bsns_year"]))
        else:
            picked = cand.iloc[0]
            fallback_rows.append({
                "corp_code": row["corp_code"], "bsns_year": row["bsns_year"],
                "rcept_no": picked["rcept_no"], "rcept_dt": picked["rcept_dt"],
                "report_nm": picked["report_nm"],
            })
            print(f"  fallback 매칭: {row['corp_code']} {row['bsns_year']} -> "
                  f"\"{picked['report_nm']}\" ({picked['rcept_dt']})")

    if still_unmatched:
        print(f"\nfallback으로도 실패: {len(still_unmatched)}건")
        for cc, yr in still_unmatched:
            print(f"  {cc} {yr}")

    dup = matched.groupby(["corp_code", "bsns_year"]).size()
    dup = dup[dup > 1]
    if not dup.empty:
        print(f"\n경고: 중복 매칭 {len(dup)}건 (동일 corp_code+bsns_year에 원본이 2개 이상 잡힘)")
        print(dup)

    out = matched[["corp_code", "bsns_year", "rcept_no", "rcept_dt"]].drop_duplicates(
        subset=["corp_code", "bsns_year"]
    )
    if fallback_rows:
        fb_df = pd.DataFrame(fallback_rows)[["corp_code", "bsns_year", "rcept_no", "rcept_dt"]]
        out = pd.concat([out, fb_df], ignore_index=True)
    out.to_csv("pilot_original_rcept_no.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: pilot_original_rcept_no.csv ({len(out)}건, 정확매칭 {len(matched)} + fallback {len(fallback_rows)})")


if __name__ == "__main__":
    main()

"""미니 백테스트 (모듈⑥, STEP 4)

라벨 정제: `disclosures`의 `[기재정정]` 중 사업보고서 정정만 사용한다 (반기·분기보고서
정정은 제외 — 반기는 검토, 분기는 검토조차 없어 감사의견과 무관한 경미한 정정이 대부분,
LIMITATIONS.md §5). "감사보고서" 단독 정정 공시는 데이터에 존재하지 않음(확인 완료) —
사업보고서 정정이 감사 관련 정정의 사실상 유일한 창구.

라벨 = "N년 플래그 → N+1년(캘린더 연도)에 그 기업의 사업보고서 정정이 실제 접수됐는가"
(LIMITATIONS.md §6 정정 후 데이터 순환 문제를 "플래그 다음 해 정정 발생 여부"로 우회)
평가 = flags.is_flagged=1 그룹의 Precision@10% 대 전체 기준율 대비 lift

실행: python backtest.py
"""
import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL

# 사업보고서 정정만 포함, "사업보고서제출기한연장신고서"(내용 정정 아님) 제외
RESTATEMENT_PATTERN = "[기재정정]사업보고서%"
EXCLUDE_PATTERN = "%제출기한연장%"

DDL = """
CREATE TABLE IF NOT EXISTS backtest_labels (
    corp_code              VARCHAR(8) NOT NULL,
    bsns_year               INT NOT NULL,
    is_flagged              INT NOT NULL,
    is_restated_next_year   INT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year)
);
"""


def main():
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text(DDL))

    with engine.connect() as conn:
        flags = pd.read_sql(text("SELECT corp_code, bsns_year, is_flagged FROM flags"), conn)
        restatements = pd.read_sql(
            text(
                "SELECT corp_code, rcept_dt FROM disclosures "
                "WHERE report_nm LIKE :p AND report_nm NOT LIKE :e"
            ),
            conn, params={"p": RESTATEMENT_PATTERN, "e": EXCLUDE_PATTERN},
        )

    restatements["rcept_year"] = pd.to_datetime(restatements["rcept_dt"]).dt.year
    restated_years = restatements.groupby("corp_code")["rcept_year"].apply(set).to_dict()

    flags["is_restated_next_year"] = flags.apply(
        lambda r: int((r["bsns_year"] + 1) in restated_years.get(r["corp_code"], set())),
        axis=1,
    )

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM backtest_labels"))
        flags[["corp_code", "bsns_year", "is_flagged", "is_restated_next_year"]].to_sql(
            "backtest_labels", conn, if_exists="append", index=False
        )

    base_rate = flags["is_restated_next_year"].mean()
    flagged = flags[flags["is_flagged"] == 1]
    precision_at_10 = flagged["is_restated_next_year"].mean()
    lift = precision_at_10 / base_rate if base_rate > 0 else float("nan")

    n_total = len(flags)
    n_flagged = len(flagged)
    n_restated_total = int(flags["is_restated_next_year"].sum())
    n_restated_flagged = int(flagged["is_restated_next_year"].sum())

    print(f"전체 기업x연도: {n_total:,}건 (다음해 정정 발생: {n_restated_total:,}건, 기준율 {base_rate * 100:.2f}%)")
    print(f"플래그(is_flagged=1): {n_flagged:,}건 (다음해 정정 발생: {n_restated_flagged:,}건)")
    print(f"Precision@10%: {precision_at_10 * 100:.2f}%")
    print(f"Lift: {lift:.2f}배 (무작위 대비)")


if __name__ == "__main__":
    main()

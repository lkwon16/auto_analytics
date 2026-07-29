"""미니 백테스트 (모듈⑥, STEP 4)

라벨 정제: `disclosures`의 `[기재정정]` 중 사업보고서 정정만 사용한다 (반기·분기보고서
정정은 제외 — 반기는 검토, 분기는 검토조차 없어 감사의견과 무관한 경미한 정정이 대부분,
LIMITATIONS.md §5). "감사보고서" 단독 정정 공시는 데이터에 존재하지 않음(확인 완료) —
사업보고서 정정이 감사 관련 정정의 사실상 유일한 창구.

`correction_details.is_financial_related`(`collect_correction_reasons.py`가 정정 원문의
CORRECTION 섹션을 파싱해 판정, LIMITATIONS.md §5)로 재무제표·감사의견에 실질적 영향을
주는 정정만 라벨에 반영한다 — report_nm 패턴만으로는 오타·경미한 서식 정정과 구분이
안 되던 문제를 해소.

라벨 = "N년 플래그 → N+1년(캘린더 연도)에 그 기업의 재무 관련 사업보고서 정정이 실제
접수됐는가" (LIMITATIONS.md §6 정정 후 데이터 순환 문제를 "플래그 다음 해 정정 발생
여부"로 우회)
평가 = flags.is_flagged=1 그룹의 Precision@10% 대 전체 기준율 대비 lift

실행: python backtest.py
"""
import math
import sys

import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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


def _norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def wald_ci(phat: float, n: int, z: float = 1.96) -> tuple:
    """단순 Wald 신뢰구간. n이 수백 이상이면 Wilson 구간과 큰 차이 없음(여기선 898건)."""
    if n == 0:
        return float("nan"), float("nan")
    margin = z * math.sqrt(phat * (1 - phat) / n)
    return max(0.0, phat - margin), min(1.0, phat + margin)


def one_sided_p_vs_base_rate(phat: float, base_rate: float, n: int) -> float:
    """귀무가설: 플래그 그룹의 정정 발생률 = 전체 기준율(base_rate).
    대립가설: 플래그 그룹이 더 높다(lift > 1). one-proportion z-test, 단측검정."""
    if n == 0 or base_rate in (0, 1):
        return float("nan")
    se = math.sqrt(base_rate * (1 - base_rate) / n)
    z = (phat - base_rate) / se
    return 1 - _norm_cdf(z), z


def main():
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text(DDL))

    with engine.connect() as conn:
        flags = pd.read_sql(text("SELECT corp_code, bsns_year, is_flagged FROM flags"), conn)
        restatements = pd.read_sql(
            text(
                "SELECT d.corp_code, d.rcept_dt FROM disclosures d "
                "JOIN correction_details c ON c.rcept_no = d.rcept_no "
                "WHERE d.report_nm LIKE :p AND d.report_nm NOT LIKE :e "
                "AND c.is_financial_related = 1"
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

    ci_lo, ci_hi = wald_ci(precision_at_10, n_flagged)
    lift_lo = ci_lo / base_rate if base_rate > 0 else float("nan")
    lift_hi = ci_hi / base_rate if base_rate > 0 else float("nan")
    p_value, z = one_sided_p_vs_base_rate(precision_at_10, base_rate, n_flagged)

    print(f"전체 기업x연도: {n_total:,}건 (다음해 정정 발생: {n_restated_total:,}건, 기준율 {base_rate * 100:.2f}%)")
    print(f"플래그(is_flagged=1): {n_flagged:,}건 (다음해 정정 발생: {n_restated_flagged:,}건)")
    print(f"Precision@10%: {precision_at_10 * 100:.2f}% (95% CI: [{ci_lo * 100:.2f}%, {ci_hi * 100:.2f}%])")
    print(f"Lift: {lift:.2f}배 (95% CI: [{lift_lo:.2f}, {lift_hi:.2f}])")
    print(f"기준율 대비 z = {z:.2f}, 단측 p = {p_value:.3f} "
          f"(귀무가설: 플래그 그룹 정정 발생률 = 전체 기준율, 대립가설: 플래그 그룹이 더 높다)")
    if p_value >= 0.05:
        print("  -> p >= 0.05: 이 Lift는 무작위(귀무가설)와 통계적으로 구분되지 않음")


if __name__ == "__main__":
    main()

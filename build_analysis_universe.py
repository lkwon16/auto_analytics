"""분석 모집단(analysis_universe) 정의 — STEP 1
기준: 금융업(KSIC 64~66, 지주회사 64992 포함) 제외 + 12월 결산 + CFS 3개년 이상
실행: python build_analysis_universe.py
"""
from sqlalchemy import create_engine, text

from config import DB_URL

DDL = """
CREATE TABLE IF NOT EXISTS analysis_universe (
    corp_code     VARCHAR(8) PRIMARY KEY,
    corp_name     VARCHAR(200) NOT NULL,
    stock_code    VARCHAR(6),
    industry_code VARCHAR(10),
    n_years       INT NOT NULL
);
"""

# 금융업 제외: KSIC 대분류 64(금융업)·65(보험업)·66(금융지원서비스업).
# 64992(회사본부 및 지주회사)도 64로 시작해 함께 제외됨 (지주회사 peer 왜곡 방지).
QUERY = """
SELECT cm.corp_code, cm.corp_name, cm.stock_code, cm.industry_code,
       COUNT(DISTINCT fs.bsns_year) AS n_years
FROM corp_master cm
JOIN financial_statements fs
  ON fs.corp_code = cm.corp_code AND fs.fs_div = 'CFS'
WHERE cm.acc_mt = '12'
  AND substr(cm.industry_code, 1, 2) NOT IN ('64', '65', '66')
GROUP BY cm.corp_code, cm.corp_name, cm.stock_code, cm.industry_code
HAVING COUNT(DISTINCT fs.bsns_year) >= 3
"""


def main():
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        for stmt in DDL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
        conn.execute(text("DELETE FROM analysis_universe"))

        rows = conn.execute(text(QUERY)).fetchall()
        for r in rows:
            conn.execute(
                text(
                    "INSERT INTO analysis_universe "
                    "(corp_code, corp_name, stock_code, industry_code, n_years) "
                    "VALUES (:corp_code, :corp_name, :stock_code, :industry_code, :n_years)"
                ),
                dict(r._mapping),
            )

    # 제외 사유별 집계 (검증용)
    with engine.connect() as conn:
        total = conn.execute(text("SELECT COUNT(*) FROM corp_master")).scalar()
        not_dec = conn.execute(text(
            "SELECT COUNT(*) FROM corp_master WHERE acc_mt != '12'"
        )).scalar()
        financial = conn.execute(text(
            "SELECT COUNT(*) FROM corp_master WHERE substr(industry_code,1,2) IN ('64','65','66')"
        )).scalar()
        n_universe = conn.execute(text("SELECT COUNT(*) FROM analysis_universe")).scalar()
        dup_check = conn.execute(text(
            "SELECT COUNT(*) FROM (SELECT corp_code FROM analysis_universe "
            "GROUP BY corp_code HAVING COUNT(*) > 1)"
        )).scalar()

    print(f"corp_master 총 {total:,}개")
    print(f"  - 12월 결산 아님 제외: {not_dec:,}개")
    print(f"  - 금융업(64~66, 지주회사 포함) 제외: {financial:,}개")
    print(f"  - (CFS 3개년 미만은 위 두 필터 통과 기업 중 추가로 제외됨)")
    print(f"analysis_universe 최종 {n_universe:,}개 (중복 corp_code: {dup_check}건)")


if __name__ == "__main__":
    main()

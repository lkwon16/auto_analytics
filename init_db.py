"""DB 테이블 생성 스크립트 (SQLite / PostgreSQL 겸용)
실행: python init_db.py
"""
from sqlalchemy import create_engine, text

from config import DB_URL

DDL = """
CREATE TABLE IF NOT EXISTS corp_master (
    corp_code   VARCHAR(8) PRIMARY KEY,
    corp_name   VARCHAR(200) NOT NULL,
    stock_code  VARCHAR(6),
    industry_code VARCHAR(10),
    acc_mt      VARCHAR(2)
);

CREATE TABLE IF NOT EXISTS financial_statements (
    id          INTEGER PRIMARY KEY,
    corp_code   VARCHAR(8),
    bsns_year   INT NOT NULL,
    reprt_code  VARCHAR(5) NOT NULL,
    fs_div      VARCHAR(3) NOT NULL,
    sj_div      VARCHAR(5),
    account_id  VARCHAR(200),
    account_nm  VARCHAR(300),
    thstrm_amount  NUMERIC,
    frmtrm_amount  NUMERIC,
    ord         INT
);

CREATE TABLE IF NOT EXISTS disclosures (
    rcept_no    VARCHAR(14) PRIMARY KEY,
    corp_code   VARCHAR(8),
    corp_name   VARCHAR(200),
    report_nm   VARCHAR(300),
    rcept_dt    DATE
);

CREATE TABLE IF NOT EXISTS analysis_universe (
    corp_code     VARCHAR(8) PRIMARY KEY,
    corp_name     VARCHAR(200) NOT NULL,
    stock_code    VARCHAR(6),
    industry_code VARCHAR(10),
    n_years       INT NOT NULL
);

CREATE TABLE IF NOT EXISTS ratios (
    corp_code               VARCHAR(8) NOT NULL,
    bsns_year               INT NOT NULL,
    receivables_turnover    NUMERIC,
    inventory_turnover      NUMERIC,
    gp_margin               NUMERIC,
    operating_margin        NUMERIC,
    sga_ratio               NUMERIC,
    debt_ratio              NUMERIC,
    interest_coverage       NUMERIC,
    total_accruals_ratio    NUMERIC,
    oi_cfo_gap_ratio        NUMERIC,
    revenue_growth          NUMERIC,
    n_ratios_computed       INT NOT NULL,
    PRIMARY KEY (corp_code, bsns_year)
);

CREATE INDEX IF NOT EXISTS idx_fs_corp_year ON financial_statements (corp_code, bsns_year);
CREATE INDEX IF NOT EXISTS idx_disc_corp ON disclosures (corp_code, rcept_dt);
"""


def main():
    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        for stmt in DDL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))
    print(f"DB 초기화 완료: {DB_URL}")


if __name__ == "__main__":
    main()

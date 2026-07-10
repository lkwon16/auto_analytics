"""재무비율 10개 계산 → ratios 테이블 (모듈③)
analysis_universe의 모든 기업 × 보유 연도에 대해 계산한다.
XBRL 계정 매핑은 xbrl_mapping.ACCOUNT_CANDIDATES의 fallback 순서를 따른다.
실행: python compute_ratios.py
"""
import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL
from xbrl_mapping import ACCOUNT_CANDIDATES

FIELDS = list(ACCOUNT_CANDIDATES.keys())


def load_fields(engine, corp_codes: list[str]) -> pd.DataFrame:
    """analysis_universe 기업들의 원천 계정값을 (corp_code, bsns_year, field)별로
    fallback 우선순위 첫 매치만 남겨 하나의 wide 테이블로 만든다."""
    placeholders = ",".join(f":c{i}" for i in range(len(corp_codes)))
    params = {f"c{i}": cc for i, cc in enumerate(corp_codes)}

    with engine.connect() as conn:
        raw = pd.read_sql(
            text(
                f"SELECT corp_code, bsns_year, sj_div, account_id, thstrm_amount "
                f"FROM financial_statements WHERE corp_code IN ({placeholders}) "
                f"AND fs_div = 'CFS'"
            ),
            conn, params=params,
        )

    result = {}  # (corp_code, bsns_year) -> {field: value}
    for field, candidates in ACCOUNT_CANDIDATES.items():
        for sj_div, account_id in candidates:
            matched = raw[(raw["sj_div"] == sj_div) & (raw["account_id"] == account_id)]
            for _, row in matched.iterrows():
                key = (row["corp_code"], row["bsns_year"])
                result.setdefault(key, {})
                if field not in result[key]:   # 이미 상위 우선순위 후보로 채워졌으면 skip
                    result[key][field] = row["thstrm_amount"]

    records = []
    for (corp_code, bsns_year), values in result.items():
        rec = {"corp_code": corp_code, "bsns_year": bsns_year}
        rec.update({f: values.get(f) for f in FIELDS})
        records.append(rec)
    return pd.DataFrame(records)


def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def compute_row(df: pd.DataFrame, corp_code: str, year: int) -> dict | None:
    cur = df[(df["corp_code"] == corp_code) & (df["bsns_year"] == year)]
    if cur.empty:
        return None
    cur = cur.iloc[0].to_dict()

    prev = df[(df["corp_code"] == corp_code) & (df["bsns_year"] == year - 1)]
    prev = prev.iloc[0].to_dict() if not prev.empty else {}

    def avg_balance(field):
        this_v, prev_v = cur.get(field), prev.get(field)
        if this_v is None:
            return None
        if prev_v is None:
            return this_v
        return (this_v + prev_v) / 2

    revenue = cur.get("revenue")
    cogs = cur.get("cogs")
    operating_income = cur.get("operating_income")
    sga = cur.get("sga")
    interest_expense = cur.get("interest_expense")
    net_income = cur.get("net_income")
    total_assets = cur.get("total_assets")
    total_liabilities = cur.get("total_liabilities")
    total_equity = cur.get("total_equity")
    operating_cf = cur.get("operating_cf")
    prev_revenue = prev.get("revenue")

    ratios = {
        "receivables_turnover": safe_div(revenue, avg_balance("trade_receivables")),
        "inventory_turnover": safe_div(cogs, avg_balance("inventory")),
        "gp_margin": safe_div(
            (revenue - cogs) if revenue is not None and cogs is not None else None, revenue
        ),
        "operating_margin": safe_div(operating_income, revenue),
        "sga_ratio": safe_div(sga, revenue),
        "debt_ratio": safe_div(total_liabilities, total_equity),
        "interest_coverage": safe_div(operating_income, interest_expense),
        "total_accruals_ratio": safe_div(
            (net_income - operating_cf) if net_income is not None and operating_cf is not None else None,
            total_assets,
        ),
        "oi_cfo_gap_ratio": safe_div(
            (operating_income - operating_cf) if operating_income is not None and operating_cf is not None else None,
            total_assets,
        ),
        "revenue_growth": safe_div(
            (revenue - prev_revenue) if revenue is not None and prev_revenue is not None else None,
            prev_revenue,
        ),
    }
    ratios["n_ratios_computed"] = sum(1 for v in ratios.values() if v is not None)
    ratios["corp_code"] = corp_code
    ratios["bsns_year"] = year
    return ratios


def main():
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        universe = pd.read_sql(text("SELECT corp_code FROM analysis_universe"), conn)
        years = pd.read_sql(
            text(
                "SELECT DISTINCT corp_code, bsns_year FROM financial_statements "
                "WHERE fs_div = 'CFS'"
            ),
            conn,
        )

    corp_codes = universe["corp_code"].tolist()
    field_df = load_fields(engine, corp_codes)

    targets = years[years["corp_code"].isin(corp_codes)][["corp_code", "bsns_year"]]

    records = []
    for _, r in targets.iterrows():
        row = compute_row(field_df, r["corp_code"], int(r["bsns_year"]))
        if row is not None:
            records.append(row)

    result_df = pd.DataFrame(records)

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ratios"))
        result_df.to_sql("ratios", conn, if_exists="append", index=False)

    # 매핑 커버리지 리포트
    total_rows = len(result_df)
    print(f"ratios 테이블 생성 완료: {total_rows:,}개 (기업×연도)")
    print(f"평균 비율 계산 성공 개수: {result_df['n_ratios_computed'].mean():.2f} / 10")

    ratio_cols = [c for c in result_df.columns if c not in ("corp_code", "bsns_year", "n_ratios_computed")]
    print("\n비율별 매핑 커버리지:")
    for col in ratio_cols:
        coverage = result_df[col].notna().mean() * 100
        mark = "OK" if coverage >= 85 else "!!"
        print(f"  [{mark}] {col}: {coverage:.1f}%")

    full_coverage = (result_df["n_ratios_computed"] == 10).mean() * 100
    print(f"\n10개 비율 전부 계산된 기업×연도 비율: {full_coverage:.1f}%")


if __name__ == "__main__":
    main()

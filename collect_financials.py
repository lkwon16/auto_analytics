"""상장사 재무제표(전체 계정) 수집
API: fnlttSinglAcntAll (단일회사 전체 재무제표)
실행: python collect_financials.py            # 전체 수집
      python collect_financials.py --test    # 10개 기업만 테스트 수집
주의: 일 20,000건 요청 제한 → 중간에 끊겨도 재실행하면 이어서 수집됨
"""
import sys
import time

import pandas as pd
import requests
from sqlalchemy import create_engine, text
from tqdm import tqdm

from config import DART_API_KEY, DB_URL, BASE_URL, TARGET_YEARS, REPORT_CODE, FS_DIV

NUMERIC_COLS = ["thstrm_amount", "frmtrm_amount"]


def to_numeric(v):
    """'1,234,567' → 1234567.0 / 빈값·'-' → None"""
    if v is None:
        return None
    v = str(v).replace(",", "").strip()
    if v in ("", "-"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def fetch_fs(corp_code: str, year: int) -> pd.DataFrame | None:
    url = f"{BASE_URL}/fnlttSinglAcntAll.json"
    params = {
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": REPORT_CODE,
        "fs_div": FS_DIV,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status == "020":
        raise SystemExit("일일 API 요청 한도(20,000건) 초과. 내일 재실행하면 이어서 수집됩니다.")
    if status != "000":   # 013 = 조회 데이터 없음
        return None

    df = pd.DataFrame(data["list"])
    keep = ["sj_div", "account_id", "account_nm", "thstrm_amount", "frmtrm_amount", "ord"]
    df = df[[c for c in keep if c in df.columns]].copy()
    for col in NUMERIC_COLS:
        if col in df.columns:
            df[col] = df[col].map(to_numeric)
    df["corp_code"] = corp_code
    df["bsns_year"] = year
    df["reprt_code"] = REPORT_CODE
    df["fs_div"] = FS_DIV
    return df


def main():
    if not DART_API_KEY:
        raise SystemExit(".env 파일에 DART_API_KEY를 설정하세요.")

    test_mode = "--test" in sys.argv
    engine = create_engine(DB_URL)

    with engine.connect() as conn:
        corps = pd.read_sql(
            text("SELECT corp_code FROM corp_master WHERE stock_code IS NOT NULL"), conn
        )
    if corps.empty:
        raise SystemExit("corp_master가 비어 있습니다. 먼저 collect_corp_codes.py를 실행하세요.")

    if test_mode:
        corps = corps.head(10)
        print("테스트 모드: 10개 기업만 수집")

    for year in TARGET_YEARS:
        with engine.connect() as conn:
            done = pd.read_sql(text(
                "SELECT DISTINCT corp_code FROM financial_statements WHERE bsns_year = :y"
            ), conn, params={"y": year})
        todo = corps[~corps["corp_code"].isin(done["corp_code"])]

        print(f"[{year}] 대상 {len(todo):,}개")
        buffer = []
        for corp_code in tqdm(todo["corp_code"], desc=f"{year}"):
            try:
                df = fetch_fs(corp_code, year)
                if df is not None:
                    buffer.append(df)
            except requests.RequestException as e:
                print(f"  실패 {corp_code}: {e}")
            time.sleep(0.1)

            if len(buffer) >= 100:
                pd.concat(buffer).to_sql(
                    "financial_statements", engine,
                    if_exists="append", index=False, chunksize=1000,
                )
                buffer = []

        if buffer:
            pd.concat(buffer).to_sql(
                "financial_statements", engine,
                if_exists="append", index=False, chunksize=1000,
            )
    print("재무제표 수집 완료")


if __name__ == "__main__":
    main()

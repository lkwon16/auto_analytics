"""상장사 업종코드(industry_code)·결산월(acc_mt) 수집 → corp_master 업데이트
API: company.json (기업개요) — corpCode.xml에는 두 값이 없어 회사별로 조회 필요
실행: python collect_industry_codes.py
주의: 일 20,000건 요청 제한 → 중간에 끊겨도 재실행하면 이어서 수집됨
"""
import time

import requests
from sqlalchemy import create_engine, text
from tqdm import tqdm

from config import DART_API_KEY, DB_URL, BASE_URL


def fetch_company_info(corp_code: str) -> tuple[str | None, str | None]:
    url = f"{BASE_URL}/company.json"
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    status = data.get("status")
    if status == "020":
        raise SystemExit("일일 API 요청 한도(20,000건) 초과. 내일 재실행하면 이어서 수집됩니다.")
    if status != "000":   # 013 = 조회 데이터 없음
        return None, None

    induty_code = (data.get("induty_code") or "").strip() or None
    acc_mt = (data.get("acc_mt") or "").strip() or None
    return induty_code, acc_mt


def main():
    if not DART_API_KEY:
        raise SystemExit(".env 파일에 DART_API_KEY를 설정하세요.")

    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        corps = conn.execute(text(
            "SELECT corp_code FROM corp_master WHERE industry_code IS NULL OR acc_mt IS NULL"
        )).fetchall()

    if not corps:
        print("업종코드·결산월이 없는 기업이 없습니다. (이미 전부 수집됨)")
        return

    print(f"대상 {len(corps):,}개")
    updated = 0
    with engine.connect() as conn:
        for (corp_code,) in tqdm(corps, desc="company_info"):
            try:
                induty_code, acc_mt = fetch_company_info(corp_code)
            except requests.RequestException as e:
                print(f"  실패 {corp_code}: {e}")
                continue

            if induty_code or acc_mt:
                with conn.begin():
                    conn.execute(
                        text(
                            "UPDATE corp_master SET "
                            "industry_code = COALESCE(:ic, industry_code), "
                            "acc_mt = COALESCE(:am, acc_mt) "
                            "WHERE corp_code = :cc"
                        ),
                        {"ic": induty_code, "am": acc_mt, "cc": corp_code},
                    )
                updated += 1
            time.sleep(0.1)

    print(f"업종코드·결산월 수집 완료 - {updated:,}건 업데이트")


if __name__ == "__main__":
    main()

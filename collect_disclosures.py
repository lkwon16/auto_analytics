"""공시목록 수집 (정정공시 식별 + 백테스트 라벨용) — 수정판 v2
API: list (공시검색)
 
[수정 사항]
- OpenDART는 기업 미지정 검색 시 조회기간을 최대 3개월로 제한
  → 연 단위 → 분기 단위 요청으로 변경
- API 에러를 "데이터 없음"으로 뭉개지 않고 상태코드·메시지를 그대로 출력
 
실행: python collect_disclosures.py
"""
import sys
import time
from datetime import date

import pandas as pd
import requests
from sqlalchemy import create_engine, text

from config import DART_API_KEY, DB_URL, BASE_URL

# Windows 콘솔(cp949)에서 이모지(⚠️ 등) 출력 시 UnicodeEncodeError로 죽는 것을 방지
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 분기별 (시작월일, 종료월일) — 3개월 제한 준수
QUARTERS = [("0101", "0331"), ("0401", "0630"), ("0701", "0930"), ("1001", "1231")]
 
 
def fetch_disclosures(bgn_de: str, end_de: str, corp_cls: str) -> pd.DataFrame:
    """기간 내 정기공시(A) 전체를 페이지 순회하며 수집. corp_cls: Y=유가증권, K=코스닥"""
    url = f"{BASE_URL}/list.json"
    all_rows, page = [], 1
    while True:
        params = {
            "crtfc_key": DART_API_KEY,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "pblntf_ty": "A",
            "corp_cls": corp_cls,
            "page_no": page,
            "page_count": 100,
        }
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
 
        status = data.get("status")
        if status == "013":   # 조회 데이터 없음 (정상 — 해당 기간에 공시가 없는 것)
            break
        if status == "020":
            raise SystemExit("일일 API 요청 한도 초과. 내일 재실행하면 이어서 수집됩니다.")
        if status != "000":
            print(f"  ⚠️ API 오류 [{bgn_de}~{end_de}/{corp_cls}] "
                  f"status={status}, message={data.get('message')}")
            break
 
        all_rows.extend(data["list"])
        if page >= int(data.get("total_page", 1)):
            break
        page += 1
        time.sleep(0.1)
 
    df = pd.DataFrame(all_rows)
    if df.empty:
        return df
    df = df[["rcept_no", "corp_code", "corp_name", "report_nm", "rcept_dt"]].copy()
    df["rcept_dt"] = pd.to_datetime(df["rcept_dt"], format="%Y%m%d").dt.date
    return df
 
 
def main():
    if not DART_API_KEY:
        raise SystemExit(".env 파일에 DART_API_KEY를 설정하세요.")
 
    engine = create_engine(DB_URL)
    today = date.today()
    total = 0
 
    for year in range(2021, today.year + 1):
        for q_start, q_end in QUARTERS:
            bgn_de = f"{year}{q_start}"
            end_de = f"{year}{q_end}"
            if bgn_de > today.strftime("%Y%m%d"):   # 미래 분기는 건너뜀
                continue
 
            for market in ("Y", "K"):
                df = fetch_disclosures(bgn_de, end_de, market)
                if df.empty:
                    print(f"[{year} Q{QUARTERS.index((q_start, q_end)) + 1}/{market}] 0건")
                    continue
 
                # 이미 적재된 접수번호 제외 (재실행 안전)
                with engine.connect() as conn:
                    existing = pd.read_sql(text("SELECT rcept_no FROM disclosures"), conn)
                df = df[~df["rcept_no"].isin(existing["rcept_no"])]
 
                if not df.empty:
                    df.to_sql("disclosures", engine, if_exists="append", index=False)
                total += len(df)
                print(f"[{year} Q{QUARTERS.index((q_start, q_end)) + 1}/{market}] {len(df):,}건 적재")
 
    print(f"\n공시목록 수집 완료 — 총 {total:,}건")
 
    # 정정공시 건수 바로 확인
    with engine.connect() as conn:
        n = conn.execute(text(
            "SELECT COUNT(*) FROM disclosures WHERE report_nm LIKE '%기재정정%'"
        )).scalar()
    print(f"이 중 기재정정 공시: {n:,}건 (백테스트 라벨 후보)")
 
 
if __name__ == "__main__":
    main()
 
"""OpenDART 전체 기업 고유번호(corpCode) 수집 → 상장사만 필터링하여 DB 적재
실행: python collect_corp_codes.py
"""
import io
import zipfile
import xml.etree.ElementTree as ET

import pandas as pd
import requests
from sqlalchemy import create_engine, text

from config import DART_API_KEY, DB_URL, BASE_URL


def fetch_corp_codes() -> pd.DataFrame:
    """corpCode.xml(zip) 다운로드 후 DataFrame으로 변환"""
    url = f"{BASE_URL}/corpCode.xml"
    resp = requests.get(url, params={"crtfc_key": DART_API_KEY}, timeout=30)
    resp.raise_for_status()

    # API 키 오류 시 zip이 아니라 XML 에러 메시지가 옴 → 친절한 안내
    if not resp.content[:2] == b"PK":
        raise SystemExit(
            "API 응답이 zip이 아닙니다. .env의 DART_API_KEY가 올바른지 확인하세요.\n"
            f"응답 내용 앞부분: {resp.content[:200]}"
        )

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        xml_data = zf.read(zf.namelist()[0])

    root = ET.fromstring(xml_data)
    rows = []
    for corp in root.iter("list"):
        rows.append({
            "corp_code": corp.findtext("corp_code"),
            "corp_name": corp.findtext("corp_name"),
            "stock_code": (corp.findtext("stock_code") or "").strip() or None,
        })
    return pd.DataFrame(rows)


def main():
    if not DART_API_KEY:
        raise SystemExit(".env 파일에 DART_API_KEY를 설정하세요.")

    df = fetch_corp_codes()
    listed = df[df["stock_code"].notna()].copy()
    print(f"전체 {len(df):,}개 중 상장사 {len(listed):,}개")

    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        # 단순화: 기존 데이터 삭제 후 재적재 (기업코드는 마스터 데이터라 안전)
        conn.execute(text("DELETE FROM corp_master"))
    listed.to_sql("corp_master", engine, if_exists="append", index=False)
    print("corp_master 적재 완료")


if __name__ == "__main__":
    main()

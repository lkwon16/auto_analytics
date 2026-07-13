"""수시공시(이벤트성 공시) 수집 -> disclosure_events (모듈⑤ 소스 1/2)

정기공시(A, collect_disclosures.py)와 달리 유상증자·소송·M&A·감사인지정 등
"사건"을 나타내는 공시를 모은다. 편차 탐지(모듈④) 결과의 원인 후보로 매칭하는 데 쓴다
(match_signals.py). pblntf_ty:
  B=주요사항보고, F=외부감사관련, I=거래소공시
카테고리 분류는 report_nm 키워드 규칙 기반 (event_categories.py, LLM 미사용).

collect_disclosures.py와 동일하게 재실행 안전(idempotent)하며, 시장 전체(Y+K)를
분기 단위로 훑는다(OpenDART 기업 미지정 조회 3개월 제한).

실행: python collect_disclosure_events.py
"""
import sys
import time
from datetime import date

import pandas as pd
import requests
from sqlalchemy import create_engine, text

from config import DART_API_KEY, DB_URL, BASE_URL
from event_categories import categorize

# Windows 콘솔(cp949)에서 이모지(⚠️ 등) 출력 시 UnicodeEncodeError로 죽는 것을 방지
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QUARTERS = [("0101", "0331"), ("0401", "0630"), ("0701", "0930"), ("1001", "1231")]
PBLNTF_TYPES = ["B", "F", "I"]
MAX_RETRIES = 5
RETRY_BACKOFF_SEC = [2, 5, 10, 20, 30]   # 시도별 대기 시간 (DART 서버 간헐적 타임아웃 대응)


def _get_with_retry(url: str, params: dict) -> requests.Response:
    """네트워크 타임아웃·연결 끊김에 최대 MAX_RETRIES회 재시도(지수적 백오프)."""
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            wait = RETRY_BACKOFF_SEC[min(attempt, len(RETRY_BACKOFF_SEC) - 1)]
            print(f"  ⚠️ 네트워크 오류({exc.__class__.__name__}), {wait}초 후 재시도 "
                  f"({attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
    raise last_exc

DDL = """
CREATE TABLE IF NOT EXISTS disclosure_events (
    rcept_no    VARCHAR(14) PRIMARY KEY,
    corp_code   VARCHAR(8),
    corp_name   VARCHAR(200),
    report_nm   VARCHAR(300),
    rcept_dt    DATE,
    pblntf_ty   VARCHAR(2),
    categories  VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_disc_events_corp ON disclosure_events (corp_code, rcept_dt);
"""


def fetch_events(bgn_de: str, end_de: str, corp_cls: str, pblntf_ty: str) -> pd.DataFrame:
    """기간 내 지정 유형 공시 전체를 페이지 순회하며 수집. corp_cls: Y=유가증권, K=코스닥"""
    url = f"{BASE_URL}/list.json"
    all_rows, page = [], 1
    while True:
        params = {
            "crtfc_key": DART_API_KEY,
            "bgn_de": bgn_de,
            "end_de": end_de,
            "pblntf_ty": pblntf_ty,
            "corp_cls": corp_cls,
            "page_no": page,
            "page_count": 100,
        }
        resp = _get_with_retry(url, params)
        data = resp.json()

        status = data.get("status")
        if status == "013":   # 조회 데이터 없음 (정상)
            break
        if status == "020":
            raise SystemExit("일일 API 요청 한도 초과. 내일 재실행하면 이어서 수집됩니다.")
        if status != "000":
            print(f"  ⚠️ API 오류 [{bgn_de}~{end_de}/{corp_cls}/{pblntf_ty}] "
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
    df["pblntf_ty"] = pblntf_ty
    df["categories"] = df["report_nm"].apply(lambda nm: ",".join(categorize(nm)))
    return df


def main():
    if not DART_API_KEY:
        raise SystemExit(".env 파일에 DART_API_KEY를 설정하세요.")

    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        for stmt in DDL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    with engine.connect() as conn:
        existing = set(pd.read_sql(text("SELECT rcept_no FROM disclosure_events"), conn)["rcept_no"])

    today = date.today()
    today_str = today.strftime("%Y%m%d")
    total = 0

    for year in range(2021, today.year + 1):
        for q_idx, (q_start, q_end) in enumerate(QUARTERS, start=1):
            bgn_de = f"{year}{q_start}"
            end_de = f"{year}{q_end}"
            if bgn_de > today_str:   # 미래 분기는 건너뜀
                continue

            for pblntf_ty in PBLNTF_TYPES:
                for market in ("Y", "K"):
                    df = fetch_events(bgn_de, end_de, market, pblntf_ty)
                    if df.empty:
                        print(f"[{year} Q{q_idx}/{market}/{pblntf_ty}] 0건")
                        continue

                    df = df[~df["rcept_no"].isin(existing)]
                    if not df.empty:
                        df.to_sql("disclosure_events", engine, if_exists="append", index=False)
                        existing.update(df["rcept_no"])
                    total += len(df)
                    print(f"[{year} Q{q_idx}/{market}/{pblntf_ty}] {len(df):,}건 적재")

    print(f"\n수시공시 수집 완료 — 총 {total:,}건")

    with engine.connect() as conn:
        by_cat = pd.read_sql(
            text(
                "SELECT categories, COUNT(*) as n FROM disclosure_events "
                "WHERE categories != '' GROUP BY categories ORDER BY n DESC LIMIT 20"
            ),
            conn,
        )
    print("\n카테고리 분포 (상위 20, 복합 카테고리 포함):")
    print(by_cat.to_string(index=False))


if __name__ == "__main__":
    main()

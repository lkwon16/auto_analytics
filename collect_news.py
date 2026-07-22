"""뉴스 신호 수집 -> news_signals (모듈⑤ 소스 2/2, 네이버 뉴스 검색 API)

다른 collect_*.py와 달리 전체 모집단(1,905개 기업)을 일괄 수집하지 않는다 — 네이버
뉴스 검색 API는 일 25,000건 호출 한도가 있어 전수 수집이 비현실적이고, 실제 사용
시나리오도 "플래그된 특정 기업을 감사인이 조회"하는 온디맨드 방식에 가깝다.
match_signals.py에서 필요할 때 기업 단위로 호출하는 구조.

[한계] 네이버 뉴스 검색 API는 날짜 범위 필터를 지원하지 않는다(sort=date로 최신순
정렬만 가능, start 파라미터 최대 1000 -> 조회 가능한 최대 건수 1000건). 오래된
회계연도(예: 2021년) 관련 기사는 그 사이 더 최근 기사가 많으면 조회 범위 밖으로
밀려나 있을 수 있다. 결과 해석 시 "누락 가능성"을 함께 고려할 것.

단독 실행(데모): python collect_news.py "기업명" [corp_code]
"""
import sys
import time
from email.utils import parsedate_to_datetime

import pandas as pd
import requests
from sqlalchemy import create_engine, text

from config import DB_URL, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET, NAVER_NEWS_URL

# Windows 콘솔(cp949)에서 이모지(⚠️ 등) 출력 시 UnicodeEncodeError로 죽는 것을 방지
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DDL = """
CREATE TABLE IF NOT EXISTS news_signals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    corp_code   VARCHAR(8),
    corp_name   VARCHAR(200),
    title       VARCHAR(500),
    description VARCHAR(1000),
    link        VARCHAR(500),
    pub_date    DATE,
    UNIQUE(corp_code, link)
);
CREATE INDEX IF NOT EXISTS idx_news_corp ON news_signals (corp_code, pub_date);
"""


def _strip_tags(s: str) -> str:
    return s.replace("<b>", "").replace("</b>", "").replace("&quot;", '"').replace("&amp;", "&")


def fetch_news(query: str, max_results: int = 100) -> pd.DataFrame:
    """네이버 뉴스 검색 API로 최신순 기사를 최대 max_results건 가져온다 (최대 1000)."""
    if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
        raise SystemExit(".env 파일에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET을 설정하세요.")

    headers = {"X-Naver-Client-Id": NAVER_CLIENT_ID, "X-Naver-Client-Secret": NAVER_CLIENT_SECRET}
    rows, start = [], 1
    while start <= min(max_results, 1000):
        display = min(100, max_results - len(rows))
        params = {"query": query, "display": display, "start": start, "sort": "date"}
        resp = requests.get(NAVER_NEWS_URL, headers=headers, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"  ⚠️ 네이버 뉴스 API 오류 status={resp.status_code}, body={resp.text[:200]}")
            break
        items = resp.json().get("items", [])
        if not items:
            break
        rows.extend(items)
        if len(items) < display:   # 더 이상 결과 없음
            break
        start += display
        time.sleep(0.1)

    if not rows:
        return pd.DataFrame(columns=["title", "description", "link", "pub_date"])

    df = pd.DataFrame(rows)
    df["title"] = df["title"].apply(_strip_tags)
    df["description"] = df["description"].apply(_strip_tags)
    df["pub_date"] = df["pubDate"].apply(lambda s: parsedate_to_datetime(s).date())
    return df[["title", "description", "link", "pub_date"]]


def collect_news_for_company(corp_code: str, corp_name: str, engine=None, max_results: int = 50) -> int:
    """특정 기업의 뉴스를 조회해 news_signals에 적재(중복 링크는 스킵). 신규 적재 건수 반환."""
    engine = engine or create_engine(DB_URL)
    with engine.begin() as conn:
        for stmt in DDL.split(";"):
            if stmt.strip():
                conn.execute(text(stmt))

    df = fetch_news(corp_name, max_results=max_results)
    if df.empty:
        return 0

    with engine.connect() as conn:
        existing = pd.read_sql(
            text("SELECT link FROM news_signals WHERE corp_code = :cc"), conn, params={"cc": corp_code}
        )
    df = df[~df["link"].isin(existing["link"])]
    if df.empty:
        return 0

    df = df.copy()
    df.insert(0, "corp_code", corp_code)
    df.insert(1, "corp_name", corp_name)
    df.to_sql("news_signals", engine, if_exists="append", index=False)
    return len(df)


def main():
    if len(sys.argv) < 2:
        raise SystemExit('사용법: python collect_news.py "기업명" [corp_code]')
    corp_name = sys.argv[1]
    engine = create_engine(DB_URL)

    corp_code = sys.argv[2] if len(sys.argv) > 2 else None
    if corp_code is None:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT corp_code FROM analysis_universe WHERE corp_name = :nm"), {"nm": corp_name}
            ).fetchone()
        if row is None:
            raise SystemExit(f"analysis_universe에서 '{corp_name}'을 찾을 수 없습니다. corp_code를 직접 지정하세요.")
        corp_code = row[0]

    n = collect_news_for_company(corp_code, corp_name, engine)
    print(f"{corp_name}({corp_code}): 신규 뉴스 {n}건 적재")


if __name__ == "__main__":
    main()

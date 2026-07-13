import os
from dotenv import load_dotenv

load_dotenv()

DART_API_KEY = os.getenv("DART_API_KEY")
DB_URL = os.getenv("DB_URL", "sqlite:///audit_ap.db")  # 기본값: SQLite (설치 불필요)

BASE_URL = "https://opendart.fss.or.kr/api"

# 네이버 뉴스 검색 API (모듈⑤ 뉴스 신호 소스, collect_news.py)
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
NAVER_NEWS_URL = "https://openapi.naver.com/v1/search/news.json"

# 수집 대상 연도 (최근 5개년)
TARGET_YEARS = [2021, 2022, 2023, 2024, 2025]

# 보고서 코드: 11011=사업보고서, 11012=반기, 11013=1분기, 11014=3분기
REPORT_CODE = "11011"

# 재무제표 구분: CFS=연결, OFS=별도
FS_DIV = "CFS"

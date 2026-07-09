# 분석적 절차 자동화 — 데이터 파이프라인

## 실행 방법 (Cursor 기준)

1. 이 폴더를 Cursor에서 열기 (File > Open Folder)
2. Cursor 내 터미널 열기 (Ctrl + `)
3. 아래 순서대로 실행:

```bash
# 패키지 설치
pip install -r requirements.txt

# .env 파일 준비: .env.example을 복사해서 .env로 만들고 API 키 입력
# (Windows) copy .env.example .env
# (Mac)     cp .env.example .env

# 1) DB 테이블 생성
python init_db.py

# 2) 상장사 기업코드 수집
python collect_corp_codes.py

# 3) 재무제표 수집 — 처음엔 반드시 테스트 모드로!
python collect_financials.py --test

# 테스트 성공 확인 후 전체 수집 (수 시간 소요, 중단돼도 재실행하면 이어짐)
python collect_financials.py

# 4) 공시목록 수집
python collect_disclosures.py
```

## 참고
- 기본 DB는 SQLite(파일 하나짜리 DB)라 별도 설치가 필요 없습니다.
  실행하면 폴더에 `audit_ap.db` 파일이 생깁니다.
- PostgreSQL로 바꾸려면 .env의 DB_URL만 수정하면 됩니다.
- OpenDART는 일 20,000건 요청 제한이 있습니다. 한도 초과 시 스크립트가
  안내 메시지와 함께 종료되며, 다음 날 재실행하면 이어서 수집합니다.
# auto_analytics

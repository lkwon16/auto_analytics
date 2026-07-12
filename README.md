# 분석적 절차 자동화 — 데이터 파이프라인

동종업계 벤치마킹 기반 분석적 절차(ISA 520) 자동화 도구의 데이터 파이프라인입니다.
전체 기획·설계는 `CLAUDE.md`, 알려진 한계는 `LIMITATIONS.md`를 참고하세요.

## 실행 환경

- Windows에서 `python` 명령이 MS Store 스텁으로 연결되는 경우가 있어 **`py` 런처를
  사용**합니다 (예: `py init_db.py`). Mac/Linux는 `python3`로 대체하세요.
- 터미널(Cursor·VS Code·PowerShell 등 아무 터미널이나 무방)에서 이 폴더로 이동 후
  아래 순서대로 실행합니다.

## 최초 설치

```bash
pip install -r requirements.txt
```

`.env` 파일을 프로젝트 루트에 직접 만들고 아래 두 줄을 채웁니다 (git에 올리지 말 것):

```
DART_API_KEY=발급받은_OpenDART_API_키
DB_URL=sqlite:///audit_ap.db
```

## 실행 순서 (전체 파이프라인)

```bash
# 1) DB 테이블 생성 (최초 1회, 이후 스키마 변경 시 재실행해도 안전)
py init_db.py

# 2) 상장사 기업코드 수집 → corp_master
py collect_corp_codes.py

# 3) 재무제표 수집 → financial_statements (처음엔 반드시 테스트 모드로!)
py collect_financials.py --test
py collect_financials.py            # 테스트 성공 확인 후 전체 수집 (수 시간, 중단 후 재실행하면 이어서 진행)

# 4) 공시목록 수집 → disclosures (기재정정 라벨 후보 포함)
py collect_disclosures.py

# 5) 업종코드·결산월 수집 → corp_master.industry_code / acc_mt
py collect_industry_codes.py

# 6) 분석 모집단 정의 → analysis_universe (금융업·비12월결산 제외, CFS 3개년 이상)
py build_analysis_universe.py

# 7) 재무비율 10개 계산 → ratios
py compute_ratios.py

# 8) 기대치·편차 탐지 → flags (peer 중위수+자사 전년도 가중 결합, 상위 10% 플래그)
py detect_flags.py
```

각 스크립트는 재실행 안전(idempotent)합니다 — 중단되거나 다시 실행해도 이미 수집된
데이터는 건너뛰고 이어서 진행합니다. `peer_group.py`는 별도 실행용이 아니라
`get_peer_group()` 함수를 다른 스크립트에서 가져다 쓰는 모듈입니다
(`py peer_group.py`로 실행하면 동작 확인용 샘플 출력만 나옵니다).

## 현재 DB 상태 (2026-07-12 기준)

| 테이블 | 행 수 | 비고 |
|---|---|---|
| `corp_master` | 3,976개 기업 | industry_code·acc_mt 전부 채워짐 |
| `financial_statements` | 약 126만 행 | BS/IS/CF/CIS만 (SCE는 데이터 손상으로 삭제, `LIMITATIONS.md` §7) |
| `disclosures` | 59,153건 | 2021~2026, 기재정정 6,584건(라벨 미정제, `LIMITATIONS.md` §5) |
| `analysis_universe` | 1,905개 기업 | 금융업·비12월결산·CFS 3개년 미만 제외 |
| `ratios` | 9,038개(기업×연도) | 10개 비율 중 9개가 매핑 커버리지 85% 이상 |
| `flags` | 9,038개(기업×연도) | 종합 스코어 계산 가능 8,974건(99.3%), 상위 10% 플래그 898건 |

진행 단계는 STEP 1(분석 모집단)·STEP 2(비율 엔진)·STEP 3(기대치·편차 탐지)까지 완료,
STEP 4(미니 백테스트) 착수 전입니다. 세부 계획은 `CLAUDE.md` §5 MVP 플로우 참고.
STEP 3에서 발견된 이슈(비율 극단값, 실무 배포 시 당기값 입력 구조 등)는
`LIMITATIONS.md` §9~11 참고.

## 참고

- 기본 DB는 SQLite(파일 하나짜리 DB)라 별도 설치가 필요 없습니다.
  실행하면 폴더에 `audit_ap.db` 파일이 생깁니다.
- PostgreSQL로 바꾸려면 `.env`의 `DB_URL`만 수정하면 됩니다.
- OpenDART는 일 20,000건 요청 제한이 있습니다. 한도 초과 시 스크립트가
  안내 메시지와 함께 종료되며, 다음 날 재실행하면 이어서 수집합니다.
- 분석 결과를 해석하기 전에 `LIMITATIONS.md`를 반드시 함께 읽으세요
  (생존편향, 라벨 노이즈 등 결과 해석에 영향을 주는 한계 기록).

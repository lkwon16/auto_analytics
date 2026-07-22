# CLAUDE.md — 프로젝트 컨텍스트 핸드오프

> 이 문서는 이전 대화(Claude 웹)에서 진행된 프로젝트 기획·구현 내용 전체를 Claude Code(CLI)에 전달하기 위한 요약이다.
> 프로젝트 루트(`audit-ap-pipeline/`)에 `CLAUDE.md`로 저장하면 Claude Code가 자동으로 컨텍스트로 읽는다.

---

## 1. 프로젝트 개요

- **개발자 배경**: 회계사 + 개발 역량 보유. 대형 회계법인 지원용 포트폴리오 프로젝트
- **주제**: 동종업계 벤치마킹 기반 **분석적 절차(analytical procedures) 자동화** 도구
- **핵심 컨셉**: 감사기준 ISA 520의 3단계 구조(기대치 형성 → 허용 편차 설정 → 편차 조사)를 그대로 코드로 구현. 단순 이상탐지(플래깅)에서 끝나지 않고 편차 원인 설명 + 감사절차 제안까지 연결
- **데이터**: OpenDART API (한국 금융감독원 전자공시). 상장사 재무제표 + 공시목록
- **AI 사용 구분** (중요): 핵심 엔진은 통계·전통 ML(회귀, 표준화 편차, isolation forest, SHAP). LLM은 리포트 서술 자동화의 선택적 확장 기능일 뿐. 판단은 통계가 하고 LLM은 서술만 하는 설계

## 2. 전체 시스템 설계 — 모듈 ①~⑦ (명칭 확정, 문서 간 통일됨)

| 번호 | 모듈 | 역할 |
|---|---|---|
| ① | 데이터 파이프라인 | OpenDART 재무제표·공시목록 수집 → SQLite |
| ② | 동종그룹 구성 모듈 | KSIC 업종코드 수집, peer group 함수, 표본 부족 시 상위 분류 fallback |
| ③ | 재무비율 엔진 | XBRL 표준계정 매핑 + 핵심 비율 산출 |
| ④ | 기대모형·편차 탐지 레이어 | 기대치(peer 분포/자사 추세/회귀) + 동적 임계값 + 편차 스코어 + 설명 |
| ⑤ | 뉴스·공시 텍스트 신호 모듈 | 편차 원인 후보를 뉴스·수시공시에서 수집, 편차 시점과 매칭 |
| ⑥ | 백테스트 모듈 | 정정공시·감사의견 변형 라벨로 유효성 검증 (Precision@K, lift) |
| ⑦ | 리포트·대시보드 | Streamlit. 기업 검색 → 플래그 → 원인 → 제안 감사절차 |

## 3. 현재 코드베이스 상태

### 폴더 구조 (`audit-ap-pipeline/`)
```
config.py                # API 키(.env), DB_URL(기본 sqlite:///audit_ap.db), TARGET_YEARS=[2021..2025], REPORT_CODE="11011"(사업보고서), FS_DIV="CFS"(연결)
init_db.py               # 테이블 생성: corp_master / financial_statements / disclosures
collect_corp_codes.py    # corpCode.xml 수집 → 상장사만 corp_master 적재
collect_financials.py    # fnlttSinglAcntAll API. --test 모드(10개 기업). 재실행 안전(연도별 수집완료 기업 스킵). 100개 단위 버퍼 적재
collect_disclosures.py   # list API. 정기공시(pblntf_ty=A), 유가증권(Y)+코스닥(K)
requirements.txt         # requests pandas sqlalchemy python-dotenv tqdm
.env                     # DART_API_KEY, DB_URL (git에 올리지 말 것)
audit_ap.db              # SQLite DB (생성됨)
```

### 진행 상태
- [x] 파이프라인 구축 완료, 사용자 PC(Windows)에서 실행 성공
- [x] 재무제표 수집 실행됨 (완료 여부·기업 수 미확인 — 확인 필요)
- [~] 공시목록: **버그 수정 이력 있음** — OpenDART list API는 기업 미지정 시 조회기간 최대 3개월 제한. 초기 버전이 연 단위 요청으로 전부 "데이터 없음" 처리됨 → 분기 단위 요청 + 에러 메시지 노출 + 기재정정 건수 출력하는 수정판으로 교체함. **수정판 실행 결과는 아직 미확인**
- [ ] 업종코드 수집 (미착수 — 기업개황 company.json API, 기업당 1건 호출 ≈ 2,600건)

### 실행 환경 주의
- **Windows + Cursor**. `python` 명령이 MS Store 스텁에 연결된 이력 → **`py` 런처 사용** (예: `py collect_financials.py --test`)
- OpenDART 일 20,000건 한도. 재무제표 수집일과 업종코드 수집일 분리 필요
- DB는 SQLite (초기), 확장 시 PostgreSQL 전환 예정

## 4. 비판적 검토 결과 (이미 수행됨 — 반영 방침 포함)

### 치명적 (인지 필수)
1. **생존 편향**: 현 상장사 기준 수집이라 2021~25년 상폐 기업(분식 최다 집단) 누락 → MVP에서는 LIMITATIONS.md에 기록만
2. **기재정정 라벨 노이즈**: `[기재정정]`의 다수가 오타·경미한 정정 → 사업보고서·감사보고서 정정만 필터하는 라벨 정제 필수
3. **정정 후 데이터 순환 문제**: OpenDART 재무제표는 정정 반영 최종본 → "정정 발생 이전 연도 재무제표로 플래깅했는가"의 시계열 설계로 우회 + 한계 명시

### 중대 (설계 반영)
4. 연간 데이터 vs ⑤의 "분기±6개월 시점 매칭" 해상도 불일치 → MVP에서 ⑤ 제외로 해소
5. **금융업(KSIC 64~66) 제외 필수** — 재무제표 구조가 달라 매핑 전체 실패
6. CFS 단일 수집 → 연결 미작성 기업 조용히 누락 (status 013). v2에서 OFS fallback
7. KSIC 명목상 분류 문제, 지주회사 peer 왜곡 → 지주회사 제외 규칙
8. "정정공시 예측" ≠ "분석적 절차" — 정상 사업 이벤트 편차는 올바른 플래그지만 라벨상 오탐. v2에서 평가 프레임 이원화
9. 일정 낙관적 → MVP로 범위 축소
10. 12월 결산 가정 암묵적, SQLite에 UNIQUE 제약 없어 중복 적재 가능 → 비율 계산 전 중복 체크

## 5. MVP 플로우 (현재 실행 계획, 3~4주)

**목표 시나리오(DoD)**: 대시보드에서 기업 검색 → 비율 추이 + 업계 대비 위치 → 플래그 목록에서 상위 편차 3개 확인 → 백테스트 표에서 랜덤 대비 lift 확인

**MVP에서 자른 것**: ⑤ 전체(DART 링크 표시로 대체), KR-FinBERT, isolation forest, SHAP(편차 순위로 대체), 회귀 기대모형, 분기 데이터, 상폐기업 소급

- **STEP 1 (2~3일)** 분석 모집단 정의: 금융업 제외 + 12월 결산 + CFS 3개년 이상 → `analysis_universe` 테이블. 중복 체크. LIMITATIONS.md 시작
- **STEP 2 (4~5일)** 업종코드 수집 + peer group 함수(중분류→대분류 fallback) + **비율 10개만**: 매출채권회전율, 재고자산회전율, GP마진, 영업이익률, 판관비율, 부채비율, 이자보상배율, 총발생액/총자산, (영업이익−영업CF)/총자산, 매출액증가율 → `ratios` 테이블. 매핑 커버리지 85% 이상 통과 기준
- **STEP 3 (4~5일)** 미니 탐지: 기대치 = peer 중위수 + 자사 전년도. 편차 = (실제−기대치)/peer IQR. 종합 스코어 = 상위 3개 비율 평균. 설명 = 기여 상위 3개 비율+방향. 임계값 = 상위 10% 고정 → `flags` 테이블
- **STEP 4 (3~4일)** 미니 백테스트: 라벨 = 사업·감사보고서 기재정정만. N년 플래그 → N+1년 정정 대조. 지표 = Precision@10% + lift 하나만. 성능 낮아도 측정 체계 완성이 목표
- **STEP 5 (3~4일)** Streamlit 화면 2개: (1) 기업 조회 — 비율 추이+peer 중위수 라인, (2) 플래그 목록 — 스코어 상위 + 편차 3개 + DART 링크

**v2 업그레이드 경로 (우선순위순)**: ⑤ 텍스트 신호 모듈 → 평가 프레임 이원화 → 회귀 기대모형+동적 임계값 → OFS fallback·분기 데이터 → isolation forest+SHAP → LLM 리포트 서술

**금지 사항**: 중간에 v2 기능 붙이기, 비율 개수 늘리기, 라벨 정제 1주 초과, 대시보드 꾸미기

## 6. 진행 현황 (2026-07-12 기준)

- STEP 1(분석 모집단, `analysis_universe` 1,905개), STEP 2(XBRL 매핑+peer group+비율 10개,
  `ratios` 9,038건) 완료.
- STEP 3(기대치·편차 탐지, `detect_flags.py`) 완료: 기대치=peer 중위수 50%+자사 전년도 50%
  가중 결합, 편차=(실제-기대치)/peer IQR, 종합 스코어=|편차| 상위 3개 평균, 임계값=상위 10%
  → `flags` 테이블(9,038건, 898건 플래그).
  - `revenue_growth`가 분모(전년 매출) 왜소화로 폭발하는 문제 발견 → `SANITY_BOUNDS`로
    `|값|>10` 제외 처리 (`LIMITATIONS.md` §9 추가 반영).
  - 매출 극소 초기 성장기업의 sga_ratio 등 다른 비율도 구조적으로 극단값을 가질 수 있음을
    확인 — 이건 진짜 이상치일 가능성이 높아 캡을 씌우지 않기로 결정 (`LIMITATIONS.md` §10).
  - **중요 설계 이슈 기록** (`LIMITATIONS.md` §11): 지금 구조는 "당기 실제값"도 DART 확정
    공시 데이터를 그대로 쓰는데, 이는 STEP 4 백테스트 목적에는 맞지만 실무 배포 시나리오와
    다르다 — 실제 감사에서는 당기 실제값이 회계사가 직접 입력하는 피감사회사의 원본(미확정)
    자료여야 한다(peer·자사 과거 추세는 DART 기반 그대로 유효). 지금은 구조 변경 없이 기록만
    해두고, v2/STEP 5 단계에서 "당기 값 입력 override" 경로를 추가하기로 함.

- STEP 4(`backtest.py`) 완료: 라벨=`[기재정정]사업보고서`(반기·분기 제외, 감사보고서 단독
  정정은 데이터에 없음 확인). N년 플래그 → N+1년(캘린더) 정정 발생 여부 대조.
  결과: 기준율 20.12%, Precision@10% 18.37%, **Lift 0.91배(무작위 이하)**.
  - 원인: §6(정정 후 데이터 순환 문제)이 실증된 것으로 판단 — DART 확정 데이터는 이미
    정정 반영된 값이라 탐지 대상인 이상 징후가 데이터 도달 전에 지워져 있음. 실무에서
    raw 당기 데이터로 분석하면(§11) 다를 수 있으나 검증 불가능한 가설로 남김
    (`LIMITATIONS.md` §12 상세).
  - MVP 목표("성능 낮아도 측정 체계 완성")대로 STEP 4 완료 처리, 추가 튜닝 없이 진행.
  - **후속 개선 (v2 진행 중 반영)**: 2026-07-19 `trade_receivables` XBRL 매핑 수정으로
    Lift 0.91배→1.02배, 2026-07-20 `correction_details.is_financial_related` 기반
    재무 관련 정정만 라벨로 쓰도록 정제(`LIMITATIONS.md` §5)해 Lift **1.02배→1.09배**,
    기준율 20.49%→10.02%로 개선(상세 `LIMITATIONS.md` §12). 여전히 "무작위보다 유의미"
    하다고 보기엔 부족한 수준 — §6 look-ahead 구조적 한계가 근본 원인이라는 결론은 유효.

- STEP 5(`dashboard.py`) 완료: Streamlit 2개 화면.
  - (1) 기업 조회: 회사명 검색 → 선택 → 10개 비율 각각 연도별 자사 값 vs peer 중위수
    라인 차트 (`peer_group.get_peer_group()` 재사용).
  - (2) 플래그 목록: `flags.is_flagged=1` 898건을 종합 스코어 순으로 표시(상위 N건
    슬라이더), 각 건 펼치면 상위 3개 기여 비율+편차+방향, 최신 공시 rcept_no 기반
    DART 공시 링크.
  - 실행 확인: `py -m streamlit run dashboard.py`로 기동 후 핵심 조회 로직(load_universe,
    load_ratios, get_peer_group, load_flags, load_latest_disclosures)을 실제 DB에
    직접 호출해 정상 동작 검증함(2026-07-12).
  - `requirements.txt`에 `streamlit` 추가.

## 7. 다음 작업

MVP 플로우(STEP 1~5) 전부 완료. v2 우선순위 1번인 ⑤ 텍스트 신호 모듈 착수함
(아래 6-1 참고). 나머지는 CLAUDE.md §5 "v2 업그레이드 경로" 참고 (평가 프레임
이원화 → 회귀 기대모형+동적 임계값 → OFS fallback·분기 데이터 →
isolation forest+SHAP → LLM 리포트 서술). `LIMITATIONS.md` §5(기재정정 라벨 정제)는
2026-07-20 완료됨(§6-2 참고). §11(실무용 당기값 입력 경로)·§13(이벤트 카테고리
정밀도 1차 개선)은 2026-07-22 완료됨(§6-3 참고).

## 6-3. 실무용 당기값 입력 경로 + 카테고리 매칭 개선 (2026-07-22)

- **§11**: `detect_flags.py`의 편차 산식(기대치 가중결합·IQR 표준화·상위 3개
  종합 스코어)을 `deviation_for_value()`/`rank_top_contributors()`로 공용 함수화.
  신규 `live_override.py`가 이를 재사용해, 감사인이 당기(미확정) 원본 계정값
  12개(매출액·매출원가·영업이익 등)를 입력하면 peer·자사 전년도는 기존 DART
  확정 데이터(`ratios` 테이블) 그대로 쓰고 당기 값만 대체해 편차를 계산한다.
  peer도 동일 12월 결산 가정이라 당기 데이터가 아직 없을 수 있어, peer 회사별로
  당기→전기 순으로 값을 채택하는 fallback을 뒀다. `dashboard.py`에 "당기값
  입력(실무용)" 탭으로 연결, 헤드리스 Edge로 실제 계산까지 검증(경인전자 예시,
  기존 배치 결과와 거의 동일한 편차값 확인).
- **§13**: `disclosure_events` 288,150건의 실제 `report_nm` 빈도를 집계해
  미매칭 유형(196,272건, 68.1%)을 실측 감사. 빈도 높고 인과관계 뚜렷한
  "매출액또는손익구조"(30%/15% 변동 공정공시) → "실적변동" 카테고리,
  "채무보증" → "채무보증" 카테고리 신규 추가, "자금조달"에도 증권발행·전환가액
  등 키워드 보강. 기존 `disclosure_events.categories`(수집 시점 저장값) 백필
  실행(35,808건 갱신) — 미매칭 196,272 → **160,467건(55.7%)**로 개선.
  `LIMITATIONS.md` §11·§13 상세 반영, 커밋(`782b373`, `21dcccb`).

## 6-1. 모듈⑤ 텍스트 신호 모듈 진행 현황 (2026-07-13 착수)

MVP에서 제외됐던 모듈⑤(뉴스·공시 텍스트 신호)를 v2 첫 항목으로 구현 시작.
LIMITATIONS.md §8(연간 데이터 vs 시점 매칭 해상도 불일치)을 "편차 시점 ±N개월"이
아니라 **회계연도 자체를 매칭 윈도우로 잡는 방식**으로 재설계해 해소함
(`match_signals.py`의 `_window()`: concurrent=회계연도 1/1~12/31,
post_year=익년 1/1~4/30).

- **`event_categories.py`**: report_nm 키워드 규칙으로 수시공시를 7개 카테고리(자금조달·
  소송·영업정지_제재·M&A_구조조정·감사관련·지배구조변경·부실징후)로 분류하고,
  10개 재무비율 각각을 관련 카테고리에 매핑(`RATIO_TO_CATEGORIES`). LLM 미사용,
  전부 규칙 기반(CLAUDE.md §1 원칙 준수).
- **`collect_disclosure_events.py`** (모듈⑤ 소스 1): DART 수시공시(B=주요사항보고,
  F=외부감사관련, I=거래소공시) 수집 → `disclosure_events` 테이블.
  `collect_disclosures.py`와 동일한 분기 단위 시장 전체 스캔 구조.
  **재실행 중 네트워크 타임아웃으로 2회 죽음** → `_get_with_retry()`(최대 5회,
  지수 백오프)를 추가해 견고화. 2026-07-13 기준 2021~2025년치는 완료, 2026년
  일부 남은 상태(재실행하면 이어서 진행됨, 아직 마무리 실행 안 함).
- **`collect_news.py`** (모듈⑤ 소스 2): 네이버 뉴스 검색 API. 다른 `collect_*.py`와
  달리 **전체 모집단을 배치 수집하지 않고 기업 단위 온디맨드**로 설계함 — 일 25,000건
  API 한도 + 날짜범위 필터 미지원(`LIMITATIONS.md` §14)이라 배치가 비현실적이고,
  실제 사용 시나리오(감사인이 플래그된 기업을 개별 조회)에도 더 부합.
- **`match_signals.py`**: `get_candidate_signals(corp_code, bsns_year, engine, top_ratios)`
  — 회계연도 윈도우 내 수시공시·뉴스를 조회하고, 편차 top1~3 비율과 카테고리가
  겹치는 수시공시에 `relevant=True` 태그. CLI로 `py match_signals.py <corp_code>
  <bsns_year>` 실행 가능.
- **`dashboard.py` 통합**: "플래그 목록" 탭의 각 항목 펼침(expander) 안에
  `render_candidate_signals()`로 편차 원인 후보를 바로 보여줌. 뉴스 미수집 상태면
  그 자리에서 "뉴스 조회하기" 버튼으로 `collect_news_for_company()`를 온디맨드 호출.
- **검증(2026-07-13)**: 한양이엔지(00216762) 2022년(정상 범위 플래그, 종합
  스코어 9.73 — 이노스페이스 같은 §10 극단값 케이스 아님)을 예시로 매칭 확인.
  `interest_coverage` 등 편차에 "자기주식취득신탁계약체결결정"(반복 연장)·
  "감사보고서제출"이 ★관련으로 정확히 매칭됨. 대시보드 초기 로드가 실행하는
  것과 동일하게 상위 플래그 50건 전체에 대해 `get_candidate_signals`를 직접
  호출해 오류 0건 확인(브라우저 자동화 도구가 환경에 없어 실제 화면 스크린샷
  검증은 못 함 — 사용자가 `py -m streamlit run dashboard.py`로 직접 확인 필요).
  - 버그 2건 발견·수정: (1) SQLite에서 읽은 날짜 컬럼이 문자열로 반환되는데
    `datetime.date`와 비교하다 죽는 버그(`match_signals.py`), (2) Windows 콘솔
    (cp949)이 이모지·특수문자(⚠️, —, ★) 출력 시 죽는 버그 — 관련 스크립트
    전체(`collect_disclosure_events.py`, `collect_news.py`, `match_signals.py`,
    `collect_disclosures.py`)에 `sys.stdout.reconfigure(encoding="utf-8",
    errors="replace")` 추가.
- **`disclosure_events` 2026년치 수집 완료** (2026-07-13 착수 → 2026-07-15/16
  두 세션에 걸쳐 완료, 최종 2026-07-16). 원본 `collect_disclosure_events.py`가 매
  실행마다 2021년부터 전체 재스캔하는 비효율(연도/분기를 건너뛰지 않고 `rcept_no`
  중복 필터에만 의존)이 있어, 2026년 구간만 도는 임시 스크립트
  `collect_disclosure_events_2026only.py`를 만들어 실행(같은 `fetch_events`/
  `categorize` 재사용, idempotent라 여러 세션에 걸쳐 나눠 돌려도 안전했음).
  최종 결과: `disclosure_events` 246,305 → **288,150**건, 최신 `rcept_dt`
  2026-07-16(오늘). 2026 Q1~Q3 18개 조합(3분기×3유형×2시장) 전부 완료, Q4는
  아직 미도래라 자동 스킵됨. 작업 완료 후 임시 스크립트는 삭제함 — 다음에 2026
  Q4치(10월 이후)나 2027년치를 수집하려면 원본 `collect_disclosure_events.py`의
  "매번 2021년부터 전체 재스캔" 구조를 먼저 고쳐야 함(연도/분기 자체를 건너뛰는
  방식으로), 그렇지 않으면 같은 비효율이 재발함.
- Notion MCP 연동 완료 확인(워크스페이스에 "📊 분석적 절차 자동화 프로젝트" 메인
  페이지 + 모듈 ①~⑦ 개별 페이지 존재, `notion-search`로 조회 성공).
- **남은 일**: 카테고리 규칙 정밀도 개선(`LIMITATIONS.md` §13), 신규 적재된 2026년
  수시공시 데이터로 `match_signals.py` 매칭 재검증(선택), 원본
  `collect_disclosure_events.py`의 재스캔 비효율 구조 개선(위 참고).

## 6-2. 백테스트 라벨·매핑 품질 개선 (2026-07-19 ~ 2026-07-20)

STEP 4 백테스트 Lift 0.91배(§6, 무작위 이하)의 원인을 두 가지 데이터 품질 문제로
좁혀 순차 개선함:

- **2026-07-19**: `xbrl_mapping.py`의 `ACCOUNT_CANDIDATES` 11개 필드 실측 감사 →
  `trade_receivables`가 `ifrs-full_TradeAndOtherCurrentReceivables`(광의)와
  `dart_ShortTermTradeReceivable`(협의)을 동의어로 취급해 peer 비교가능성을 훼손하던
  문제 발견(동시 공시 396개사 중위수 84% 차이). 협의 태그 우선순위로 수정 →
  `compute_ratios.py`/`detect_flags.py`/`backtest.py` 재실행, Lift 0.91배→1.02배.
  (`LIMITATIONS.md` §15)
- **2026-07-20**: `LIMITATIONS.md` §5(기재정정 라벨 노이즈)의 데이터 수집은
  2026-07-13 세션에 이미 끝나 있었으나(`correction_details` 4,053건, 재무 관련
  1,968건), `backtest.py`가 이를 쓰지 않고 여전히 report_nm 패턴만으로 라벨링하던
  미완료 항목을 마무리. `backtest.py`가 `correction_details.is_financial_related=1`
  (오타·서식 정정 등 비재무 정정 제외)만 라벨로 쓰도록 수정 → 기준율 20.49%→10.02%,
  Lift 1.02배→**1.09배**. (`LIMITATIONS.md` §5, §12)
- **결론**: 라벨·매핑 품질 개선으로 얻을 수 있는 향상분은 이번 두 조치로 대체로
  소진된 것으로 판단. Lift가 여전히 1.1배 근처에 머무는 것은 §6의 look-ahead
  구조적 한계(DART 확정 데이터는 이미 정정 반영됨) 때문이라는 결론이 강화됨 —
  추가 라벨 튜닝보다 §11(실무용 당기값 입력 override 경로) 등 v2 항목으로 넘어가는
  것이 합리적.

## 8. 문서 산출물 (노션에 정리되어 있음)

- 프로젝트 기획안 v3 (모듈 ①~⑦ 상세 설계, 리스크·대응)
- 차후 진행 로드맵 v2 (모듈별 체크리스트·완료 기준 — 기획안과 명칭 통일됨)
- MVP 개발 플로우 (현재 유효한 실행 계획 — 로드맵보다 우선)
- 데이터 파이프라인 코드 문서

## 9. 코드 작성 시 지켜야 할 컨벤션

- 모든 수집 스크립트는 **재실행 안전(idempotent)** — 중단 후 재실행하면 이어서 진행
- API 에러는 삼키지 말고 status·message를 그대로 출력
- 한도 초과(status 020)는 명확한 안내와 함께 종료
- 사용자는 개발 숙련도가 높지 않음 — 에러 메시지는 친절하게, 실행 방법은 README에 갱신
- 주석·출력 메시지는 한국어
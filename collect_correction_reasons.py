"""사업보고서 [기재정정] 정정사유 수집 -> correction_details (LIMITATIONS.md §5 라벨 정제)

backtest.py가 라벨로 쓰는 "[기재정정]사업보고서" 6,584건 중 4,053건(제출기한연장 제외)은
report_nm만으로는 오타·경미한 서식 정정과 재무제표·감사의견에 실질적 영향을 주는 정정을
구분할 수 없다. document.xml API로 정정 원문을 내려받아 CORRECTION 섹션을 파싱하고,
correction_reasons.classify()의 규칙(사업보고서 표준 목차 III·V 챕터 또는 정정대상
공시서류의 재무제표/감사보고서 키워드)으로 재무 관련 여부를 판정해 저장한다.

document.xml은 정정분만이 아니라 사업보고서 전체(최대 수 MB)를 압축해 내려준다 —
API 특성상 피할 수 없음. 재실행 안전(이미 처리된 rcept_no는 스킵).

실행: python collect_correction_reasons.py [--test]
"""
import sys
import time
import zipfile
import io

import pandas as pd
import requests
from sqlalchemy import create_engine, text

from config import DART_API_KEY, DB_URL, BASE_URL
from correction_reasons import classify

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RESTATEMENT_PATTERN = "[기재정정]사업보고서%"
EXCLUDE_PATTERN = "%제출기한연장%"

MAX_RETRIES = 5
RETRY_BACKOFF_SEC = [2, 5, 10, 20, 30]

DDL = """
CREATE TABLE IF NOT EXISTS correction_details (
    rcept_no              VARCHAR(14) PRIMARY KEY,
    corp_code              VARCHAR(8),
    is_financial_related   INT NOT NULL,
    target_doc              VARCHAR(300),
    reason_text             VARCHAR(500),
    items_summary            VARCHAR(1000)
);
"""


def _get_with_retry(url: str, params: dict) -> requests.Response:
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            wait = RETRY_BACKOFF_SEC[min(attempt, len(RETRY_BACKOFF_SEC) - 1)]
            print(f"  ⚠️ 네트워크 오류({exc.__class__.__name__}), {wait}초 후 재시도 "
                  f"({attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
    raise last_exc


def _decode(data: bytes) -> str:
    """document.xml은 <?xml encoding="utf-8"?>로 선언돼 있어도 실제로는 옛 공시일수록
    euc-kr 바이트인 경우가 있다(2022년 이전 필기 확인됨). utf-8 strict 실패 시 폴백."""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("euc-kr", errors="replace")


def fetch_correction(rcept_no: str) -> dict:
    url = f"{BASE_URL}/document.xml"
    resp = _get_with_retry(url, {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no})

    content_type = resp.headers.get("content-type", "")
    if "json" in content_type or resp.content[:200].lstrip().startswith(b"<?xml"):
        # 정상 응답은 zip 바이너리다. status 코드가 담긴 xml/json 오류 응답이면 원인을 그대로 노출.
        if b"<status>" in resp.content[:300]:
            import re as _re
            sm = _re.search(rb"<status>(.*?)</status>", resp.content)
            mm = _re.search(rb"<message>(.*?)</message>", resp.content)
            status = sm.group(1).decode() if sm else "?"
            msg = mm.group(1).decode("utf-8", errors="replace") if mm else resp.text
            raise RuntimeError(f"status={status} message={msg}")
        data = resp.json()
        raise RuntimeError(f"status={data.get('status')} message={data.get('message')}")

    z = zipfile.ZipFile(io.BytesIO(resp.content))
    name = z.namelist()[0]
    raw = z.read(name)
    text_full = _decode(raw)
    return classify(text_full)


def main():
    if not DART_API_KEY:
        raise SystemExit(".env 파일에 DART_API_KEY를 설정하세요.")

    test_mode = "--test" in sys.argv

    engine = create_engine(DB_URL)
    with engine.begin() as conn:
        conn.execute(text(DDL))

    with engine.connect() as conn:
        targets = pd.read_sql(
            text(
                "SELECT rcept_no, corp_code, corp_name FROM disclosures "
                "WHERE report_nm LIKE :p AND report_nm NOT LIKE :e"
            ),
            conn, params={"p": RESTATEMENT_PATTERN, "e": EXCLUDE_PATTERN},
        )
        done = pd.read_sql(text("SELECT rcept_no FROM correction_details"), conn)

    targets = targets[~targets["rcept_no"].isin(done["rcept_no"])]
    if test_mode:
        targets = targets.head(10)

    total = len(targets)
    print(f"처리 대상: {total:,}건 (이미 완료: {len(done):,}건)")
    if total == 0:
        print("처리할 건이 없습니다 — 이미 전부 완료됨.")
        return

    n_ok, n_fail, n_financial = 0, 0, 0
    buffer = []

    for i, row in enumerate(targets.itertuples(index=False), 1):
        try:
            result = fetch_correction(row.rcept_no)
        except RuntimeError as e:
            msg = str(e)
            if "status=020" in msg:
                print("\n일일 API 요청 한도 초과. 내일 재실행하면 이어서 수집됩니다.")
                break
            if "status=014" in msg:
                # DART 원본이 아예 존재하지 않는 경우 — 재시도 무의미. 재무 무관(미확인)으로
                # 명시적으로 기록해 라벨 정제 대상에서 조용히 누락되지 않게 한다.
                buffer.append({
                    "rcept_no": row.rcept_no, "corp_code": row.corp_code,
                    "is_financial_related": 0, "target_doc": None,
                    "reason_text": "원본 문서 없음(DART status 014) — 재무 무관으로 보수적 처리",
                    "items_summary": None,
                })
                n_ok += 1
                continue
            print(f"  ⚠️ [{row.corp_name} {row.rcept_no}] API 오류: {msg}")
            n_fail += 1
            continue
        except Exception as e:
            print(f"  ⚠️ [{row.corp_name} {row.rcept_no}] 처리 실패: {e.__class__.__name__}: {e}")
            n_fail += 1
            continue

        n_ok += 1
        if result["is_financial_related"]:
            n_financial += 1

        buffer.append({
            "rcept_no": row.rcept_no,
            "corp_code": row.corp_code,
            "is_financial_related": int(result["is_financial_related"]),
            "target_doc": result["target_doc"],
            "reason_text": result["reason_text"],
            "items_summary": " | ".join(result["items"])[:1000],
        })

        if len(buffer) >= 50:
            pd.DataFrame(buffer).to_sql("correction_details", engine, if_exists="append", index=False)
            buffer = []

        if i % 100 == 0 or i == total:
            print(f"  진행: {i:,}/{total:,}건 (재무관련 {n_financial:,}건, 실패 {n_fail:,}건)")

        time.sleep(0.15)

    if buffer:
        pd.DataFrame(buffer).to_sql("correction_details", engine, if_exists="append", index=False)

    print(f"\n완료 — 처리 {n_ok:,}건 (재무관련 판정 {n_financial:,}건), 실패 {n_fail:,}건")


if __name__ == "__main__":
    main()

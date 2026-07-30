"""§17 파일럿 STEP 2: 원본 XBRL 수집·파싱 후 편차 재계산 (look-ahead 가설 실측 검증)

대상: pilot_original_xbrl.py가 만든 pilot_original_rcept_no.csv (95건).
각 건에 대해:
  1. fnlttXbrl.xml로 원본(정정 전) XBRL zip 다운로드 (로컬 캐시, 재실행 안전)
  2. xbrl_raw_parser로 12개 계정 원본값 추출
  3. live_override.compute_live_deviations 재사용 — peer·자사 전년도는 기존 확정
     데이터(ratios) 그대로, "당기" 값만 원본으로 대체해 편차 재계산
  4. detect_flags.rank_top_contributors로 종합 스코어 산출
  5. flags 테이블의 기존(확정본 기준) composite_score와 비교

실행: py pilot_original_xbrl_step2.py
"""
import io
import json
import sys
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import create_engine, text

from config import DART_API_KEY, DB_URL, BASE_URL, REPORT_CODE
from xbrl_raw_parser import parse_original_values
from live_override import compute_live_deviations
from detect_flags import rank_top_contributors, FLAG_THRESHOLD_PCT

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

CACHE_DIR = Path("pilot_xbrl_cache")
CACHE_DIR.mkdir(exist_ok=True)

MAX_RETRIES = 5
RETRY_BACKOFF_SEC = [2, 5, 10, 20, 30]


def _get_with_retry(url: str, params: dict) -> requests.Response:
    last_exc = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.exceptions.RequestException as exc:
            last_exc = exc
            wait = RETRY_BACKOFF_SEC[min(attempt, len(RETRY_BACKOFF_SEC) - 1)]
            print(f"  ⚠️ 네트워크 오류({exc.__class__.__name__}), {wait}초 후 재시도 ({attempt + 1}/{MAX_RETRIES})")
            time.sleep(wait)
    raise last_exc


def fetch_zip(rcept_no: str) -> bytes:
    cache_path = CACHE_DIR / f"{rcept_no}.zip"
    if cache_path.exists():
        return cache_path.read_bytes()

    url = f"{BASE_URL}/fnlttXbrl.xml"
    params = {"crtfc_key": DART_API_KEY, "rcept_no": rcept_no, "reprt_code": REPORT_CODE}
    resp = _get_with_retry(url, params)

    if resp.headers.get("Content-Type", "").startswith("application/json"):
        data = resp.json()
        raise RuntimeError(f"XBRL 없음(status={data.get('status')}, message={data.get('message')})")

    cache_path.write_bytes(resp.content)
    return resp.content


def main():
    if not DART_API_KEY:
        raise SystemExit(".env 파일에 DART_API_KEY를 설정하세요.")

    targets = pd.read_csv("pilot_original_rcept_no.csv", dtype={"corp_code": str, "rcept_no": str})
    print(f"대상: {len(targets)}건")

    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        current_flags = pd.read_sql(
            text("SELECT corp_code, bsns_year, composite_score FROM flags"), conn
        )
        threshold = current_flags["composite_score"].dropna().quantile(FLAG_THRESHOLD_PCT / 100)
    print(f"현재(확정본) 기준 플래그 임계값(상위 10%): {threshold:.4f}")

    rows = []
    for i, row in targets.iterrows():
        corp_code, bsns_year, rcept_no = row["corp_code"], int(row["bsns_year"]), row["rcept_no"]
        print(f"[{i + 1}/{len(targets)}] {corp_code} {bsns_year} (rcept_no={rcept_no})", end=" ")
        try:
            zip_bytes = fetch_zip(rcept_no)
            original_accounts = parse_original_values(zip_bytes, bsns_year)
        except Exception as exc:
            print(f"실패: {exc}")
            rows.append({
                "corp_code": corp_code, "bsns_year": bsns_year, "rcept_no": rcept_no,
                "status": f"오류: {exc}",
            })
            continue

        n_fields_found = sum(1 for v in original_accounts.values() if v is not None)
        if n_fields_found == 0:
            print("실패: 12개 필드 전부 매칭 안 됨")
            rows.append({
                "corp_code": corp_code, "bsns_year": bsns_year, "rcept_no": rcept_no,
                "status": "필드 매칭 실패",
            })
            time.sleep(0.2)
            continue

        try:
            devs = compute_live_deviations(corp_code, bsns_year, original_accounts, engine)
        except Exception as exc:
            print(f"실패(편차계산): {exc}")
            rows.append({
                "corp_code": corp_code, "bsns_year": bsns_year, "rcept_no": rcept_no,
                "status": f"편차계산 오류: {exc}",
            })
            time.sleep(0.2)
            continue

        composite_original, top = rank_top_contributors(devs)

        cur = current_flags[
            (current_flags["corp_code"] == corp_code) & (current_flags["bsns_year"] == bsns_year)
        ]
        composite_current = float(cur.iloc[0]["composite_score"]) if not cur.empty else None

        rows.append({
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "rcept_no": rcept_no,
            "status": "정상",
            "n_fields_found": n_fields_found,
            "composite_current": composite_current,
            "composite_original": composite_original,
            "is_flagged_original": int(composite_original is not None and composite_original >= threshold),
            "original_accounts_json": json.dumps(original_accounts),
            "top_contributors_original": json.dumps([(r, d["deviation"]) for r, d in top]) if composite_original is not None else None,
        })
        print(f"현재스코어={composite_current} 원본스코어={composite_original}")
        time.sleep(0.2)

    out = pd.DataFrame(rows)
    out.to_csv("pilot_step2_results.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: pilot_step2_results.csv ({len(out)}건)")

    ok = out[out["status"] == "정상"]
    print(f"\n정상 처리: {len(ok)}/{len(out)}건")
    if not ok.empty:
        print(f"원본 스코어 >= 현재 스코어: {(ok['composite_original'] >= ok['composite_current']).sum()}건")
        print(f"원본 기준으로도 플래그 유지(is_flagged_original=1): {ok['is_flagged_original'].sum()}건 / {len(ok)}건")


if __name__ == "__main__":
    main()

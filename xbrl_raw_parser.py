"""§17 파일럿: OpenDART 원본(정정 전) XBRL zip 파서

`fnlttSinglAcntAll`(JSON API, collect_financials.py가 씀)은 (corp_code, 연도) 키라
최신(정정 반영) 값만 준다. 반면 `fnlttXbrl.xml`은 `rcept_no`(접수번호) 키라 그 접수건이
실제 제출된 시점의 원본 XBRL을 준다 — LIMITATIONS.md §17.

원본 XBRL(zip 안 *.xbrl 인스턴스 문서)은 JSON API와 태그 형식이 다르다:
- JSON: account_id="ifrs-full_Revenue" (sj_div로 BS/IS/CIS/CF 구분)
- XBRL 원문: <ifrs-full:Revenue contextRef="...">값</ifrs-full:Revenue>
  contextRef가 "어느 시점·연결/별도·세그먼트인지"를 가리킴 — 같은 태그가 세그먼트별
  (지역별·부문별) 분해값까지 수십 건씩 나오므로, "연결 전사 합계, 당해 사업연도" 딱
  하나의 context만 골라야 한다.

이 모듈은 xbrl_mapping.ACCOUNT_CANDIDATES와 완전히 동일한 fallback 우선순위 로직을
원본 XBRL에 대해 재사용한다 — compute_ratios.py의 load_fields()와 대응.
"""
import re
import zipfile

from xbrl_mapping import ACCOUNT_CANDIDATES

# sj_div → 필요한 기간 유형 (BS=시점, 나머지=기간)
INSTANT_TYPES = {"BS"}

# DART 원본 XBRL은 신고연도에 따라 taxonomy 버전이 갈려 컨테이너 형식이 두 가지다:
#   신형(2024 taxonomy 기준, 2023 사업연도분부터 확인): 루트가 xbrli: 네임스페이스 접두사를
#     쓰고, 차원 정보는 <xbrli:entity><xbrli:segment>에 들어간다.
#   구형(2019 taxonomy, 2021~2022 사업연도분에서 확인): 접두사 없는 <context>/<period>를
#     쓰고, 차원 정보는 <scenario>에 들어간다.
# id 네이밍 규칙(CFY2024dFY_... 등)과 계정 태그 접두사(ifrs-full:, dart:)는 두 형식에서
# 동일해 그 부분은 그대로 재사용 가능 — context/period 태그의 xbrli: 접두사 유무만 다르다.
CONTEXT_RE = re.compile(r'<(?:xbrli:)?context id="([^"]+)">(.*?)</(?:xbrli:)?context>', re.S)
SEGMENT_MEMBER_RE = re.compile(r'<xbrldi:explicitMember dimension="[^"]+">([^<]+)</xbrldi:explicitMember>')
DURATION_RE = re.compile(r'<(?:xbrli:)?startDate>([^<]+)</(?:xbrli:)?startDate>\s*<(?:xbrli:)?endDate>([^<]+)</(?:xbrli:)?endDate>')
INSTANT_RE = re.compile(r'<(?:xbrli:)?instant>([^<]+)</(?:xbrli:)?instant>')


def _find_consolidated_contexts(xbrl_text: str, bsns_year: int, want_instant: bool) -> list[str]:
    """세그먼트 멤버가 정확히 1개 && 그 값이 'ConsolidatedMember'로 끝나며, 기간이
    당해 사업연도(bsns_year, 12월 결산 가정)와 일치하는 context id 전부를 찾는다.
    같은 회사가 같은 개념을 ifrs-full 축과 dart-gcd 축 등 서로 다른 dimension으로
    중복 표현하는 경우가 있어(구형 taxonomy에서 관찰됨), 실제 계정 태그가 어느 축의
    context를 참조하는지는 미리 알 수 없다 — 후보를 전부 반환해 호출부에서 순서대로
    시도한다. ifrs-full 축이 표준이라 먼저 오도록 정렬."""
    target_start, target_end = f"{bsns_year}-01-01", f"{bsns_year}-12-31"
    candidates = []
    for cid, body in CONTEXT_RE.findall(xbrl_text):
        members = SEGMENT_MEMBER_RE.findall(body)
        if len(members) != 1 or not members[0].endswith(":ConsolidatedMember"):
            continue
        if want_instant:
            m = INSTANT_RE.search(body)
            if m and m.group(1) == target_end:
                candidates.append(cid)
        else:
            m = DURATION_RE.search(body)
            if m and m.group(1) == target_start and m.group(2) == target_end:
                candidates.append(cid)
    candidates.sort(key=lambda cid: 0 if "ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis" in cid else 1)
    return candidates


def _find_fact_value(xbrl_text: str, tag: str, context_ids: list[str]) -> float | None:
    pat = re.compile(r"<" + re.escape(tag) + r'\b([^>]*)>([^<]*)</' + re.escape(tag) + r">")
    facts = {}
    for attrs, val in pat.findall(xbrl_text):
        m = re.search(r'contextRef="([^"]+)"', attrs)
        if m and val.strip() != "":
            facts.setdefault(m.group(1), val)
    for cid in context_ids:
        if cid in facts:
            try:
                return float(facts[cid])
            except ValueError:
                continue
    return None


def _account_id_to_tag(account_id: str) -> str:
    """'ifrs-full_Revenue' -> 'ifrs-full:Revenue', 'dart_OperatingIncomeLoss' -> 'dart:OperatingIncomeLoss'"""
    prefix, local = account_id.split("_", 1)
    return f"{prefix}:{local}"


def parse_original_values(zip_bytes: bytes, bsns_year: int) -> dict:
    """zip 안 *.xbrl 인스턴스 문서에서 ACCOUNT_CANDIDATES 12개 필드를 원본 값으로 추출.
    반환: {field: value or None}. 매칭 실패 필드는 None(compute_ratios.py와 동일하게
    확신 없는 매핑은 결측 처리)."""
    with zipfile.ZipFile(__import__("io").BytesIO(zip_bytes)) as z:
        xbrl_names = [n for n in z.namelist() if n.endswith(".xbrl")]
        if not xbrl_names:
            raise ValueError("zip 안에 .xbrl 인스턴스 문서가 없음")
        xbrl_text = z.read(xbrl_names[0]).decode("utf-8", errors="replace")

    ctx_duration = _find_consolidated_contexts(xbrl_text, bsns_year, want_instant=False)
    ctx_instant = _find_consolidated_contexts(xbrl_text, bsns_year, want_instant=True)

    result = {}
    for field, candidates in ACCOUNT_CANDIDATES.items():
        value = None
        for sj_div, account_id in candidates:
            ctxs = ctx_instant if sj_div in INSTANT_TYPES else ctx_duration
            if not ctxs:
                continue
            tag = _account_id_to_tag(account_id)
            value = _find_fact_value(xbrl_text, tag, ctxs)
            if value is not None:
                break
        result[field] = value
    return result

"""사업보고서 [기재정정] 라벨 정제 — 재무제표·감사의견 관련 정정만 판별 (LIMITATIONS.md §5)

DART 정정 문서는 CORRECTION 섹션 안에 두 템플릿 중 하나로 정정 내역을 적는다:
  - 템플릿 A (단일 서류 정정, 주로 감사보고서 단독 재발행): 자유서술
    "1. 정정대상 공시서류 : ..." / "3. 정정사유 : ..." 형태
  - 템플릿 B (항목별 표): 각 행 첫 컬럼(항목)이 사업보고서 표준 목차 챕터명으로 시작한다.

판단 규칙 (LLM 미사용 — CLAUDE.md §1 원칙: 판단은 통계/규칙, LLM은 서술만):
  - 템플릿 B: 정정 항목 텍스트에 챕터 제목 문구("재무에 관한 사항" 또는 "감사인의
    감사의견")가 포함되면 재무 관련으로 판정. **로마숫자 번호(I~XI)가 아니라 제목
    문구로 판정한다** — 실측 결과 (1) 일부 문서는 로마숫자를 유니코드 글자(Ⅲ, ⅰ 등)로
    표기해 ASCII 정규식이 놓치고, (2) 드물게 필터 자체가 번호를 오기재한 사례
    (예: "Ⅲ. 임원 및 직원 등에 관한 사항" — 번호는 재무 챕터인데 내용은 임원 챕터)가
    있어 번호보다 제목 문구가 더 신뢰할 수 있는 신호로 확인됨.
  - 템플릿 A: "정정대상 공시서류"에 재무제표·감사보고서·감사의견·주석 키워드가 있으면
    재무 관련으로 판정.
  - 둘 다 해당 없으면(예: 첨부 누락, 표지 오타, 지배구조·주주·임원 관련 정정,
    최대주주 소속회사의 재무현황 등) 비재무로 판정.

한계: 규칙 기반 초기 버전이며 오탐/누락 가능(LIMITATIONS.md §13과 동일한 성격의 한계).
"""
import re

FINANCIAL_PHRASES_NOSPACE = ("재무에관한사항", "감사인의감사의견")
TARGET_DOC_KEYWORDS = ("재무제표", "감사보고서", "감사의견", "주석")

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
TR_RE = re.compile(r"<TR[^>]*>(.*?)</TR>", re.S)
TD_RE = re.compile(r"<TD[^>]*>(.*?)</TD>", re.S)
CORRECTION_RE = re.compile(r"<CORRECTION>(.*?)</CORRECTION>", re.S)


def _clean(cell: str) -> str:
    return TAG_RE.sub("", cell).replace("&cr;", " ").strip()


def _nospace(s: str) -> str:
    return WS_RE.sub("", s)


def classify(document_text: str) -> dict:
    """document.xml(압축 해제된 원문 텍스트)에서 CORRECTION 섹션을 찾아 재무 관련
    정정 여부를 판정한다.

    반환 dict:
      is_financial_related : bool
      target_doc            : 템플릿 A "정정대상 공시서류" 텍스트 (없으면 None)
      reason_text           : 템플릿 A "정정사유" 자유서술 텍스트 (없으면 None)
      items                 : 템플릿 B에서 재무 관련 문구에 매칭된 항목명 목록
      found_correction       : CORRECTION 섹션 자체를 찾았는지 (False면 파싱 실패 의심)
    """
    m = CORRECTION_RE.search(document_text)
    if not m:
        return {
            "is_financial_related": False, "target_doc": None,
            "reason_text": None, "items": [], "found_correction": False,
        }
    block = m.group(1)
    plain = _clean(block)

    target_doc = None
    tm = re.search(r"정정대상\s*공시서류\s*[:：]\s*([^\n]+)", plain)
    if tm:
        target_doc = tm.group(1).strip()[:300]

    reason_text = None
    rm = re.search(r"정정사유\s*[:：]\s*([^\n]+)", plain)
    if rm:
        reason_text = rm.group(1).strip()[:500]

    items = []
    for tr in TR_RE.findall(block):
        tds = TD_RE.findall(tr)
        if not tds:
            continue
        item = _clean(tds[0])
        if not item:
            continue
        if any(p in _nospace(item) for p in FINANCIAL_PHRASES_NOSPACE):
            items.append(item[:200])

    is_financial = bool(items)
    if target_doc and any(k in target_doc for k in TARGET_DOC_KEYWORDS):
        is_financial = True

    return {
        "is_financial_related": is_financial,
        "target_doc": target_doc,
        "reason_text": reason_text,
        "items": items,
        "found_correction": True,
    }

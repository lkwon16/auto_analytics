"""이벤트 카테고리 규칙 — 모듈⑤ 공용
report_nm 키워드 매칭으로 수시공시를 카테고리화하고, 각 카테고리가 어떤 재무비율
편차와 관련 있을 가능성이 높은지 매핑한다. (규칙 기반 — LLM 미사용, CLAUDE.md §1 원칙)
"""

EVENT_CATEGORIES: dict[str, list[str]] = {
    "자금조달": ["유상증자", "무상증자", "전환사채", "신주인수권부사채", "교환사채", "자기주식"],
    "소송": ["소송"],
    "영업정지_제재": ["영업정지", "행정처분", "과징금"],
    "M&A_구조조정": ["합병", "분할", "영업양수", "영업양도", "자산양수", "자산양도", "타법인주식및출자증권"],
    "감사관련": ["감사인", "감사의견", "감사보고서", "외부감사"],
    "지배구조변경": ["대표이사", "최대주주", "임원"],
    "부실징후": ["부도", "회생절차", "파산", "채무불이행", "관리종목", "상장폐지", "불성실공시"],
}

# 재무비율 -> 관련 가능성 높은 이벤트 카테고리 (모듈④ 편차 원인 후보 탐색용)
RATIO_TO_CATEGORIES: dict[str, list[str]] = {
    "debt_ratio": ["자금조달", "부실징후"],
    "interest_coverage": ["자금조달", "부실징후"],
    "total_accruals_ratio": ["감사관련", "부실징후", "M&A_구조조정"],
    "oi_cfo_gap_ratio": ["감사관련", "부실징후", "M&A_구조조정"],
    "revenue_growth": ["M&A_구조조정", "영업정지_제재"],
    "gp_margin": ["M&A_구조조정", "영업정지_제재"],
    "operating_margin": ["M&A_구조조정", "영업정지_제재"],
    "sga_ratio": ["M&A_구조조정", "영업정지_제재"],
    "receivables_turnover": ["M&A_구조조정", "영업정지_제재"],
    "inventory_turnover": ["M&A_구조조정", "영업정지_제재"],
}


def categorize(report_nm: str) -> list[str]:
    """report_nm에 매칭되는 카테고리 전부 반환 (중복 매칭 가능, 예: 자금조달+M&A)."""
    if not report_nm:
        return []
    matched = []
    for category, keywords in EVENT_CATEGORIES.items():
        if any(kw in report_nm for kw in keywords):
            matched.append(category)
    return matched

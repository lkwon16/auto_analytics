"""동종그룹(peer group) 구성 함수 — 모듈②
KSIC 중분류(2자리)로 묶되, 표본이 부족하면 대분류(section)로 fallback한다.
"""
from sqlalchemy import create_engine, text

from config import DB_URL

# KSIC(10차) 대분류 — (시작 중분류, 끝 중분류, 섹션코드)
KSIC_SECTIONS = [
    (1, 3, "A"), (5, 8, "B"), (10, 34, "C"), (35, 35, "D"), (36, 39, "E"),
    (41, 42, "F"), (45, 47, "G"), (49, 52, "H"), (55, 56, "I"), (58, 63, "J"),
    (64, 66, "K"), (68, 68, "L"), (70, 73, "M"), (74, 76, "N"), (84, 84, "O"),
    (85, 85, "P"), (86, 87, "Q"), (90, 91, "R"), (94, 96, "S"), (97, 98, "T"),
    (99, 99, "U"),
]


def get_division(industry_code: str) -> str | None:
    """업종코드 앞 2자리 = KSIC 중분류"""
    if not industry_code or len(industry_code) < 2:
        return None
    return industry_code[:2]


def get_section(division: str) -> str | None:
    """중분류 → 대분류(section) 변환"""
    if division is None or not division.isdigit():
        return None
    d = int(division)
    for lo, hi, section in KSIC_SECTIONS:
        if lo <= d <= hi:
            return section
    return None


def get_peer_group(corp_code: str, engine=None, min_peers: int = 10) -> dict:
    """analysis_universe 내에서 peer group을 구성한다.
    반환: {"level": "division"|"section"|"none", "division": str, "section": str,
           "peers": [corp_code, ...]}  (peers에는 자기 자신 제외)
    """
    engine = engine or create_engine(DB_URL)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT industry_code FROM analysis_universe WHERE corp_code = :cc"),
            {"cc": corp_code},
        ).fetchone()
        if row is None:
            raise ValueError(f"analysis_universe에 없는 기업: {corp_code}")
        industry_code = row[0]

        division = get_division(industry_code)
        section = get_section(division)

        if division is not None:
            peers = conn.execute(
                text(
                    "SELECT corp_code FROM analysis_universe "
                    "WHERE substr(industry_code, 1, 2) = :div AND corp_code != :cc"
                ),
                {"div": division, "cc": corp_code},
            ).fetchall()
            peers = [p[0] for p in peers]
            if len(peers) >= min_peers:
                return {"level": "division", "division": division, "section": section, "peers": peers}

        if section is not None:
            # 같은 section에 속하는 모든 중분류 코드 목록
            lo, hi = next((lo, hi) for lo, hi, s in KSIC_SECTIONS if s == section)
            all_codes = conn.execute(
                text("SELECT corp_code, industry_code FROM analysis_universe WHERE corp_code != :cc"),
                {"cc": corp_code},
            ).fetchall()
            peers = [
                cc for cc, ic in all_codes
                if ic and len(ic) >= 2 and ic[:2].isdigit() and lo <= int(ic[:2]) <= hi
            ]
            return {"level": "section", "division": division, "section": section, "peers": peers}

    return {"level": "none", "division": division, "section": section, "peers": []}


if __name__ == "__main__":
    import sys
    engine = create_engine(DB_URL)
    with engine.connect() as conn:
        sample = conn.execute(text("SELECT corp_code, corp_name FROM analysis_universe LIMIT 5")).fetchall()
    for corp_code, corp_name in sample:
        result = get_peer_group(corp_code, engine)
        print(f"{corp_name}({corp_code}): level={result['level']}, "
              f"division={result['division']}, peers={len(result['peers'])}개")

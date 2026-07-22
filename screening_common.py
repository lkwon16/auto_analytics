"""스크리닝 도구(⑧, growth_screener.py 및 이후 조건 모듈들)의 공용 헬퍼.

감사 모집단(analysis_universe) 제한 없이 상장사 전체를 대상으로 XBRL 계정을
추출하는 로직과 업종 분류 로직은 스크리닝 조건이 늘어날 때마다 반복되므로
여기에 모아 재사용한다. 감사 모듈(compute_ratios.py 등)과는 별개 코드 경로다.
"""
import pandas as pd
from sqlalchemy import text

from peer_group import get_division, get_section

# KSIC(10차) 대분류 코드 -> 한글 이름. 구간 정의(peer_group.KSIC_SECTIONS) 자체는
# 코드에서 가져오므로 여기서는 이름만 붙인다.
KSIC_SECTION_NAMES = {
    "A": "농업·임업·어업", "B": "광업", "C": "제조업",
    "D": "전기·가스·증기·공기조절 공급업", "E": "수도·하수·폐기물처리·원료재생업",
    "F": "건설업", "G": "도매·소매업", "H": "운수·창고업", "I": "숙박·음식점업",
    "J": "정보통신업", "K": "금융·보험업", "L": "부동산업",
    "M": "전문·과학·기술서비스업", "N": "사업시설관리·사업지원·임대서비스업",
    "O": "공공행정·국방·사회보장행정", "P": "교육서비스업",
    "Q": "보건업·사회복지서비스업", "R": "예술·스포츠·여가관련서비스업",
    "S": "협회·단체·수리·개인서비스업", "T": "가구내 고용활동·자가소비 생산활동",
    "U": "국제·외국기관",
}


def section_of(industry_code: str | None) -> str | None:
    return get_section(get_division(industry_code))


def fetch_account(engine, candidates, value_col: str) -> pd.DataFrame:
    """CFS 기준 계정 후보(candidates, [(sj_div, account_id), ...]) 중 fallback
    우선순위 첫 매치만 채택해 corp_code/bsns_year별 값을 반환한다."""
    cand_sql = " OR ".join(
        f"(sj_div = :sj{i} AND account_id = :ac{i})" for i in range(len(candidates))
    )
    params = {}
    for i, (sj, ac) in enumerate(candidates):
        params[f"sj{i}"] = sj
        params[f"ac{i}"] = ac

    with engine.connect() as conn:
        raw = pd.read_sql(
            text(
                f"SELECT corp_code, bsns_year, sj_div, account_id, thstrm_amount "
                f"FROM financial_statements WHERE fs_div = 'CFS' AND ({cand_sql})"
            ),
            conn, params=params,
        )

    if raw.empty:
        return pd.DataFrame(columns=["corp_code", "bsns_year", value_col])

    priority = {(sj, ac): i for i, (sj, ac) in enumerate(candidates)}
    raw["_priority"] = raw.apply(lambda r: priority.get((r["sj_div"], r["account_id"]), 999), axis=1)
    raw = raw.sort_values("_priority").drop_duplicates(subset=["corp_code", "bsns_year"], keep="first")
    return raw[["corp_code", "bsns_year", "thstrm_amount"]].rename(columns={"thstrm_amount": value_col})


def fetch_listed_corps(engine) -> pd.DataFrame:
    """상장사 전체(corp_master.stock_code IS NOT NULL). 감사 모집단 제한 없음."""
    with engine.connect() as conn:
        return pd.read_sql(
            text("SELECT corp_code, corp_name, industry_code FROM corp_master WHERE stock_code IS NOT NULL"),
            conn,
        )


def with_section(df: pd.DataFrame) -> pd.DataFrame:
    """industry_code 컬럼이 있는 DataFrame에 section/section_name 컬럼을 붙인다."""
    df = df.copy()
    df["section"] = df["industry_code"].apply(section_of)
    df["section_name"] = df["section"].map(KSIC_SECTION_NAMES)
    return df

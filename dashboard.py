"""분석적 절차 자동화 대시보드 (모듈⑦, STEP 5)

화면 (1) 기업 조회: 비율 추이 + peer 중위수 비교
화면 (2) 플래그 목록: 종합 스코어 상위 + 편차 3개 + DART 공시 링크

결과를 해석하기 전에 LIMITATIONS.md를 반드시 함께 확인할 것
(특히 §10 비율 극단값, §12 백테스트 Lift가 낮은 근본 원인).

실행: py -m streamlit run dashboard.py
"""
import pandas as pd
import streamlit as st
from sqlalchemy import create_engine, text

from config import DB_URL
from peer_group import get_peer_group
from match_signals import get_candidate_signals
from collect_news import collect_news_for_company

PBLNTF_TY_LABELS = {"B": "주요사항보고", "F": "외부감사관련", "I": "거래소공시"}

RATIO_LABELS = {
    "receivables_turnover": "매출채권회전율",
    "inventory_turnover": "재고자산회전율",
    "gp_margin": "매출총이익률",
    "operating_margin": "영업이익률",
    "sga_ratio": "판관비율",
    "debt_ratio": "부채비율",
    "interest_coverage": "이자보상배율",
    "total_accruals_ratio": "총발생액/총자산",
    "oi_cfo_gap_ratio": "(영업이익-영업CF)/총자산",
    "revenue_growth": "매출액증가율",
}
RATIO_COLS = list(RATIO_LABELS.keys())

# DART 전자공시 개별 공시서류 뷰어 (rcept_no 필요)
DART_VIEWER_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


@st.cache_resource
def get_engine():
    return create_engine(DB_URL)


@st.cache_data
def load_universe(_engine) -> pd.DataFrame:
    return pd.read_sql(
        text("SELECT corp_code, corp_name, industry_code FROM analysis_universe ORDER BY corp_name"),
        _engine,
    )


@st.cache_data
def load_ratios(_engine, corp_codes: tuple[str, ...]) -> pd.DataFrame:
    if not corp_codes:
        return pd.DataFrame(columns=["corp_code", "bsns_year", *RATIO_COLS])
    placeholders = ",".join(f":c{i}" for i in range(len(corp_codes)))
    params = {f"c{i}": cc for i, cc in enumerate(corp_codes)}
    return pd.read_sql(
        text(f"SELECT corp_code, bsns_year, {', '.join(RATIO_COLS)} FROM ratios WHERE corp_code IN ({placeholders})"),
        _engine, params=params,
    )


@st.cache_data
def load_flags(_engine) -> pd.DataFrame:
    query = """
        SELECT f.corp_code, u.corp_name, f.bsns_year, f.composite_score, f.n_ratios_used,
               f.top1_ratio, f.top1_deviation, f.top1_direction,
               f.top2_ratio, f.top2_deviation, f.top2_direction,
               f.top3_ratio, f.top3_deviation, f.top3_direction
        FROM flags f
        JOIN analysis_universe u ON u.corp_code = f.corp_code
        WHERE f.is_flagged = 1
        ORDER BY f.composite_score DESC
    """
    return pd.read_sql(text(query), _engine)


@st.cache_data
def load_latest_disclosures(_engine, corp_codes: tuple[str, ...]) -> dict:
    """기업별 가장 최근 공시의 rcept_no (DART 링크용)."""
    if not corp_codes:
        return {}
    placeholders = ",".join(f":c{i}" for i in range(len(corp_codes)))
    params = {f"c{i}": cc for i, cc in enumerate(corp_codes)}
    df = pd.read_sql(
        text(f"SELECT corp_code, rcept_no, rcept_dt FROM disclosures WHERE corp_code IN ({placeholders})"),
        _engine, params=params,
    )
    if df.empty:
        return {}
    latest = df.sort_values("rcept_dt").groupby("corp_code").tail(1)
    return dict(zip(latest["corp_code"], latest["rcept_no"]))


def render_company_view(engine, universe: pd.DataFrame):
    name_query = st.text_input("회사명 검색", "")
    matched = (
        universe[universe["corp_name"].str.contains(name_query, case=False, na=False)]
        if name_query else universe
    )
    if matched.empty:
        st.info("검색 결과가 없습니다.")
        return

    options = {f"{r.corp_name} ({r.corp_code})": r.corp_code for r in matched.itertuples()}
    selected_label = st.selectbox("기업 선택", list(options.keys()))
    corp_code = options[selected_label]

    peer_info = get_peer_group(corp_code, engine)
    peers = peer_info["peers"]
    st.caption(f"peer 그룹: {peer_info['level']} 기준 {len(peers)}개사")

    own_ratios = load_ratios(engine, (corp_code,)).sort_values("bsns_year")
    if own_ratios.empty:
        st.warning("이 기업의 비율 데이터가 없습니다.")
        return
    peer_ratios = load_ratios(engine, tuple(peers))

    for ratio in RATIO_COLS:
        own_series = own_ratios.set_index("bsns_year")[ratio]
        peer_median = (
            peer_ratios.groupby("bsns_year")[ratio].median() if not peer_ratios.empty
            else pd.Series(dtype=float)
        )
        chart_df = pd.DataFrame({"자사": own_series, "peer 중위수": peer_median})
        st.subheader(RATIO_LABELS[ratio])
        st.line_chart(chart_df)


def render_flag_list(engine, universe: pd.DataFrame):
    flags = load_flags(engine)
    if flags.empty:
        st.info("플래그된 기업이 없습니다.")
        return

    st.caption(f"전체 플래그 {len(flags):,}건 (종합 스코어 상위 10% 기준) 중 회사명으로 검색. "
               "스코어 극단값 해석은 `LIMITATIONS.md` §10 참고.")
    name_query = st.text_input("회사명 검색", "", key="flag_name_query")

    if not name_query:
        st.info("회사명을 검색하면 해당 기업의 플래그만 표시됩니다.")
        return

    shown = flags[flags["corp_name"].str.contains(name_query, case=False, na=False)]
    if shown.empty:
        in_universe = universe["corp_name"].str.contains(name_query, case=False, na=False).any()
        if in_universe:
            st.info(f"'{name_query}'은(는) 분석 모집단에 있으나, 현재 편차 임계값(상위 10%) "
                    "플래그 대상은 아닙니다.")
        else:
            st.info(f"'{name_query}' 검색 결과가 없습니다 (분석 모집단에 없는 기업일 수 있습니다).")
        return

    st.write(f"'{name_query}' 검색 결과 {len(shown):,}건")
    shown = shown.sort_values("composite_score", ascending=False)

    latest_disclosures = load_latest_disclosures(engine, tuple(shown["corp_code"].unique()))

    for row in shown.itertuples():
        with st.expander(f"{row.corp_name} ({row.bsns_year}) — 종합 스코어 {row.composite_score:.2f}"):
            top_ratios = []
            for i in (1, 2, 3):
                ratio_col = getattr(row, f"top{i}_ratio")
                if not ratio_col:
                    continue
                top_ratios.append(ratio_col)
                deviation = getattr(row, f"top{i}_deviation")
                direction = getattr(row, f"top{i}_direction")
                label = RATIO_LABELS.get(ratio_col, ratio_col)
                st.write(f"{i}. **{label}** — 편차 {deviation:.2f} ({direction})")

            rcept_no = latest_disclosures.get(row.corp_code)
            if rcept_no:
                st.markdown(f"[DART 공시 보기]({DART_VIEWER_URL.format(rcept_no=rcept_no)})")

            render_candidate_signals(engine, row.corp_code, row.corp_name, int(row.bsns_year), top_ratios)


def render_candidate_signals(engine, corp_code: str, corp_name: str, bsns_year: int, top_ratios: list[str]):
    """모듈⑤ — 편차 원인 후보(수시공시·뉴스)를 회계연도 윈도우 기준으로 보여준다.
    시점 매칭 설계는 match_signals.py 상단 주석(LIMITATIONS.md §8) 참고."""
    st.markdown("**편차 원인 후보 (모듈⑤)**")
    st.caption("회계연도 1/1~12/31(concurrent) + 익년 1/1~4/30(post_year) 윈도우 내 후보. "
               "★관련 = 편차 상위 비율과 카테고리가 매칭됨(규칙 기반, LIMITATIONS.md §13 참고).")

    result = get_candidate_signals(corp_code, bsns_year, engine, top_ratios)
    events = result["events"]
    news = result["news"]

    if events.empty:
        st.write("수시공시 후보 없음 (또는 collect_disclosure_events.py 미실행)")
    else:
        for r in events.itertuples():
            tag = " ⭐관련" if r.relevant else ""
            ty_label = PBLNTF_TY_LABELS.get(r.pblntf_ty, r.pblntf_ty)
            st.write(f"- {r.rcept_dt} [{r.window}/{ty_label}] {r.report_nm} ({r.categories}){tag}")

    if news.empty:
        st.caption(f"뉴스 후보 없음 — 이 기업 뉴스는 아직 미수집일 수 있습니다 "
                   "(네이버 뉴스 API는 날짜 범위 필터가 없어 오래된 기사는 누락될 수 있음, LIMITATIONS.md §14).")
        if st.button(f"{corp_name} 뉴스 조회하기", key=f"news_{corp_code}_{bsns_year}"):
            n = collect_news_for_company(corp_code, corp_name, engine)
            st.success(f"신규 뉴스 {n}건 수집 완료. 다시 펼쳐서 확인하세요.")
            st.rerun()
    else:
        for r in news.itertuples():
            st.write(f"- {r.pub_date} [{r.window}] [{r.title}]({r.link})")


def main():
    st.set_page_config(page_title="분석적 절차 자동화", layout="wide")
    st.title("AUTO 분석적 절차")
    st.caption("ISA 520 기반 MVP — 결과 해석 전 LIMITATIONS.md를 함께 확인하세요.")

    engine = get_engine()
    universe = load_universe(engine)

    tab1, tab2 = st.tabs(["기업 조회", "플래그 목록"])
    with tab1:
        render_company_view(engine, universe)
    with tab2:
        render_flag_list(engine, universe)


if __name__ == "__main__":
    main()

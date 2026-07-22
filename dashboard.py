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
from growth_screener import compute_revenue_growth
from turnaround_screener import compute_turnaround
from leverage_screener import compute_debt_ratio_change
from cash_quality_screener import compute_cash_quality
from screening_common import fetch_listed_corps, with_section
from live_override import ACCOUNT_FIELDS, ACCOUNT_LABELS, compute_live_deviations
from detect_flags import rank_top_contributors

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


@st.cache_data
def load_growth(_engine) -> pd.DataFrame:
    return compute_revenue_growth(_engine)


@st.cache_data
def load_turnaround(_engine) -> pd.DataFrame:
    return compute_turnaround(_engine)


@st.cache_data
def load_leverage(_engine) -> pd.DataFrame:
    return compute_debt_ratio_change(_engine)


@st.cache_data
def load_cash_quality(_engine) -> pd.DataFrame:
    return compute_cash_quality(_engine)


@st.cache_data
def load_unified_screening(_engine) -> pd.DataFrame:
    """4개 스크리닝 조건(성장·흑자전환·부채비율·이익의 질)을 corp_code+bsns_year
    기준으로 outer join해 하나의 표로 합친다. 조건별 원천 데이터 커버리지가 달라
    (예: 매출은 있어도 부채/자본 계정이 없는 기업) 값이 없는 조건은 NaN으로 남고,
    그 조건을 활성화하면 자연히 걸러진다(조건 미충족과 동일하게 취급)."""
    g = load_growth(_engine)[[
        "corp_code", "bsns_year", "revenue", "growth", "is_extreme",
        "consecutive_growth_years", "margin_improved",
    ]].rename(columns={"is_extreme": "growth_extreme"})
    t = load_turnaround(_engine)[["corp_code", "bsns_year", "operating_income", "prev_operating_income", "is_turnaround"]]
    lev = load_leverage(_engine)[[
        "corp_code", "bsns_year", "debt_ratio", "prev_debt_ratio", "debt_ratio_change_pp", "is_extreme",
    ]].rename(columns={"is_extreme": "debt_extreme"})
    cq = load_cash_quality(_engine)[["corp_code", "bsns_year", "cfo_oi_ratio", "is_good_quality"]]

    merged = g.merge(t, on=["corp_code", "bsns_year"], how="outer") \
              .merge(lev, on=["corp_code", "bsns_year"], how="outer") \
              .merge(cq, on=["corp_code", "bsns_year"], how="outer")

    corps = with_section(fetch_listed_corps(_engine))
    merged = merged.merge(corps, on="corp_code", how="left")
    return merged


def render_unified_screener(engine):
    """통합 기업 스크리닝 (스크리닝 도구⑧). 4개 조건(매출 성장·흑자전환·부채비율
    급변·이익의 질)을 체크박스로 켜고 끄면서 AND 조건으로 동시에 조회한다 —
    조건별 탭을 따로 두지 않고 하나의 검색 바에서 조합하는 구조."""
    st.caption("감사 모집단 제한 없이 상장사 전체를 대상으로 합니다. 아래 조건 중 "
               "하나 이상을 켜면 그 조건들을 모두 만족하는 기업만 표시됩니다(AND).")

    df_all = load_unified_screening(engine)
    if df_all.empty:
        st.info("데이터가 없습니다.")
        return

    years = sorted(df_all["bsns_year"].unique(), reverse=True)
    col1, col2 = st.columns(2)
    with col1:
        year = st.selectbox("기준 연도", years, key="unified_year")
    with col2:
        section_options = ["전체"] + sorted(
            df_all.loc[df_all["section_name"].notna(), "section_name"].unique().tolist()
        )
        section_choice = st.selectbox("업종 (KSIC 대분류)", section_options, key="unified_section")

    shown = df_all[df_all["bsns_year"] == year].copy()
    if section_choice != "전체":
        shown = shown[shown["section_name"] == section_choice]

    active_labels = []
    show_cols = ["corp_name", "section_name"]

    st.markdown("**조건 선택**")
    c1, c2 = st.columns(2)
    with c1:
        use_growth = st.checkbox("매출 성장", key="use_growth")
        if use_growth:
            min_growth = st.slider("최소 매출 증가율(%)", -50, 500, 20, step=5, key="unified_min_growth")
            growth_extreme_ok = st.checkbox("극단값 포함", value=False, key="unified_growth_extreme")
            shown = shown[shown["growth"].notna() & (shown["growth"] >= min_growth / 100)]
            if not growth_extreme_ok:
                shown = shown[shown["growth_extreme"] != True]  # noqa: E712
            active_labels.append(f"매출증가율 {min_growth}%↑")
            show_cols += ["growth", "consecutive_growth_years"]

        use_turnaround = st.checkbox("흑자전환 (전기적자→당기흑자)", key="use_turnaround")
        if use_turnaround:
            shown = shown[shown["is_turnaround"] == True]  # noqa: E712
            active_labels.append("흑자전환")
            show_cols += ["operating_income", "prev_operating_income"]

    with c2:
        use_leverage = st.checkbox("부채비율 개선/악화", key="use_leverage")
        if use_leverage:
            direction = st.radio("방향", ["개선(감소)", "악화(증가)"], horizontal=True, key="unified_leverage_dir")
            leverage_extreme_ok = st.checkbox("극단값(자본잠식 등) 포함", value=False, key="unified_leverage_extreme")
            if direction == "개선(감소)":
                shown = shown[shown["debt_ratio_change_pp"].notna() & (shown["debt_ratio_change_pp"] < 0)]
            else:
                shown = shown[shown["debt_ratio_change_pp"].notna() & (shown["debt_ratio_change_pp"] > 0)]
            if not leverage_extreme_ok:
                shown = shown[shown["debt_extreme"] != True]  # noqa: E712
            active_labels.append(f"부채비율 {direction}")
            show_cols += ["debt_ratio", "debt_ratio_change_pp"]

        use_cash_quality = st.checkbox("이익의 질 우수 (영업CF > 영업이익)", key="use_cash_quality")
        if use_cash_quality:
            shown = shown[shown["is_good_quality"] == True]  # noqa: E712
            active_labels.append("이익의 질 우수")
            show_cols += ["cfo_oi_ratio"]

    if not active_labels:
        st.info("조건을 하나 이상 선택하세요.")
        return

    shown = shown.drop_duplicates(subset=["corp_code"])
    st.write(f"**{len(shown):,}개 기업** — {year}년, 업종: {section_choice}, 조건: {' + '.join(active_labels)}")

    if shown.empty:
        st.info("조건에 맞는 기업이 없습니다. 조건을 완화해 보세요.")
        return

    display = shown[show_cols].copy()
    col_labels = {"corp_name": "기업명", "section_name": "업종"}
    if "growth" in show_cols:
        display["growth"] = (shown["growth"] * 100).round(1).map(lambda v: f"{v:,.1f}%" if pd.notna(v) else "-")
        display["consecutive_growth_years"] = shown["consecutive_growth_years"]
        col_labels |= {"growth": "매출증가율", "consecutive_growth_years": "연속성장(년)"}
    if "operating_income" in show_cols:
        display["operating_income"] = shown["operating_income"].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "-")
        display["prev_operating_income"] = shown["prev_operating_income"].map(lambda v: f"{v:,.0f}" if pd.notna(v) else "-")
        col_labels |= {"operating_income": "당기영업이익", "prev_operating_income": "전기영업이익"}
    if "debt_ratio" in show_cols:
        display["debt_ratio"] = (shown["debt_ratio"] * 100).round(1).map(lambda v: f"{v:,.1f}%" if pd.notna(v) else "-")
        display["debt_ratio_change_pp"] = shown["debt_ratio_change_pp"].round(1).map(
            lambda v: f"{v:+,.1f}%p" if pd.notna(v) else "-"
        )
        col_labels |= {"debt_ratio": "부채비율", "debt_ratio_change_pp": "부채비율변화"}
    if "cfo_oi_ratio" in show_cols:
        display["cfo_oi_ratio"] = shown["cfo_oi_ratio"].map(lambda v: f"{v:,.1f}배" if pd.notna(v) else "-")
        col_labels |= {"cfo_oi_ratio": "CF/이익배율"}

    display = display.rename(columns=col_labels)
    st.dataframe(display, width="stretch", hide_index=True)


def render_live_override(engine, universe: pd.DataFrame):
    """실무용 당기(미확정) 실제값 입력 화면 (LIMITATIONS.md §11).
    peer·자사 전년도는 기존과 동일하게 DART 확정 데이터를 쓰고, 감사 중인 "당기"
    값만 회계사가 원본 계정값을 직접 입력해 편차를 계산한다."""
    st.caption("피감사회사의 아직 DART에 공시되지 않은 당기(미확정) 원본 재무자료를 "
               "직접 입력해 편차를 계산합니다. peer 그룹·자사 전년도는 기존 DART 확정 "
               "데이터를 그대로 사용합니다. 상세는 `LIMITATIONS.md` §11 참고.")

    name_query = st.text_input("회사명 검색", "", key="live_name_query")
    matched = (
        universe[universe["corp_name"].str.contains(name_query, case=False, na=False)]
        if name_query else universe
    )
    if matched.empty:
        st.info("검색 결과가 없습니다.")
        return

    options = {f"{r.corp_name} ({r.corp_code})": r.corp_code for r in matched.itertuples()}
    selected_label = st.selectbox("기업 선택", list(options.keys()), key="live_corp_select")
    corp_code = options[selected_label]

    own_ratios = load_ratios(engine, (corp_code,))
    default_year = int(own_ratios["bsns_year"].max()) + 1 if not own_ratios.empty else 2026
    bsns_year = st.number_input("당기 회계연도", min_value=2000, max_value=2100, value=default_year, step=1)

    if int(bsns_year) - 1 not in own_ratios["bsns_year"].tolist():
        st.warning(f"자사의 {int(bsns_year) - 1}년 확정 데이터가 없어 자사 전년도 기대치를 "
                   "사용할 수 없습니다(peer 기대치만으로 계산됩니다).")

    st.markdown("**당기 원본 계정값 입력 (원 단위)**")
    c1, c2 = st.columns(2)
    live_accounts = {}
    for i, field in enumerate(ACCOUNT_FIELDS):
        col = c1 if i % 2 == 0 else c2
        with col:
            v = st.number_input(ACCOUNT_LABELS.get(field, field), value=None, step=1_000_000,
                                 key=f"live_acc_{field}")
        if v is not None:
            live_accounts[field] = v

    if st.button("편차 계산", key="live_compute"):
        if not live_accounts:
            st.warning("최소 1개 이상 계정값을 입력하세요.")
            return
        devs = compute_live_deviations(corp_code, int(bsns_year), live_accounts, engine)
        if not devs:
            st.info("편차를 계산할 수 있는 비율이 없습니다 (입력값 부족, peer 표본 부족, "
                     "또는 §9 극단값 배제 기준 초과).")
            return
        composite_score, top = rank_top_contributors(devs)
        st.write(f"**종합 스코어: {composite_score:.2f}** (상위 {len(top)}개 비율 |편차| 평균)")
        for i, (ratio, v) in enumerate(top, start=1):
            label = RATIO_LABELS.get(ratio, ratio)
            st.write(f"{i}. **{label}** — 편차 {v['deviation']:.2f} ({v['direction']})")
        with st.expander("전체 비율 편차 보기"):
            for ratio, v in sorted(devs.items(), key=lambda kv: abs(kv[1]["deviation"]), reverse=True):
                label = RATIO_LABELS.get(ratio, ratio)
                st.write(f"- {label}: 편차 {v['deviation']:.2f} ({v['direction']})")


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
            tag = " ⭐관련" if r.relevant else ""
            st.write(f"- {r.pub_date} [{r.window}] [{r.title}]({r.link}){tag}")


def main():
    st.set_page_config(page_title="분석적 절차 자동화", layout="wide")
    st.title("AUTO 분석적 절차")
    st.caption("ISA 520 기반 MVP — 결과 해석 전 LIMITATIONS.md를 함께 확인하세요.")

    engine = get_engine()
    universe = load_universe(engine)

    tab1, tab2, tab3, tab4 = st.tabs(["기업 조회", "플래그 목록", "기업 스크리닝", "당기값 입력(실무용)"])
    with tab1:
        render_company_view(engine, universe)
    with tab2:
        render_flag_list(engine, universe)
    with tab3:
        render_unified_screener(engine)
    with tab4:
        render_live_override(engine, universe)


if __name__ == "__main__":
    main()

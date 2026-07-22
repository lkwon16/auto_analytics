"""편차 원인 후보 매칭 -> 모듈⑤ 핵심

flags 테이블의 (corp_code, bsns_year) 편차에 대해 disclosure_events(수시공시)와
news_signals(뉴스)를 시점 매칭해 원인 후보를 제시한다. 판단은 규칙 기반
(event_categories.py의 카테고리<->비율 매핑)이며, LLM은 쓰지 않는다(CLAUDE.md §1 원칙).

[시점 매칭 설계 — LIMITATIONS.md §8 재검토]
연간 비율 편차는 "회계연도 중 어느 시점"인지 특정할 수 없으므로, 편차 시점 ±N개월이
아니라 **회계연도 자체를 윈도우**로 잡는다:
  - concurrent: bsns_year 1/1 ~ 12/31 (편차를 만든 사건 후보)
  - post_year:  (bsns_year+1) 1/1 ~ 4/30 (사업보고서 제출 시한 전후 — 정정·감사의견
    이슈 등 회계연도 종료 후 드러나는 사건 후보)

뉴스는 온디맨드 조회 대상(collect_news.py)이라 이 함수 호출 시점에 아직 수집되지
않았을 수 있다 — 그 경우 disclosure_events만으로 결과를 반환한다.

단독 실행:
  python match_signals.py                          (flags 상위 1건을 예시 기업으로 사용)
  python match_signals.py <corp_code> <bsns_year>   (특정 기업·연도 지정)
"""
import sys
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text

from config import DB_URL
from event_categories import RATIO_TO_CATEGORIES, categorize

# Windows 콘솔(cp949)에서 특수문자(—, ★ 등) 출력 시 UnicodeEncodeError로 죽는 것을 방지
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _window(bsns_year: int) -> tuple[date, date, date, date]:
    concurrent_start = date(bsns_year, 1, 1)
    concurrent_end = date(bsns_year, 12, 31)
    post_start = date(bsns_year + 1, 1, 1)
    post_end = date(bsns_year + 1, 4, 30)
    return concurrent_start, concurrent_end, post_start, post_end


def _relevant_categories(top_ratios: list[str]) -> set[str]:
    cats: set[str] = set()
    for r in top_ratios:
        cats.update(RATIO_TO_CATEGORIES.get(r, []))
    return cats


def get_candidate_signals(
    corp_code: str, bsns_year: int, engine=None, top_ratios: list[str] | None = None
) -> dict:
    """반환: {"events": DataFrame, "news": DataFrame}
    각 DataFrame에는 window(concurrent/post_year) 컬럼과, top_ratios와 카테고리가
    겹치는지를 나타내는 relevant 컬럼이 붙고, 그 기준으로 정렬된다(관련 후보가 위로).
    events는 report_nm, news는 title+description에 키워드 카테고리 규칙을 적용한다."""
    engine = engine or create_engine(DB_URL)
    c_start, c_end, p_start, p_end = _window(bsns_year)
    relevant_cats = _relevant_categories(top_ratios or [])

    with engine.connect() as conn:
        events = pd.read_sql(
            text(
                "SELECT rcept_no, report_nm, rcept_dt, pblntf_ty, categories FROM disclosure_events "
                "WHERE corp_code = :cc AND ((rcept_dt BETWEEN :cs AND :ce) OR (rcept_dt BETWEEN :ps AND :pe)) "
                "ORDER BY rcept_dt"
            ),
            conn, params={"cc": corp_code, "cs": c_start, "ce": c_end, "ps": p_start, "pe": p_end},
        )
        news = pd.read_sql(
            text(
                "SELECT title, description, link, pub_date FROM news_signals "
                "WHERE corp_code = :cc AND ((pub_date BETWEEN :cs AND :ce) OR (pub_date BETWEEN :ps AND :pe)) "
                "ORDER BY pub_date"
            ),
            conn, params={"cc": corp_code, "cs": c_start, "ce": c_end, "ps": p_start, "pe": p_end},
        )

    if not events.empty:
        # SQLite에는 네이티브 DATE 타입이 없어 read_sql로 읽으면 문자열로 온다 -> date로 변환 필요
        events["rcept_dt"] = pd.to_datetime(events["rcept_dt"]).dt.date
        events["window"] = events["rcept_dt"].apply(lambda d: "concurrent" if c_start <= d <= c_end else "post_year")
        events["relevant"] = events["categories"].apply(
            lambda cs: any(c in relevant_cats for c in (cs.split(",") if cs else []))
        )
        events = events.sort_values(["relevant", "rcept_dt"], ascending=[False, True])

    if not news.empty:
        news["pub_date"] = pd.to_datetime(news["pub_date"]).dt.date
        news["window"] = news["pub_date"].apply(lambda d: "concurrent" if c_start <= d <= c_end else "post_year")
        # 뉴스 제목+본문에 공시와 동일한 키워드 카테고리 규칙(event_categories)을 적용.
        # report_nm(정형화된 공시명)과 달리 뉴스는 자유 문장이라 오탐 가능성이 더 높음
        # (LIMITATIONS.md §14 참고).
        news_categories = (news["title"] + " " + news["description"]).apply(categorize)
        news["relevant"] = news_categories.apply(lambda cs: any(c in relevant_cats for c in cs))
        news = news.sort_values(["relevant", "pub_date"], ascending=[False, True])

    return {"events": events, "news": news}


def _load_flag_row(engine, corp_code: str | None, bsns_year: int | None) -> pd.Series:
    """corp_code/bsns_year가 지정되면 해당 건, 아니면 flags 상위 1건을 반환."""
    if corp_code is not None:
        query = (
            "SELECT f.corp_code, u.corp_name, f.bsns_year, f.composite_score, "
            "f.top1_ratio, f.top2_ratio, f.top3_ratio "
            "FROM flags f JOIN analysis_universe u ON u.corp_code = f.corp_code "
            "WHERE f.corp_code = :cc"
        )
        params = {"cc": corp_code}
        if bsns_year is not None:
            query += " AND f.bsns_year = :yr"
            params["yr"] = bsns_year
        query += " ORDER BY f.bsns_year DESC LIMIT 1"
        with engine.connect() as conn:
            df = pd.read_sql(text(query), conn, params=params)
        if df.empty:
            raise SystemExit(f"flags 테이블에서 corp_code={corp_code}"
                              f"{f', bsns_year={bsns_year}' if bsns_year else ''} 건을 찾을 수 없습니다.")
        return df.iloc[0]

    with engine.connect() as conn:
        top_flag = pd.read_sql(
            text(
                "SELECT f.corp_code, u.corp_name, f.bsns_year, f.composite_score, "
                "f.top1_ratio, f.top2_ratio, f.top3_ratio "
                "FROM flags f JOIN analysis_universe u ON u.corp_code = f.corp_code "
                "WHERE f.is_flagged = 1 ORDER BY f.composite_score DESC LIMIT 1"
            ),
            conn,
        )
    if top_flag.empty:
        raise SystemExit("flags 테이블이 비어 있습니다. detect_flags.py를 먼저 실행하세요.")
    return top_flag.iloc[0]


def main():
    engine = create_engine(DB_URL)

    corp_code = sys.argv[1] if len(sys.argv) > 1 else None
    bsns_year = int(sys.argv[2]) if len(sys.argv) > 2 else None

    row = _load_flag_row(engine, corp_code, bsns_year)
    top_ratios = [r for r in (row.top1_ratio, row.top2_ratio, row.top3_ratio) if r]
    print(f"예시 기업: {row.corp_name}({row.corp_code}), {row.bsns_year}년, "
          f"종합 스코어 {row.composite_score:.2f}, 상위 편차 비율: {top_ratios}")

    result = get_candidate_signals(row.corp_code, int(row.bsns_year), engine, top_ratios)

    events = result["events"]
    print(f"\n[수시공시 후보] {len(events)}건 (회계연도 + 익년 4월까지)")
    if not events.empty:
        for r in events.itertuples():
            tag = " ★관련" if r.relevant else ""
            print(f"  {r.rcept_dt} [{r.window}] {r.report_nm} ({r.categories}){tag}")
    else:
        print("  (없음 — collect_disclosure_events.py를 먼저 실행했는지 확인하세요)")

    news = result["news"]
    print(f"\n[뉴스 후보] {len(news)}건")
    if not news.empty:
        for r in news.itertuples():
            tag = " ★관련" if r.relevant else ""
            print(f"  {r.pub_date} [{r.window}] {r.title}{tag}")
    else:
        print(f"  (없음 — 이 기업 뉴스는 아직 미수집. 예시: "
              f'py collect_news.py "{row.corp_name}" {row.corp_code})')


if __name__ == "__main__":
    main()

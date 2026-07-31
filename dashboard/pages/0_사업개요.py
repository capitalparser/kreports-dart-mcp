import re
import sys
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st

from dashboard.db import get_company, get_business_report_package, get_years_with_business_report
from dashboard.styles import (
    CSS,
    BORDER,
    GREEN,
    NAVY,
    ORANGE,
    PRIMARY,
    RED,
    TEXT_DARK,
    TEXT_MID,
    WHITE,
    insight,
    kpi_card,
    no_data,
    page_header,
    section_title,
)
from kreports.processor.report_section_parser import SECTION_LABELS


st.set_page_config(page_title="사업 개요", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)

from dashboard.sidebar import render_sidebar  # noqa: E402  # Configure Streamlit first.

render_sidebar()


_SECTION_ORDER = [
    "business_overview",
    "business_description",
    "risk_management",
    "management_plan",
    "rd_activities",
    "key_contracts",
]


def _safe(value: object) -> str:
    return escape(str(value or ""))


def _short(value: object, limit: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _dart_report_url(rcept_no: str | None) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""


@st.cache_data(ttl=86400, show_spinner=False)
def _get_company_web_links(corp_code: str) -> dict:
    """DART 회사개황의 홈페이지/IR 링크를 캐시해 페이지 진입 지연을 줄인다."""
    links = {"homepage": "", "ir": ""}
    try:
        from kreports.collector.fetcher import DART_BASE, _check_api_key, _get_client
        from kreports.config import settings

        _check_api_key()
        params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code}
        with _get_client() as client:
            resp = client.get(f"{DART_BASE}/company.json", params=params)
            data = resp.json()
        if data.get("status") != "000":
            return links
        for src_key, dst_key in (("hm_url", "homepage"), ("ir_url", "ir")):
            url = (data.get(src_key) or "").strip()
            if url and not url.startswith(("http://", "https://")):
                url = f"https://{url}"
            links[dst_key] = url
    except Exception:
        return links
    return links


def _link_btn(label: str, url: str, desc: str = "") -> str:
    if not url:
        return (
            f'<div style="text-align:center;padding:0.55rem;background:#F0F0F0;'
            f'border:1px solid {BORDER};border-radius:6px;color:#888;font-size:0.78rem;">'
            f'{_safe(label)}<br><span style="font-size:0.65rem;">미확인</span></div>'
        )
    desc_html = (
        f'<br><span style="font-size:0.64rem;color:{TEXT_MID};font-weight:400;">{_safe(desc)}</span>'
        if desc else ""
    )
    return (
        f'<a href="{_safe(url)}" target="_blank" style="display:block;text-align:center;'
        f'padding:0.55rem;background:{WHITE};border:1px solid {BORDER};border-radius:6px;'
        f'color:{PRIMARY};font-size:0.78rem;font-weight:700;text-decoration:none;">'
        f'{_safe(label)}{desc_html}</a>'
    )


def _render_focus_cards(cards: list[dict], empty_msg: str) -> None:
    if not cards:
        st.markdown(insight(empty_msg, "warn"), unsafe_allow_html=True)
        return

    cols = st.columns(2)
    for idx, card in enumerate(cards):
        source = card.get("source_label") or SECTION_LABELS.get(card.get("source_section", ""), "")
        accounts = card.get("accounts") or "-"
        assertions = card.get("assertions") or "-"
        evidence = _short(card.get("evidence"), 210)
        border = {"ok": GREEN, "warn": ORANGE, "bad": RED}.get(card.get("risk_level"), PRIMARY)
        with cols[idx % 2]:
            st.markdown(
                f"""
                <div style="background:{WHITE};border:1px solid {BORDER};border-top:3px solid {border};
                            border-radius:8px;padding:0.85rem 0.95rem;margin-bottom:0.8rem;
                            min-height:185px;box-shadow:0 1px 4px rgba(0,51,141,0.06);">
                  <div style="color:{TEXT_MID};font-size:0.68rem;font-weight:700;margin-bottom:0.25rem;">
                    {_safe(source or "사업보고서")}
                  </div>
                  <div style="color:{NAVY};font-size:0.95rem;font-weight:800;line-height:1.35;">
                    {_safe(card.get("title"))}
                  </div>
                  <div style="color:{TEXT_DARK};font-size:1.05rem;font-weight:800;margin:0.35rem 0;">
                    {_safe(card.get("value"))}
                  </div>
                  <div style="color:{TEXT_DARK};font-size:0.78rem;line-height:1.55;margin-bottom:0.45rem;">
                    {_safe(card.get("detail"))}
                  </div>
                  <div style="color:{TEXT_MID};font-size:0.7rem;line-height:1.45;">
                    <b>계정</b> {_safe(accounts)}<br>
                    <b>주장</b> {_safe(assertions)}
                  </div>
                  <div style="margin-top:0.55rem;padding-top:0.5rem;border-top:1px solid {BORDER};
                              color:{TEXT_MID};font-size:0.72rem;line-height:1.55;">
                    {_safe(evidence or "근거 스니펫 없음")}
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def _render_risk_chart(risk_distribution: dict | None) -> None:
    if not risk_distribution or len(risk_distribution.get("categories", [])) < 2:
        return
    import plotly.graph_objects as go

    categories = risk_distribution["categories"]
    fig = go.Figure(go.Bar(
        x=[c["name"] for c in categories],
        y=[c["count"] for c in categories],
        marker_color=PRIMARY,
        text=[f'{c["pct"]}%' for c in categories],
        textposition="outside",
    ))
    fig.update_layout(
        title="위험 유형별 언급 빈도",
        height=300,
        margin=dict(t=42, b=20, l=30, r=10),
        plot_bgcolor="white",
        paper_bgcolor="white",
        font=dict(color=TEXT_DARK, size=11),
        yaxis_title="언급 횟수",
    )
    st.plotly_chart(fig, use_container_width=True)


def _search_snippet(text: str, query: str, limit: int = 260) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not query:
        return clean[:limit] + ("..." if len(clean) > limit else "")
    pos = clean.lower().find(query.lower())
    if pos < 0:
        return ""
    start = max(0, pos - 90)
    end = min(len(clean), pos + 170)
    return ("..." if start else "") + clean[start:end].strip() + ("..." if end < len(clean) else "")


stock = st.session_state.get("selected_stock")
if not stock:
    st.warning("홈 페이지에서 종목을 먼저 선택하세요.")
    st.stop()

company = get_company(stock)
if not company:
    st.error(f"종목 {stock}을 찾을 수 없습니다.")
    st.stop()

corp_code = company["corp_code"]
corp_name = company["corp_name"]

st.markdown(page_header(
    f"사업보고서 분석 — {corp_name}",
    "감사 계획 관점과 투자 분석 관점으로 사업보고서 핵심 내용을 재구성합니다.",
), unsafe_allow_html=True)

years = get_years_with_business_report(corp_code)
if not years:
    st.markdown(no_data(
        "수집된 사업보고서가 없습니다. 아래 버튼으로 사업보고서 공시만 먼저 수집하세요."
    ), unsafe_allow_html=True)
    from dashboard.collector import render_business_report_collect_button

    render_business_report_collect_button(corp_code)
    st.stop()

sel_year = st.selectbox("사업연도", years, index=0)

with st.spinner("사업보고서 본문과 근거 스니펫을 분석 중입니다..."):
    package = get_business_report_package(corp_code, sel_year)

if not package:
    st.markdown(no_data(
        f"{sel_year}년 사업보고서를 불러올 수 없습니다. 사업보고서 수집 상태를 확인하세요.",
        state="empty",
    ), unsafe_allow_html=True)
    st.stop()

meta = package.get("meta", {})
sections = package.get("sections", {}) or {}
coverage = package.get("coverage", {}) or {}
rcept_no = meta.get("rcept_no")
dart_report_url = _dart_report_url(rcept_no)

top_cols = st.columns(4)
top_cols[0].markdown(kpi_card("사업연도", f"{meta.get('bsns_year') or sel_year}년", "사업보고서 기준", "ok"), unsafe_allow_html=True)
top_cols[1].markdown(kpi_card("제출일", meta.get("disc_date") or "-", "DART 접수일", "ok"), unsafe_allow_html=True)
top_cols[2].markdown(kpi_card(
    "추출 커버리지",
    f"{coverage.get('found_count', 0)} / {coverage.get('expected_count', len(SECTION_LABELS))}",
    f"{coverage.get('coverage_pct', 0)}%",
    "ok" if coverage.get("found_count", 0) >= 3 else "warn",
), unsafe_allow_html=True)
top_cols[3].markdown(kpi_card(
    "본문 분량",
    f"{coverage.get('total_chars', 0):,}자",
    "분석 대상 텍스트",
    "ok" if coverage.get("total_chars", 0) >= 1000 else "warn",
), unsafe_allow_html=True)

web_links = _get_company_web_links(corp_code)
analyst_url = f"https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={stock}"
consensus_url = f"https://finance.naver.com/item/coinfo.naver?code={stock}&target=finsum_more"
dart_search_url = f"https://dart.fss.or.kr/dsab007/detailSearch.do?textCrpNm={corp_name}"
ir_url = web_links["ir"] or (web_links["homepage"].rstrip("/") + "/ir" if web_links["homepage"] else "")

st.markdown(section_title("보고서와 외부 근거"), unsafe_allow_html=True)
link_cols = st.columns(5)
link_cols[0].markdown(_link_btn("DART 원문", dart_report_url, rcept_no or ""), unsafe_allow_html=True)
link_cols[1].markdown(_link_btn("DART 검색", dart_search_url, "전체 공시"), unsafe_allow_html=True)
link_cols[2].markdown(_link_btn("회사 IR", ir_url or web_links["homepage"], _short(web_links["homepage"], 25)), unsafe_allow_html=True)
link_cols[3].markdown(_link_btn("리서치", analyst_url, "네이버"), unsafe_allow_html=True)
link_cols[4].markdown(_link_btn("컨센서스", consensus_url, "네이버"), unsafe_allow_html=True)

if not sections:
    st.markdown(no_data(
        "사업보고서 본문에서 핵심 섹션을 식별하지 못했습니다. DART 원문 구조가 비표준일 수 있습니다.",
        state="empty",
    ), unsafe_allow_html=True)
    st.stop()

audit_focus = package.get("audit_focus", []) or []
investment_focus = package.get("investment_focus", []) or []
industry_insights = package.get("industry_insights", []) or []
risk_distribution = package.get("risk_distribution")

tab_audit, tab_invest, tab_source = st.tabs(["감사 관점", "투자 관점", "원문 근거"])

with tab_audit:
    st.markdown(section_title("감사 계획 메모"), unsafe_allow_html=True)
    st.markdown(insight(
        f"{corp_name}의 {sel_year}년 사업보고서에서 감사 착안 후보 {len(audit_focus)}건을 추출했습니다. "
        "각 카드는 사업보고서 원문 근거와 관련 계정·주장을 함께 표시합니다."
    ), unsafe_allow_html=True)
    _render_focus_cards(audit_focus, "사업보고서 원문에서 감사 착안 후보를 충분히 식별하지 못했습니다.")
    _render_risk_chart(risk_distribution)

with tab_invest:
    st.markdown(section_title("투자 분석 포인트"), unsafe_allow_html=True)
    if industry_insights:
        st.markdown(insight(
            f"업종 키워드 기반 추가 포인트 {len(industry_insights)}건을 먼저 확인했습니다."
        ), unsafe_allow_html=True)
        _render_focus_cards(industry_insights, "업종 키워드 기반 포인트가 없습니다.")
    _render_focus_cards(investment_focus, "사업보고서 원문에서 투자 분석 포인트를 충분히 식별하지 못했습니다.")

with tab_source:
    st.markdown(section_title("원문 근거 탐색"), unsafe_allow_html=True)
    if coverage.get("missing_labels"):
        st.caption(
            "미발견 섹션: "
            + ", ".join(coverage["missing_labels"])
            + " — 사업보고서 구조에 따라 일부 섹션은 추출되지 않을 수 있습니다."
        )

    query = st.text_input("원문 검색", placeholder="예: 수주, 연구개발, 환율, 주요 고객")
    section_options = {"전체": ""}
    section_options.update({SECTION_LABELS.get(key, key): key for key in _SECTION_ORDER if key in sections})
    selected_label = st.selectbox("섹션 필터", list(section_options.keys()), index=0)
    selected_key = section_options[selected_label]

    rendered = 0
    for section_key in _SECTION_ORDER:
        if section_key not in sections:
            continue
        if selected_key and section_key != selected_key:
            continue

        sec = sections[section_key]
        body_text = sec.get("body_text", "")
        snippet = _search_snippet(body_text, query)
        if query and not snippet:
            continue

        label = SECTION_LABELS.get(section_key, section_key)
        title = sec.get("title", label)
        length = sec.get("length", len(body_text))
        st.markdown(
            f'<div style="border-left:4px solid {PRIMARY};padding-left:0.75rem;margin:1rem 0 0.35rem;">'
            f'<div style="color:{NAVY};font-size:0.95rem;font-weight:800;">{_safe(label)}</div>'
            f'<div style="color:{TEXT_MID};font-size:0.72rem;">원문 제목: {_safe(title)} · {length:,}자</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        if snippet:
            st.markdown(insight(_safe(snippet)), unsafe_allow_html=True)
        with st.expander(f"{label} 원문 보기", expanded=(rendered == 0 and not query)):
            st.markdown(
                f'<div style="font-size:0.82rem;color:{TEXT_DARK};line-height:1.7;'
                f'max-height:620px;overflow-y:auto;background:{WHITE};border:1px solid {BORDER};'
                f'border-radius:6px;padding:1rem 1.2rem;">{sec.get("body_html", "")}</div>',
                unsafe_allow_html=True,
            )
        rendered += 1

    if rendered == 0:
        st.markdown(insight("검색어와 일치하는 원문 섹션이 없습니다.", "warn"), unsafe_allow_html=True)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from dashboard.db import get_company, get_business_report_sections, get_years_with_business_report
from dashboard.styles import (
    CSS, page_header, kpi_card, section_title, insight, no_data,
    PRIMARY, NAVY, GREEN, ORANGE, TEXT_DARK, TEXT_MID, WHITE, BORDER,
)

st.set_page_config(page_title="사업 개요", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
from dashboard.sidebar import render_sidebar
render_sidebar()

from kreports.processor.report_section_parser import SECTION_LABELS

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
    f"사업 개요 — {corp_name}",
    "사업보고서 핵심 경영 정보 요약 · 사업개요 · 위험관리 · 경영계획"
), unsafe_allow_html=True)

# ── 도움말 ────────────────────────────────────────────────────────────────
with st.expander("이 페이지 안내", expanded=False):
    st.markdown("""
    DART 사업보고서 본문에서 **핵심 경영 정보 섹션**을 자동 추출합니다.

    - **사업의 개요**: 회사 연혁, 주요 사업 영역, 조직 구조
    - **사업의 내용**: 제품/서비스, 매출 구조, 경쟁 현황, 시장 분석
    - **위험관리**: 재무 위험, 사업 위험, 파생상품 현황
    - **경영진 경영계획**: 중장기 전략, 투자 계획, 성장 목표
    - **연구개발활동**: R&D 투자 현황, 핵심 기술 개발
    - **주요 계약**: 핵심 거래 계약, 라이선스, 파트너십

    감사 계획 수립 시 사업 이해(ISA 315), 투자 분석 시 비즈니스 모델 파악에 활용하세요.
    """)

# ── 연도 선택 ──────────────────────────────────────────────────────────────
years = get_years_with_business_report(corp_code)
if not years:
    st.markdown(no_data(
        "수집된 사업보고서가 없습니다. 공시 수집 후 다시 확인하세요."
    ), unsafe_allow_html=True)
    from dashboard.collector import render_collect_button
    render_collect_button(corp_code, stock)
    st.stop()

sel_year = st.selectbox("사업연도", years, index=0)

# ── 데이터 로딩 ────────────────────────────────────────────────────────────
with st.spinner("사업보고서 본문 분석 중..."):
    result = get_business_report_sections(corp_code, sel_year)

if not result:
    st.markdown(no_data(
        f"{sel_year}년 사업보고서 본문을 추출할 수 없습니다. "
        "사업보고서가 수집되지 않았거나 문서 구조가 비표준일 수 있습니다.",
        state="empty",
    ), unsafe_allow_html=True)
    st.stop()

# get_business_report_sections는 {section_key: {...}} dict를 직접 반환
sections = result if isinstance(result, dict) else {}
rcept_no = ""

if not sections:
    st.markdown(no_data(
        "사업보고서 본문에서 핵심 섹션을 식별하지 못했습니다.",
        state="empty",
    ), unsafe_allow_html=True)
    st.stop()

# ── KPI 카드 ───────────────────────────────────────────────────────────────
k1, k2, k3 = st.columns(3)
k1.markdown(kpi_card(
    "추출 섹션", f"{len(sections)} / {len(SECTION_LABELS)}",
    "사업보고서 핵심 항목", "ok" if len(sections) >= 3 else "warn",
), unsafe_allow_html=True)
k2.markdown(kpi_card(
    "사업연도", f"{sel_year}년",
    f"사업보고서 기준", "ok",
), unsafe_allow_html=True)
total_chars = sum(s.get("length", 0) for s in sections.values())
k3.markdown(kpi_card(
    "본문 분량", f"{total_chars:,}자",
    "추출된 텍스트 총량", "ok",
), unsafe_allow_html=True)

st.markdown("<div style='margin:0.8rem 0'></div>", unsafe_allow_html=True)

# ── 외부 리소스 링크 ──────────────────────────────────────────────────────
st.markdown(section_title("외부 리소스"), unsafe_allow_html=True)

# 회사 홈페이지/IR 페이지를 DART에서 가져오기
_hm_url = ""
_ir_url_dart = ""
try:
    from kreports.collector.fetcher import fetch_company_info as _fci_full
    from kreports.collector.fetcher import _get_client, _check_api_key, DART_BASE
    from kreports.config import settings
    _check_api_key()
    _params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code}
    with _get_client() as _client:
        _resp = _client.get(f"{DART_BASE}/company.json", params=_params)
        _cdata = _resp.json()
        if _cdata.get("status") == "000":
            _hm_url = (_cdata.get("hm_url") or "").strip()
            _ir_url_dart = (_cdata.get("ir_url") or "").strip()
            if _hm_url and not _hm_url.startswith("http"):
                _hm_url = f"https://{_hm_url}"
            if _ir_url_dart and not _ir_url_dart.startswith("http"):
                _ir_url_dart = f"https://{_ir_url_dart}"
except Exception:
    pass

# 실질적 외부 링크
_analyst_url = f"https://finance.naver.com/research/company_list.naver?searchType=itemCode&itemCode={stock}"
_ir_url = _ir_url_dart or (_hm_url + "/ir" if _hm_url else "")
_dart_url = f"https://dart.fss.or.kr/dsab007/detailSearch.do?textCrpNm={corp_name}"

def _link_btn(label, url, desc=""):
    if not url:
        return (
            f'<div style="text-align:center;padding:0.5rem;background:#F0F0F0;'
            f'border:1px solid {BORDER};border-radius:6px;color:#999;font-size:0.78rem;">'
            f'{label}<br><span style="font-size:0.65rem;">(미등록)</span></div>'
        )
    return (
        f'<a href="{url}" target="_blank" style="display:block;text-align:center;'
        f'padding:0.5rem;background:{WHITE};border:1px solid {BORDER};border-radius:6px;'
        f'color:{PRIMARY};font-size:0.78rem;font-weight:600;text-decoration:none;">'
        f'{label}'
        + (f'<br><span style="font-size:0.62rem;color:{TEXT_MID};font-weight:400;">{desc}</span>' if desc else "")
        + '</a>'
    )

_link_cols = st.columns(4)
_link_cols[0].markdown(_link_btn("애널리스트 보고서", _analyst_url, "네이버 리서치"), unsafe_allow_html=True)
_link_cols[1].markdown(_link_btn("회사 IR", _ir_url or _hm_url, _hm_url.replace("https://","").replace("http://","")[:25] if _hm_url else ""), unsafe_allow_html=True)
_link_cols[2].markdown(_link_btn("DART 공시", _dart_url, "전체 공시 검색"), unsafe_allow_html=True)
_link_cols[3].markdown(_link_btn("네이버 컨센서스", f"https://finance.naver.com/item/coinfo.naver?code={stock}&target=finsum_more", "목표주가/투자의견"), unsafe_allow_html=True)

# ── 자동 인사이트 ──────────────────────────────────────────────────────────
from kreports.analysis.business_insights import generate_business_insights

_insights = generate_business_insights(sections, induty_code=company.get("induty_code", ""))
if _insights:
    st.markdown(section_title("핵심 인사이트"), unsafe_allow_html=True)

    # 감사 포인트 / 투자 포인트 분리
    _audit_insights = [i for i in _insights if i["audience"] in ("auditor", "both")]
    _invest_insights = [i for i in _insights if i["audience"] in ("investor", "both")]

    _tab_audit, _tab_invest = st.tabs(["감사 포인트", "투자 포인트"])

    def _render_insight_card(col, ins):
        detail = ins.get("detail", "")
        # detail이 60자 초과면 sub에 요약만, tooltip에 전문
        if len(detail) > 60:
            sub = detail[:55] + "..."
            tooltip = detail
        else:
            sub = detail
            tooltip = ""
        col.markdown(
            kpi_card(ins["title"], ins["value"], sub, ins["risk_level"], tooltip=tooltip),
            unsafe_allow_html=True,
        )

    with _tab_audit:
        if _audit_insights:
            _a_cols = st.columns(min(len(_audit_insights), 4))
            for i, ins in enumerate(_audit_insights):
                _render_insight_card(_a_cols[i % len(_a_cols)], ins)
        else:
            st.caption("추출된 감사 관련 인사이트가 없습니다.")

    with _tab_invest:
        if _invest_insights:
            _i_cols = st.columns(min(len(_invest_insights), 4))
            for i, ins in enumerate(_invest_insights):
                _render_insight_card(_i_cols[i % len(_i_cols)], ins)
        else:
            st.caption("추출된 투자 관련 인사이트가 없습니다.")

    # 위험 분포 차트 (bar)
    from kreports.analysis.business_insights import extract_risk_distribution
    _risk_dist = extract_risk_distribution(sections)
    if _risk_dist and len(_risk_dist["categories"]) >= 2:
        import plotly.graph_objects as go
        _fig = go.Figure(go.Bar(
            x=[c["name"] for c in _risk_dist["categories"]],
            y=[c["count"] for c in _risk_dist["categories"]],
            marker_color=PRIMARY,
            text=[f'{c["pct"]}%' for c in _risk_dist["categories"]],
            textposition="outside",
        ))
        _fig.update_layout(
            title="위험 유형별 언급 빈도",
            height=280,
            margin=dict(t=40, b=20, l=30, r=10),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color=TEXT_DARK, size=11),
            yaxis_title="언급 횟수",
        )
        st.plotly_chart(_fig, use_container_width=True)

st.markdown("<div style='margin:0.8rem 0'></div>", unsafe_allow_html=True)

# ── 섹션별 렌더링 ──────────────────────────────────────────────────────────
_SECTION_ORDER = [
    "business_overview", "business_description", "risk_management",
    "management_plan", "rd_activities", "key_contracts",
]

first_rendered = True
for section_key in _SECTION_ORDER:
    if section_key not in sections:
        continue

    sec = sections[section_key]
    label = SECTION_LABELS.get(section_key, section_key)
    title = sec.get("title", label)
    body_html = sec.get("body_html", "")
    length = sec.get("length", 0)

    # 섹션 카드 — 색상 구분
    color_map = {
        "business_overview": PRIMARY,
        "business_description": NAVY,
        "risk_management": ORANGE,
        "management_plan": GREEN,
        "rd_activities": PRIMARY,
        "key_contracts": NAVY,
    }
    border_color = color_map.get(section_key, PRIMARY)

    st.markdown(
        f'<div style="border-left:4px solid {border_color};padding-left:0.8rem;'
        f'margin:1rem 0 0.3rem;">'
        f'<div style="color:{NAVY};font-size:0.95rem;font-weight:700;">{label}</div>'
        f'<div style="color:{TEXT_MID};font-size:0.72rem;">'
        f'원문 제목: {title} · {length:,}자</div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    with st.expander(f"{label} 상세 보기", expanded=first_rendered):
        st.markdown(
            f'<div style="font-size:0.82rem;color:{TEXT_DARK};'
            f'line-height:1.7;max-height:600px;overflow-y:auto;'
            f'background:{WHITE};border:1px solid {BORDER};'
            f'border-radius:6px;padding:1rem 1.2rem;">'
            f'{body_html}</div>',
            unsafe_allow_html=True,
        )
    first_rendered = False

# ── 미발견 섹션 안내 ───────────────────────────────────────────────────────
missing = [SECTION_LABELS[k] for k in _SECTION_ORDER if k not in sections]
if missing:
    st.caption(f"미발견 섹션: {', '.join(missing)} — 사업보고서 구조에 따라 일부 섹션은 추출되지 않을 수 있습니다.")

# ── DART 원문 링크 ─────────────────────────────────────────────────────────
if rcept_no:
    dart_link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
    st.markdown(
        f'<div style="margin-top:1rem;text-align:center;">'
        f'<a href="{dart_link}" target="_blank" style="color:{PRIMARY};font-size:0.85rem;">'
        f'DART 사업보고서 원문 보기 ({rcept_no})</a></div>',
        unsafe_allow_html=True,
    )

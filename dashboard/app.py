import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from dashboard.db import search_companies, get_financials, get_auditors, get_risk_summary, get_data_freshness
from dashboard.styles import CSS, page_header, kpi_card, section_title, no_data, PRIMARY, NAVY, RED, GREEN, ORANGE, TEXT_MID

st.set_page_config(
    page_title="DART 감사·투자 분석",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.markdown(CSS, unsafe_allow_html=True)

# 사이드바 — 종목 선택
with st.sidebar:
    st.markdown(f"""
    <div style="padding:1rem 0 0.5rem 0;">
      <div style="font-size:1.1rem; font-weight:700; letter-spacing:-0.3px;">DART 분석</div>
      <div style="font-size:0.72rem; opacity:0.7; margin-top:0.1rem;">감사팀 · 투자분석 플랫폼</div>
    </div>
    """, unsafe_allow_html=True)
    st.divider()

    search_input = st.text_input("종목 검색", placeholder="회사명 또는 종목코드", key="sidebar_search")
    if not search_input and "selected_stock" not in st.session_state:
        st.caption("또는 메인 화면에서 검색하세요")
    if search_input:
        candidates = search_companies(search_input, limit=10)
        if candidates:
            options = {f"{c['corp_name']} ({c['stock_code']})": c["stock_code"] for c in candidates}
            chosen_label = st.selectbox("검색 결과", list(options.keys()))
            if st.button("선택", use_container_width=True):
                st.session_state["selected_stock"] = options[chosen_label]
                st.session_state["selected_corp_name"] = chosen_label.split(" (")[0]
                st.rerun()

    if "selected_stock" in st.session_state:
        st.divider()
        st.markdown(f"""
        <div style="background:rgba(255,255,255,0.1); border-radius:8px; padding:0.6rem 0.8rem;">
          <div style="font-size:0.68rem; opacity:0.7;">현재 종목</div>
          <div style="font-weight:700; font-size:0.95rem;">{st.session_state.get('selected_corp_name','')}</div>
          <div style="font-size:0.75rem; opacity:0.8;">{st.session_state.get('selected_stock','')}</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("✕ 선택 해제", use_container_width=True, key="sidebar_clear_btn"):
            del st.session_state["selected_stock"]
            if "selected_corp_name" in st.session_state:
                del st.session_state["selected_corp_name"]
            st.rerun()

    st.divider()
    st.markdown("""
    <div style="font-size:0.72rem; opacity:0.6; line-height:1.7;">
    <b>페이지</b><br>
    홈 · 기업 검색<br>
    1 · 재무 요약<br>
    2 · 위험 신호<br>
    3 · 감사인 이력<br>
    4 · 공시 타임라인<br>
    5 · 재무 상세<br>
    6 · 회계정책
    </div>
    """, unsafe_allow_html=True)


# 메인
st.markdown(page_header(
    "DART 감사·투자 분석 플랫폼",
    "감사팀 현장 지원 · 재무 위험 조기 경보 · 감사인 이력 추적"
), unsafe_allow_html=True)

# 검색창
col_search, col_btn = st.columns([5, 1])
with col_search:
    query = st.text_input(" ", placeholder="회사명 또는 종목코드 입력 (예: SK하이닉스, 000660)", label_visibility="collapsed")
with col_btn:
    st.markdown("<div style='margin-top:0.4rem'></div>", unsafe_allow_html=True)

import pandas as pd

if query:
    results = search_companies(query)
    if results:
        st.markdown(section_title(f"검색 결과 {len(results)}건"), unsafe_allow_html=True)
        df = pd.DataFrame(results)[["corp_name", "stock_code", "market"]]
        df.columns = ["회사명", "종목코드", "시장"]

        selected = st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            height=min(len(results) * 35 + 50, 400),
        )
        if selected["selection"]["rows"]:
            idx = selected["selection"]["rows"][0]
            stock_code = results[idx]["stock_code"]
            corp_name = results[idx]["corp_name"]
            st.session_state["selected_stock"] = stock_code
            st.session_state["selected_corp_name"] = corp_name
            st.rerun()
    else:
        st.warning("검색 결과가 없습니다.")

# 선택된 종목 개요
if "selected_stock" in st.session_state:
    stock = st.session_state["selected_stock"]
    corp_name = st.session_state.get("selected_corp_name", stock)

    st.markdown(section_title(f"{corp_name} ({stock}) — 분석 개요"), unsafe_allow_html=True)

    summary = get_risk_summary(
        next(iter([c["corp_code"] for c in search_companies(stock, 1)]), None) or ""
    )

    if not summary["has_data"]:
        st.markdown(no_data(f"{corp_name} 재무 데이터가 아직 수집되지 않았습니다."), unsafe_allow_html=True)
        corp_code_for_collect = next(iter([c["corp_code"] for c in search_companies(stock, 1)]), None) or ""
        from dashboard.collector import render_collect_button
        render_collect_button(corp_code_for_collect, stock)
    else:
        # 위험 신호 카드 4개
        c1, c2, c3, c4 = st.columns(4)
        def _risk(v, threshold=0):
            return "bad" if v > threshold else "ok"

        c1.markdown(kpi_card(
            "CFS/OFS 순이익 괴리",
            f"{summary['cfs_ofs_gap_count']}건",
            "±20% 초과 분기 수",
            _risk(summary["cfs_ofs_gap_count"])
        ), unsafe_allow_html=True)
        c2.markdown(kpi_card(
            "계정 매핑 불완전",
            f"{summary['low_map_conf_count']}건",
            "신뢰도 80% 미만",
            _risk(summary["low_map_conf_count"])
        ), unsafe_allow_html=True)
        c3.markdown(kpi_card(
            "감사인 교체",
            f"{summary['auditor_change_count']}회",
            "최근 5개년",
            _risk(summary["auditor_change_count"])
        ), unsafe_allow_html=True)
        c4.markdown(kpi_card(
            "비적정 감사의견",
            f"{summary['non_clean_opinion_count']}건",
            "한정·부적정·의견거절",
            _risk(summary["non_clean_opinion_count"])
        ), unsafe_allow_html=True)

        # 데이터 신선도 표시
        freshness = get_data_freshness(
            next(iter([c["corp_code"] for c in search_companies(stock, 1)]), None) or ""
        )
        _parts = []
        for label, dt in [("재무", freshness["financial"]), ("공시", freshness["disclosure"]), ("감사", freshness["auditor"])]:
            if dt:
                _parts.append(f"{label}: {dt.strftime('%Y-%m-%d')}")
        if _parts:
            st.markdown(
                f'<div style="font-size:0.72rem; color:{TEXT_MID}; margin-top:0.5rem;">'
                f'최종 수집 — {" · ".join(_parts)}</div>',
                unsafe_allow_html=True,
            )

        st.caption("상세 분석은 좌측 사이드바 또는 상단 메뉴에서 페이지를 선택하세요.")
else:
    st.markdown("""
    <div style="margin-top:3rem; text-align:center; color:#8492B4;">
      <div style="font-size:3rem;">🔍</div>
      <div style="font-size:1rem; font-weight:600; margin-top:0.5rem;">종목을 검색하세요</div>
      <div style="font-size:0.82rem; margin-top:0.3rem;">회사명 또는 6자리 종목코드를 입력하세요</div>
    </div>
    """, unsafe_allow_html=True)

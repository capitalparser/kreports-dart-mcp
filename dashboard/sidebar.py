"""모든 페이지에서 공유하는 사이드바 종목 검색 컴포넌트."""
import streamlit as st
from dashboard.db import search_companies
from dashboard.styles import CSS


def render_sidebar() -> None:
    """
    사이드바에 종목 검색 위젯을 렌더링한다.
    모든 페이지에서 호출하여 어느 탭에서든 종목 변경이 가능하게 한다.
    """
    with st.sidebar:
        st.markdown(CSS, unsafe_allow_html=True)
        st.markdown("""
        <div style="padding:1rem 0 0.5rem 0;">
          <div style="font-size:1.1rem; font-weight:700; letter-spacing:-0.3px; color:white;">DART 분석</div>
          <div style="font-size:0.72rem; opacity:0.7; margin-top:0.1rem; color:white;">감사팀 · 투자분석 플랫폼</div>
        </div>
        """, unsafe_allow_html=True)
        st.divider()

        search_input = st.text_input(
            "종목 검색",
            placeholder="회사명 또는 종목코드",
            key="sidebar_search_global",
        )
        if search_input:
            candidates = search_companies(search_input, limit=10)
            if candidates:
                options = {
                    f"{c['corp_name']} ({c['stock_code']})": c["stock_code"]
                    for c in candidates
                }
                chosen_label = st.selectbox("검색 결과", list(options.keys()), key="sidebar_selectbox_global")
                if st.button("선택", use_container_width=True, key="sidebar_select_btn_global"):
                    st.session_state["selected_stock"] = options[chosen_label]
                    st.session_state["selected_corp_name"] = chosen_label.split(" (")[0]
                    st.rerun()
            else:
                st.caption("검색 결과 없음")

        if "selected_stock" in st.session_state:
            st.divider()
            st.markdown(f"""
            <div style="background:rgba(255,255,255,0.1); border-radius:8px; padding:0.6rem 0.8rem;">
              <div style="font-size:0.68rem; opacity:0.7; color:white;">현재 종목</div>
              <div style="font-weight:700; font-size:0.95rem; color:white;">{st.session_state.get('selected_corp_name', '')}</div>
              <div style="font-size:0.75rem; opacity:0.8; color:white;">{st.session_state.get('selected_stock', '')}</div>
            </div>
            """, unsafe_allow_html=True)

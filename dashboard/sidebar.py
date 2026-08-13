"""모든 페이지에서 공유하는 사이드바 종목 검색 컴포넌트."""
import streamlit as st
from dashboard.db import search_companies, get_bootstrap_status
from dashboard.styles import CSS


def render_sidebar() -> None:
    """
    사이드바에 종목 검색 위젯을 렌더링한다.
    모든 페이지에서 호출하여 어느 탭에서든 종목 변경이 가능하게 한다.
    """
    bootstrap = get_bootstrap_status()
    master_data_ready = bootstrap["has_company_master"]

    with st.sidebar:
        st.markdown(CSS, unsafe_allow_html=True)
        st.markdown("""
        <div style="padding:1rem 0 0.5rem 0;">
          <div style="font-size:1.1rem; font-weight:700; letter-spacing:-0.3px; color:white;">DART 분석</div>
          <div style="font-size:0.72rem; opacity:0.7; margin-top:0.1rem; color:white;">감사팀 · 투자분석 플랫폼</div>
        </div>
        """, unsafe_allow_html=True)
        st.divider()

        if not master_data_ready:
            if bootstrap["api_key_set"]:
                st.caption("회사 목록이 비어 있습니다. `kreports sync-companies` 실행이 필요합니다.")
            else:
                st.caption("`.env`에 `DART_API_KEY`를 넣고 `kreports sync-companies`를 실행해야 검색할 수 있습니다.")

        search_input = st.text_input(
            "종목 검색",
            placeholder="회사명 또는 종목코드",
            key="sidebar_search_global",
            disabled=not master_data_ready,
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
                    # 최근 조회 종목에 추가
                    _recent = st.session_state.get("recent_stocks", [])
                    _new = {"stock_code": options[chosen_label], "corp_name": chosen_label.split(" (")[0]}
                    _recent = [r for r in _recent if r["stock_code"] != _new["stock_code"]]
                    _recent.insert(0, _new)
                    st.session_state["recent_stocks"] = _recent[:5]
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
            if st.button("✕ 선택 해제", use_container_width=True, key="sidebar_clear_btn_global"):
                del st.session_state["selected_stock"]
                if "selected_corp_name" in st.session_state:
                    del st.session_state["selected_corp_name"]
                st.rerun()

            # CFS/OFS 구분 — 페이지 간 공유
            st.divider()
            _fs_default = 0 if st.session_state.get("fs_div", "CFS") == "CFS" else 1
            _fs_choice = st.radio(
                "재무제표 구분",
                ["CFS (연결)", "OFS (별도)"],
                index=_fs_default,
                horizontal=True,
                key="sidebar_fs_div_global",
            )
            st.session_state["fs_div"] = "CFS" if "CFS" in _fs_choice else "OFS"

        # 최근 조회 종목
        _recent = st.session_state.get("recent_stocks", [])
        if _recent:
            st.divider()
            st.markdown(
                '<div style="font-size:0.72rem;opacity:0.6;color:white;margin-bottom:0.3rem;">최근 조회</div>',
                unsafe_allow_html=True,
            )
            for i, item in enumerate(_recent[:5]):
                if item["stock_code"] != st.session_state.get("selected_stock"):
                    if st.button(
                        f"{item['corp_name']} ({item['stock_code']})",
                        use_container_width=True,
                        key=f"recent_{i}",
                    ):
                        st.session_state["selected_stock"] = item["stock_code"]
                        st.session_state["selected_corp_name"] = item["corp_name"]
                        st.rerun()

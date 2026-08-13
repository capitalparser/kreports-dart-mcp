import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd
from dashboard.db import (
    get_financial_facts, get_financial_facts_years, has_financial_facts,
    get_company, get_notes_for_account,
)
from dashboard.styles import CSS, page_header, section_title, no_data

st.set_page_config(page_title="재무 상세", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
from dashboard.sidebar import render_sidebar
render_sidebar()

_UNIT = 1e8  # 억원
_SJ_LABELS = {
    "BS": "재무상태표",
    "IS": "손익계산서",
    "CIS": "포괄손익계산서",
    "CF": "현금흐름표",
    "SCE": "자본변동표",
}
_REPRT_MAP = {"연간(Q4)": "11011", "Q3": "11014", "Q2": "11012", "Q1": "11013"}
_SJ_MAP = {
    "전체": None, "BS (재무상태표)": "BS", "IS (손익계산서)": "IS",
    "CIS (포괄손익)": "CIS", "CF (현금흐름)": "CF", "SCE (자본변동)": "SCE",
}


def _render_fact_table(sub: pd.DataFrame, sj: str) -> None:
    if sub.empty:
        st.info("데이터 없음")
        return

    st.markdown(section_title(_SJ_LABELS.get(sj, sj)), unsafe_allow_html=True)

    disp = sub[["순서", "계정명", "account_id", "당기", "전기", "전전기", "당분기"]].copy()

    # 억원 단위 변환
    for col in ["당기", "전기", "전전기", "당분기"]:
        disp[col] = pd.to_numeric(disp[col], errors="coerce") / _UNIT
        disp[col] = disp[col].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-")

    # 합성 ID(_nm_)인 계정은 하위 계정으로 들여쓰기 표시
    disp["계정명"] = disp.apply(
        lambda r: ("  · " if str(r.get("account_id", "")).startswith("_nm_") else "") + str(r["계정명"]),
        axis=1,
    )
    # 합성 ID 숨기기
    disp["account_id"] = disp["account_id"].apply(
        lambda x: "" if str(x).startswith("_nm_") else x
    )

    show_cols = ["계정명", "account_id", "당기(억원)", "전기(억원)", "전전기(억원)"]
    disp = disp.rename(columns={
        "account_id": "account_id",
        "당기": "당기(억원)", "전기": "전기(억원)", "전전기": "전전기(억원)", "당분기": "당분기(억원)",
    })

    # 분기보고서는 당분기 컬럼 추가
    if disp["당분기(억원)"].nunique() > 1 or (disp["당분기(억원)"] != "-").any():
        show_cols = ["계정명", "account_id", "당기(억원)", "당분기(억원)", "전기(억원)", "전전기(억원)"]

    st.dataframe(
        disp[show_cols],
        use_container_width=True,
        hide_index=True,
        height=min(len(disp) * 35 + 50, 700),
    )


# ── 페이지 렌더링 ─────────────────────────────────────────────────────────

stock = st.session_state.get("selected_stock")
if not stock:
    st.warning("홈 페이지에서 종목을 먼저 선택하세요.")
    st.stop()

company = get_company(stock)
if not company:
    st.error(f"종목 {stock}을 찾을 수 없습니다.")
    st.stop()

st.markdown(page_header(
    f"재무 상세 — {company['corp_name']}",
    "XBRL 공시 계정 전체 · BS / IS / CF / SCE 세부 계정 (상장사 전용)"
), unsafe_allow_html=True)

if not has_financial_facts(company["corp_code"]):
    st.markdown(no_data(
        "전체 계정 데이터가 없습니다. "
        "상장사만 지원하며, 홈 화면의 '데이터 수집' 버튼을 실행하세요."
    ), unsafe_allow_html=True)
    from dashboard.collector import render_collect_button
    render_collect_button(company["corp_code"], stock)
    st.stop()

# ── 필터 ──────────────────────────────────────────────────────────────────
years = get_financial_facts_years(company["corp_code"])

col_f1, col_f2, col_f3, col_f4, col_f5 = st.columns([2, 2, 2, 2, 3])
with col_f1:
    sel_year = st.selectbox("연도", years, index=0)
with col_f2:
    sel_reprt = st.selectbox("보고서", ["연간(Q4)", "Q3", "Q2", "Q1"])
    reprt_code = _REPRT_MAP[sel_reprt]
with col_f3:
    _fs_default = 0 if st.session_state.get("fs_div", "CFS") == "CFS" else 1
    sel_fs = st.radio("재무제표", ["CFS (연결)", "OFS (별도)"], horizontal=True, index=_fs_default)
    fs_div = "CFS" if "CFS" in sel_fs else "OFS"
with col_f4:
    sel_sj = st.selectbox("재무표", list(_SJ_MAP.keys()))
    sj_div = _SJ_MAP[sel_sj]
with col_f5:
    kw = st.text_input("계정명 검색", placeholder="예: 현금, 매출채권, 리스")

df = get_financial_facts(
    company["corp_code"],
    bsns_year=sel_year,
    reprt_code=reprt_code,
    fs_div=fs_div,
    sj_div=sj_div,
)

if df.empty:
    st.info(
        f"{sel_year}년 {sel_reprt} {fs_div} 데이터가 없습니다. "
        "해당 분기가 수집되지 않았을 수 있습니다."
    )
    st.stop()

if kw:
    df = df[df["계정명"].str.contains(kw, case=False, na=False)]
    if df.empty:
        st.info(f"'{kw}' 검색 결과가 없습니다.")
        st.stop()

st.caption(f"총 **{len(df):,}개** 계정 · 단위: 억원")

# ── 재무표별 탭 렌더링 ────────────────────────────────────────────────────
if sj_div is None:
    sj_present = [s for s in ["BS", "IS", "CIS", "CF", "SCE"] if s in df["재무표"].values]
    if sj_present:
        tabs = st.tabs([_SJ_LABELS.get(s, s) for s in sj_present])
        for tab, sj in zip(tabs, sj_present):
            with tab:
                _render_fact_table(df[df["재무표"] == sj].copy(), sj)
    else:
        st.info("표시할 데이터가 없습니다.")
else:
    _render_fact_table(df, sj_div)

# ── 주석 조회 ─────────────────────────────────────────────────────────────
st.markdown("---")
st.markdown(section_title("주석 조회"), unsafe_allow_html=True)

note_account = st.text_input("계정명 입력 후 Enter", placeholder="예: 장기대여금, 무형자산, 리스")
if note_account:
    with st.spinner(f"'{note_account}' 관련 주석 검색 중..."):
        note_html = get_notes_for_account(company["corp_code"], sel_year, note_account, fs_div)
    if note_html:
        with st.expander(f"'{note_account}' 관련 주석", expanded=True):
            st.markdown(f"""
<div style="
    background:#F8FAFF;
    border:1px solid #D1D8F0;
    border-left:4px solid #1E49E2;
    border-radius:6px;
    padding:1rem 1.2rem;
    font-size:0.82rem;
    color:#1A1A2E;
    max-height:700px;
    overflow-y:auto;
    line-height:1.6;
">{note_html}</div>
""", unsafe_allow_html=True)
    else:
        st.info(f"'{note_account}' 관련 주석을 찾지 못했습니다. (사업보고서 공시 데이터 수집 필요)")

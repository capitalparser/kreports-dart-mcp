import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import plotly.express as px
import pandas as pd
from dashboard.db import get_disclosures, get_company
from dashboard.styles import CSS, page_header, section_title, no_data, PRIMARY, NAVY

st.set_page_config(page_title="공시 타임라인", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
from dashboard.sidebar import render_sidebar
render_sidebar()

DART_URL = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="

# 공시 유형 한글 레이블
TYPE_LABELS = {
    "A": "정기공시", "B": "주요사항", "C": "외감법인",
    "D": "지분공시", "E": "기타공시", "F": "외감·감사",
    "G": "증권신고", "H": "합병공시", "I": "자산유동화",
}

AUDIT_KEYWORDS = ("감사보고서", "내부회계관리제도")

stock = st.session_state.get("selected_stock")
if not stock:
    st.warning("홈 페이지에서 종목을 먼저 선택하세요.")
    st.stop()

company = get_company(stock)
if not company:
    st.error(f"종목 {stock}을 찾을 수 없습니다.")
    st.stop()

st.markdown(page_header(
    f"공시 타임라인 — {company['corp_name']}",
    "DART 전체 공시 흐름 · 감사 관련 공시 필터"
), unsafe_allow_html=True)

with st.spinner("공시 데이터 로딩 중..."):
    df = get_disclosures(company["corp_code"])
if df.empty:
    from dashboard.db import has_been_collected
    _collected = has_been_collected(company["corp_code"], "disclosure")
    if _collected:
        st.markdown(no_data("공시 데이터가 DART에 없습니다.", state="empty"), unsafe_allow_html=True)
    else:
        st.markdown(no_data("공시 데이터가 아직 수집되지 않았습니다."), unsafe_allow_html=True)
        from dashboard.collector import render_collect_button
        render_collect_button(company["corp_code"], stock)
    st.stop()

df["공시일"] = pd.to_datetime(df["공시일"], errors="coerce")
df["연도"] = df["공시일"].dt.year
df["유형_레이블"] = df["유형"].map(TYPE_LABELS).fillna(df["유형"])
df["감사관련"] = df["공시명"].apply(lambda x: any(k in str(x) for k in AUDIT_KEYWORDS))

# ── 필터 ──────────────────────────────────────────────────────────────────
col_f1, col_f2, col_f3 = st.columns([2, 2, 3])
with col_f1:
    type_options = ["전체"] + sorted(df["유형_레이블"].unique().tolist())
    sel_type = st.selectbox("공시 유형", type_options)
with col_f2:
    audit_only = st.checkbox("감사 관련 공시만", value=False)
with col_f3:
    keyword = st.text_input("공시명 검색", placeholder="예: 분기보고서, 주요사항")

filtered = df.copy()
if sel_type != "전체":
    filtered = filtered[filtered["유형_레이블"] == sel_type]
if audit_only:
    filtered = filtered[filtered["감사관련"]]
if keyword:
    filtered = filtered[filtered["공시명"].str.contains(keyword, na=False, case=False)]

st.caption(f"전체 {len(df):,}건 중 **{len(filtered):,}건** 표시")

# ── 연도별 공시 건수 차트 ────────────────────────────────────────────────
st.markdown(section_title("연도별 공시 현황"), unsafe_allow_html=True)

counts = (
    filtered.groupby(["연도", "유형_레이블"])
    .size().reset_index(name="건수")
    .dropna(subset=["연도"])
)
counts["연도"] = counts["연도"].astype(int)

if not counts.empty:
    color_seq = [PRIMARY, NAVY, "#7B9FE8", "#4A6DB5", "#B8C9F5", "#9CA3AF", "#4B5563"]
    fig = px.bar(
        counts, x="연도", y="건수", color="유형_레이블",
        color_discrete_sequence=color_seq,
        height=250,
    )
    fig.update_layout(
        margin=dict(t=10, b=10, l=0, r=0),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color="#333333"),
        xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
        yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
        legend=dict(orientation="h", y=-0.25, font_size=11),
        bargap=0.2,
    )
    st.plotly_chart(fig, use_container_width=True)

# ── 공시 목록 테이블 ──────────────────────────────────────────────────────
st.markdown(section_title("공시 목록"), unsafe_allow_html=True)

# DART 링크 포함 HTML 테이블
disp = filtered[["공시일", "유형_레이블", "공시명", "제출인", "접수번호"]].copy()
disp["공시일"] = disp["공시일"].dt.strftime("%Y-%m-%d")
disp["공시명_링크"] = disp.apply(
    lambda r: f'<a href="{DART_URL}{r["접수번호"]}" target="_blank" style="color:{PRIMARY}; text-decoration:none;">{r["공시명"]}</a>',
    axis=1,
)
disp["감사관련"] = filtered["감사관련"].apply(lambda x: "🔍" if x else "")

table_html = disp[["공시일", "유형_레이블", "공시명_링크", "제출인", "감사관련"]].rename(
    columns={"유형_레이블": "유형", "공시명_링크": "공시명"}
).to_html(escape=False, index=False, classes="styled-table")

st.markdown(f"""
<style>
.styled-table {{
  width:100%; border-collapse:collapse; font-size:0.82rem;
}}
.styled-table th {{
  background:{NAVY}; color:white; padding:0.5rem 0.7rem; text-align:left;
  font-weight:600; letter-spacing:0.3px;
}}
.styled-table td {{
  padding:0.4rem 0.7rem; border-bottom:1px solid #E5E7EB; vertical-align:middle;
  color: #1A1A2E !important; background-color: #FFFFFF;
}}
.styled-table tr:nth-child(even) td {{ background-color: #F8FAFF; }}
.styled-table tr:hover td {{ background:#EEF2FF !important; }}
</style>
{table_html}
""", unsafe_allow_html=True)

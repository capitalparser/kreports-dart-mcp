import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from dashboard.db import get_company, get_dcf_inputs
from dashboard.styles import (
    CSS, page_header, kpi_card, section_title, insight, no_data,
    PRIMARY, NAVY, RED, GREEN, ORANGE, WHITE, BORDER, TEXT_DARK, TEXT_MID,
)

st.set_page_config(page_title="기업가치 — DCF 지원", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
from dashboard.sidebar import render_sidebar
render_sidebar()

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
    f"기업가치 — {corp_name}",
    "DCF 평가 지원 데이터 · 역사적 재무 추세 · FCF 분석"
), unsafe_allow_html=True)

# ── 도움말 ────────────────────────────────────────────────────────────────
with st.expander("이 페이지 안내", expanded=False):
    st.markdown("""
    **DCF(Discounted Cash Flow) 평가 모델 작성 시 필요한 역사적 재무 데이터**를 제공합니다.

    - **NOPAT**: 영업이익 × (1 - 실효세율) — 세후 순영업이익
    - **EBITDA**: 영업이익 + 감가상각비 및 무형자산상각비
    - **Unlevered FCF**: NOPAT + D&A - CapEx - Delta NWC — 레버리지 제거 잉여현금흐름
    - **실효세율**: 법인세비용 / 세전이익 (0~50% 클램핑, 산출 불가 시 22% 법정세율 적용)
    - **Net Debt**: 총차입금 - 현금성자산

    CSV 다운로드로 Excel DCF 모델에 바로 활용할 수 있습니다.
    """)

# ── FS 구분 ────────────────────────────────────────────────────────────────
_fs_default = 0 if st.session_state.get("fs_div", "CFS") == "CFS" else 1
fs_choice = st.radio("재무제표 구분", ["CFS (연결)", "OFS (별도)"],
                     horizontal=True, index=_fs_default)
fs_div = "CFS" if "CFS" in fs_choice else "OFS"

# ── 데이터 로딩 ────────────────────────────────────────────────────────────
with st.spinner("DCF 입력 데이터 산출 중..."):
    df = get_dcf_inputs(corp_code, fs_div)

if df.empty:
    st.markdown(no_data(f"{corp_name} DCF 입력 데이터를 산출할 수 없습니다."), unsafe_allow_html=True)
    from dashboard.collector import render_collect_button
    render_collect_button(corp_code, stock)
    st.stop()

latest = df.iloc[-1]
prev = df.iloc[-2] if len(df) >= 2 else None

# ── KPI 카드 ───────────────────────────────────────────────────────────────
st.markdown(section_title("핵심 가치 지표"), unsafe_allow_html=True)
c1, c2, c3, c4 = st.columns(4)

# 매출 CAGR
cagr_v = latest.get("매출_CAGR")
cagr_fmt = f"{cagr_v:.1f}%" if cagr_v is not None and pd.notna(cagr_v) else "-"
c1.markdown(kpi_card(
    "매출 CAGR", cagr_fmt,
    f"{len(df)}개년 복합성장률",
    "ok" if cagr_v is not None and pd.notna(cagr_v) and cagr_v > 0 else "warn",
    tooltip="Compound Annual Growth Rate. 수집 기간 전체의 매출 연평균 복합성장률.",
), unsafe_allow_html=True)

# EBITDA 마진
ebitda_v = latest.get("EBITDA")
rev_v = latest.get("매출액")
ebitda_margin = None
if ebitda_v is not None and rev_v is not None and pd.notna(ebitda_v) and pd.notna(rev_v) and rev_v != 0:
    ebitda_margin = ebitda_v / rev_v * 100
ebitda_fmt = f"{ebitda_margin:.1f}%" if ebitda_margin is not None else "-"
c2.markdown(kpi_card(
    "EBITDA 마진", ebitda_fmt,
    f"EBITDA {ebitda_v:,.0f}억원" if ebitda_v is not None and pd.notna(ebitda_v) else "",
    "ok" if ebitda_margin is not None and ebitda_margin > 10 else "warn",
    tooltip="EBITDA / 매출액. 영업이익 + 감가상각비. 현금 기반 영업 수익성 지표.",
), unsafe_allow_html=True)

# ROIC (실효세율 기준)
nopat_v = latest.get("NOPAT")
roic_real = None
invested_cap = latest.get("투하자본") if "투하자본" in df.columns else None
if nopat_v is not None and invested_cap is not None and pd.notna(nopat_v) and pd.notna(invested_cap) and invested_cap > 0:
    roic_real = nopat_v / invested_cap * 100
roic_fmt = f"{roic_real:.1f}%" if roic_real is not None else "-"
c3.markdown(kpi_card(
    "ROIC (실효세율)", roic_fmt,
    "NOPAT / 투하자본",
    "ok" if roic_real is not None and roic_real > 5 else ("warn" if roic_real is not None and roic_real > 0 else "bad"),
    tooltip="실효세율 기반 ROIC. 법인세비용/세전이익으로 산출한 실제 세율 적용. 기본 ROIC(22% 가정)보다 정확.",
), unsafe_allow_html=True)

# Unlevered FCF
ufcf_v = latest.get("Unlevered_FCF")
ufcf_fmt = f"{ufcf_v:,.0f}억원" if ufcf_v is not None and pd.notna(ufcf_v) else "-"
c4.markdown(kpi_card(
    "Unlevered FCF", ufcf_fmt,
    "NOPAT+D&A-CapEx-ΔNWC",
    "ok" if ufcf_v is not None and pd.notna(ufcf_v) and ufcf_v > 0 else "bad",
    tooltip="레버리지 제거 잉여현금흐름. DCF 모델의 핵심 입력값. 자본구조 중립적 현금창출력.",
), unsafe_allow_html=True)

# ── 역사적 재무 요약 테이블 ────────────────────────────────────────────────
st.markdown(section_title("역사적 DCF 입력 데이터"), unsafe_allow_html=True)

_display_cols = ["연도"]
_possible_cols = [
    "매출액", "영업이익", "D&A", "EBITDA", "NOPAT",
    "CapEx", "Delta_NWC", "Unlevered_FCF",
    "실효세율", "영업이익률", "Net_Debt",
]
for c in _possible_cols:
    if c in df.columns:
        _display_cols.append(c)

disp = df[_display_cols].copy()

# 포맷: 금액은 소수 1자리, 비율은 %
for col in disp.columns:
    if col in ("실효세율", "영업이익률", "매출_CAGR"):
        disp[col] = pd.to_numeric(disp[col], errors="coerce").apply(
            lambda x: f"{x:.1f}%" if pd.notna(x) else "-"
        )
    elif col != "연도":
        disp[col] = pd.to_numeric(disp[col], errors="coerce").apply(
            lambda x: f"{x:,.0f}" if pd.notna(x) else "-"
        )

st.dataframe(disp, use_container_width=True, hide_index=True)

# ── CSV 다운로드 ───────────────────────────────────────────────────────────
_csv_cols = [c for c in [
    "연도", "매출액", "영업이익", "순이익", "D&A", "EBITDA", "NOPAT",
    "영업CF", "CapEx", "FCF", "NWC", "Delta_NWC", "Unlevered_FCF",
    "실효세율", "영업이익률", "ROIC", "총차입금", "Net_Debt", "매출_CAGR",
] if c in df.columns]
_csv_data = df[_csv_cols].to_csv(index=False).encode("utf-8-sig")
st.download_button(
    "CSV 다운로드 (DCF 입력 데이터)",
    data=_csv_data,
    file_name=f"{corp_name}_DCF입력_{fs_div}.csv",
    mime="text/csv",
)

# ── EBITDA & NOPAT 추세 차트 ──────────────────────────────────────────────
st.markdown(section_title("수익성 추세"), unsafe_allow_html=True)
col_ch1, col_ch2 = st.columns(2)

with col_ch1:
    if "EBITDA" in df.columns and df["EBITDA"].notna().any():
        fig = go.Figure()
        fig.add_bar(x=df["연도"], y=df["매출액"], name="매출액", marker_color="#B8C9F5")
        fig.add_bar(x=df["연도"], y=df["EBITDA"], name="EBITDA", marker_color=PRIMARY)
        if "NOPAT" in df.columns:
            fig.add_bar(x=df["연도"], y=df["NOPAT"], name="NOPAT", marker_color=NAVY)
        fig.update_layout(
            barmode="group", height=300, margin=dict(t=10, b=10, l=0, r=0),
            yaxis_title="억원", plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333"),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
            legend=dict(orientation="h", y=-0.15, font_size=11),
        )
        st.plotly_chart(fig, use_container_width=True)

with col_ch2:
    # 실효세율 & 영업이익률 추이
    if "실효세율" in df.columns:
        fig2 = go.Figure()
        fig2.add_scatter(x=df["연도"], y=df["영업이익률"], mode="lines+markers",
                         name="영업이익률", line=dict(color=PRIMARY, width=2.5), marker_size=7)
        fig2.add_scatter(x=df["연도"], y=df["실효세율"], mode="lines+markers",
                         name="실효세율", line=dict(color=ORANGE, width=2, dash="dot"), marker_size=7)
        fig2.add_hline(y=22, line_dash="dash", line_color="#CCCCCC", line_width=1,
                       annotation_text="법정세율 22%", annotation_font_size=10)
        fig2.update_layout(
            height=300, margin=dict(t=10, b=10, l=0, r=0),
            yaxis_title="%", plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333"),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
            legend=dict(orientation="h", y=-0.15, font_size=11),
        )
        st.plotly_chart(fig2, use_container_width=True)

# ── FCF 워터폴 차트 ───────────────────────────────────────────────────────
if len(df) >= 1 and "Unlevered_FCF" in df.columns:
    st.markdown(section_title(f"FCF 브리지 — {int(latest['연도'])}년"), unsafe_allow_html=True)

    nopat_val = latest.get("NOPAT", 0) or 0
    da_val = latest.get("D&A", 0) or 0
    capex_val = latest.get("CapEx", 0) or 0
    dnwc_val = latest.get("Delta_NWC", 0) or 0
    ufcf_val = latest.get("Unlevered_FCF", 0) or 0

    fig_wf = go.Figure(go.Waterfall(
        x=["NOPAT", "+D&A", "-CapEx", "-ΔNWC", "=Unlevered FCF"],
        y=[nopat_val, da_val, -capex_val, -dnwc_val, ufcf_val],
        measure=["relative", "relative", "relative", "relative", "total"],
        connector=dict(line=dict(color="#D1D8F0")),
        increasing=dict(marker=dict(color=GREEN)),
        decreasing=dict(marker=dict(color=RED)),
        totals=dict(marker=dict(color=PRIMARY)),
        text=[f"{v:,.0f}" for v in [nopat_val, da_val, -capex_val, -dnwc_val, ufcf_val]],
        textposition="outside",
        textfont=dict(size=11, color="#333333"),
    ))
    fig_wf.update_layout(
        height=320, margin=dict(t=20, b=20, l=0, r=0),
        plot_bgcolor="white", paper_bgcolor="white",
        font=dict(color="#333333"),
        yaxis=dict(title="억원", gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
        xaxis=dict(tickfont=dict(color="#333333")),
        showlegend=False,
    )
    st.plotly_chart(fig_wf, use_container_width=True)

# ── 자본 구조 ──────────────────────────────────────────────────────────────
if "Net_Debt" in df.columns and df["Net_Debt"].notna().any():
    st.markdown(section_title("자본 구조"), unsafe_allow_html=True)

    nd_v = latest.get("Net_Debt")
    td_v = latest.get("총차입금")
    eq_v = latest.get("자본총계") if "자본총계" in df.columns else None

    nc1, nc2, nc3 = st.columns(3)
    nc1.markdown(kpi_card(
        "총차입금", f"{td_v:,.0f}억원" if td_v is not None and pd.notna(td_v) else "-",
        "단기+유동성장기+장기", "ok",
    ), unsafe_allow_html=True)
    nc2.markdown(kpi_card(
        "Net Debt", f"{nd_v:,.0f}억원" if nd_v is not None and pd.notna(nd_v) else "-",
        "총차입금 - 현금성자산",
        "warn" if nd_v is not None and pd.notna(nd_v) and nd_v > 0 else "ok",
    ), unsafe_allow_html=True)
    if eq_v is not None and pd.notna(eq_v) and eq_v > 0 and td_v is not None and pd.notna(td_v):
        de_ratio = td_v / eq_v
        nc3.markdown(kpi_card(
            "D/E Ratio", f"{de_ratio:.2f}x",
            "총차입금 / 자본총계",
            "bad" if de_ratio > 2 else ("warn" if de_ratio > 1 else "ok"),
        ), unsafe_allow_html=True)

st.caption(
    "WACC 산출에 필요한 베타·무위험이자율·시장위험프리미엄은 외부 데이터가 필요하므로 포함되지 않습니다. "
    "CSV를 다운로드하여 Excel DCF 모델에서 직접 입력하세요."
)

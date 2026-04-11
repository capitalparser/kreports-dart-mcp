import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
from dashboard.db import get_financials, get_financials_extended, get_company
from dashboard.styles import CSS, page_header, kpi_card, section_title, insight, no_data, PRIMARY, NAVY, RED, GREEN, ORANGE

st.set_page_config(page_title="재무 요약", layout="wide")
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

st.markdown(page_header(
    f"재무 요약 — {company['corp_name']}",
    f"{stock} | {company['market']}"
), unsafe_allow_html=True)

# FS 구분 선택 (사이드바 설정 연동)
_fs_default = 0 if st.session_state.get("fs_div", "CFS") == "CFS" else 1
fs_div = st.radio("재무제표 구분", ["CFS (연결)", "OFS (별도)"], horizontal=True, index=_fs_default)
fs_div_code = "CFS" if "CFS" in fs_div else "OFS"

with st.spinner("재무 데이터 로딩 중..."):
    df = get_financials(company["corp_code"], fs_div_code)
if df.empty:
    from dashboard.db import has_been_collected
    _collected = has_been_collected(company["corp_code"], "financial")
    if _collected:
        st.markdown(no_data(f"{company['corp_name']} 재무 데이터가 DART에 없습니다.", state="empty"), unsafe_allow_html=True)
    else:
        st.markdown(no_data(f"{company['corp_name']} 재무 데이터가 아직 수집되지 않았습니다."), unsafe_allow_html=True)
        from dashboard.collector import render_collect_button
        render_collect_button(company["corp_code"], stock)
    st.stop()

annual = df[df["분기"] == 4].copy()
latest = annual.iloc[-1] if not annual.empty else df.iloc[-1]
prev = annual.iloc[-2] if len(annual) >= 2 else None

# ── KPI 카드 ──────────────────────────────────────────────────────────────
st.markdown(section_title("핵심 지표"), unsafe_allow_html=True)

def _delta(cur, prev_val):
    if cur is None or pd.isna(cur) or prev_val is None or pd.isna(prev_val):
        return ""
    d = cur - prev_val
    sign = "▲" if d > 0 else "▼"
    color = GREEN if d > 0 else RED
    return f"<span style='color:{color}; font-size:0.72rem'>{sign} {abs(d):,.1f}</span>"

cols = st.columns(5)
metrics = [
    ("매출액", "억원", "revenue"),
    ("영업이익", "억원", "operating_profit"),
    ("순이익", "억원", "net_income"),
    ("영업이익률", "%", "operating_profit_rate"),
    ("부채비율", "%", "debt_ratio"),
]

val_map = {
    "revenue": latest.get("매출액"),
    "operating_profit": latest.get("영업이익"),
    "net_income": latest.get("순이익"),
    "operating_profit_rate": latest.get("영업이익률"),
    "debt_ratio": latest.get("부채비율"),
}
prev_map = {}
if prev is not None:
    prev_map = {
        "revenue": prev.get("매출액"),
        "operating_profit": prev.get("영업이익"),
        "net_income": prev.get("순이익"),
        "operating_profit_rate": prev.get("영업이익률"),
        "debt_ratio": prev.get("부채비율"),
    }

for col, (label, unit, key) in zip(cols, metrics):
    v = val_map.get(key)
    p = prev_map.get(key)
    fmt = f"{v:,.0f}{unit}" if v is not None and not pd.isna(v) else "-"
    delta_html = _delta(v, p)
    risk = "bad" if key == "debt_ratio" and v is not None and not pd.isna(v) and v > 200 else "ok"
    col.markdown(kpi_card(label, fmt, delta_html, risk), unsafe_allow_html=True)

# 2행: ROE / ROA / 자본총계 / 영업CF / 발생액비율
cols2 = st.columns(5)
roe_v = latest.get("ROE")
roa_v = latest.get("ROA")
roe_p = prev.get("ROE") if prev is not None else None
roa_p = prev.get("ROA") if prev is not None else None
eq_v = latest.get("자본총계")
ocf_v = latest.get("영업CF")
ocf_p = prev.get("영업CF") if prev is not None else None
accrual_v = latest.get("발생액비율")

def _roe_risk(v): return "bad" if v is not None and not pd.isna(v) and v < 0 else "ok"

cols2[0].markdown(kpi_card(
    "ROE", f"{roe_v:.1f}%" if roe_v is not None and not pd.isna(roe_v) else "-",
    _delta(roe_v, roe_p), _roe_risk(roe_v),
), unsafe_allow_html=True)
cols2[1].markdown(kpi_card(
    "ROA", f"{roa_v:.1f}%" if roa_v is not None and not pd.isna(roa_v) else "-",
    _delta(roa_v, roa_p), _roe_risk(roa_v),
), unsafe_allow_html=True)
cols2[2].markdown(kpi_card(
    "자본총계", f"{eq_v:,.0f}억원" if eq_v is not None and not pd.isna(eq_v) else "-",
    "", "ok",
), unsafe_allow_html=True)
cols2[3].markdown(kpi_card(
    "영업CF",
    f"{ocf_v:,.0f}억원" if ocf_v is not None and not pd.isna(ocf_v) else "-",
    _delta(ocf_v, ocf_p),
    "bad" if ocf_v is not None and not pd.isna(ocf_v) and ocf_v < 0 else "ok",
    tooltip="영업활동현금흐름. 음수 지속 시 이익 품질 저하 또는 운전자본 악화 시그널.",
), unsafe_allow_html=True)
cols2[4].markdown(kpi_card(
    "발생액비율",
    f"{accrual_v:.2f}" if accrual_v is not None and not pd.isna(accrual_v) else "-",
    "",
    "bad" if accrual_v is not None and not pd.isna(accrual_v) and abs(accrual_v) > 1.0 else "ok",
    tooltip="발생액비율 = (순이익 - 영업CF) / |순이익|. |1.0| 초과 시 현금 뒷받침 없는 장부 이익 비중 과다 (Sloan 1996).",
), unsafe_allow_html=True)

# ── CSV 다운로드 ──────────────────────────────────────────────────────────
if not annual.empty:
    _csv_cols = [c for c in ["연도", "매출액", "영업이익", "순이익", "영업이익률", "순이익률",
                              "부채비율", "ROE", "ROA", "자본총계", "영업CF", "발생액비율"] if c in annual.columns]
    _csv_data = annual[_csv_cols].to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "CSV 다운로드 (연간 재무요약)",
        data=_csv_data,
        file_name=f"{company['corp_name']}_재무요약_{fs_div_code}.csv",
        mime="text/csv",
    )

# ── 차트 영역 ──────────────────────────────────────────────────────────────
st.markdown(section_title("수익성 추세"), unsafe_allow_html=True)
col1, col2 = st.columns(2)

with col1:
    # 매출·영업이익·순이익 막대
    if not annual.empty:
        fig = go.Figure()
        fig.add_bar(x=annual["연도"], y=annual["매출액"], name="매출액",
                    marker_color="#B8C9F5", text=annual["매출액"].apply(lambda x: f"{x:,.0f}" if x else ""),
                    textposition="outside", textfont_size=9)
        fig.add_bar(x=annual["연도"], y=annual["영업이익"], name="영업이익",
                    marker_color=PRIMARY)
        fig.add_bar(x=annual["연도"], y=annual["순이익"], name="순이익",
                    marker_color=NAVY)
        fig.update_layout(
            barmode="group", height=300, margin=dict(t=10, b=10, l=0, r=0),
            legend=dict(orientation="h", y=-0.15, font_size=11),
            yaxis_title="억원", plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333"),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
        )
        st.plotly_chart(fig, use_container_width=True)

with col2:
    # 이익률 추세
    if not annual.empty:
        fig2 = go.Figure()
        fig2.add_scatter(x=annual["연도"], y=annual["영업이익률"],
                         mode="lines+markers", name="영업이익률",
                         line=dict(color=PRIMARY, width=2.5), marker_size=7)
        fig2.add_scatter(x=annual["연도"], y=annual["순이익률"],
                         mode="lines+markers", name="순이익률",
                         line=dict(color=NAVY, width=2.5, dash="dot"), marker_size=7)
        fig2.add_hline(y=0, line_dash="dash", line_color="#CCCCCC", line_width=1)
        fig2.update_layout(
            height=300, margin=dict(t=10, b=10, l=0, r=0),
            legend=dict(orientation="h", y=-0.15, font_size=11),
            yaxis_title="%", plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333"),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
        )
        st.plotly_chart(fig2, use_container_width=True)

# ── 재무건전성 ──────────────────────────────────────────────────────────────
st.markdown(section_title("재무건전성"), unsafe_allow_html=True)
col3, col4 = st.columns(2)

with col3:
    # 부채비율 + ROE
    if not annual.empty:
        fig3 = go.Figure()
        fig3.add_bar(x=annual["연도"], y=annual["부채비율"],
                     name="부채비율", marker_color="#F0F4FF",
                     marker_line_color=PRIMARY, marker_line_width=1.5)
        fig3.add_scatter(x=annual["연도"], y=annual["ROE"],
                         mode="lines+markers", name="ROE",
                         line=dict(color=RED, width=2), marker_size=7,
                         yaxis="y2")
        fig3.add_hline(y=200, line_dash="dash", line_color=RED,
                       line_width=1, annotation_text="부채비율 200%",
                       annotation_font_size=10)
        fig3.update_layout(
            height=300, margin=dict(t=10, b=10, l=0, r=0),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(title="부채비율 (%)", gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
            yaxis2=dict(title="ROE (%)", overlaying="y", side="right", showgrid=False, tickfont=dict(color="#333333")),
            legend=dict(orientation="h", y=-0.15, font_size=11),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333"),
        )
        st.plotly_chart(fig3, use_container_width=True)

with col4:
    # 자산·부채·자본 구조
    if not annual.empty:
        fig4 = go.Figure()
        fig4.add_scatter(x=annual["연도"], y=annual["자산총계"],
                         mode="lines+markers", name="자산총계",
                         line=dict(color=NAVY, width=2.5), marker_size=7)
        fig4.add_scatter(x=annual["연도"], y=annual["부채총계"],
                         mode="lines+markers", name="부채총계",
                         line=dict(color=RED, width=2, dash="dot"), marker_size=7)
        fig4.add_scatter(x=annual["연도"], y=annual["자본총계"],
                         mode="lines+markers", name="자본총계",
                         line=dict(color=PRIMARY, width=2), marker_size=7)
        fig4.update_layout(
            height=300, margin=dict(t=10, b=10, l=0, r=0),
            yaxis_title="억원", plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333"),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
            legend=dict(orientation="h", y=-0.15, font_size=11),
        )
        st.plotly_chart(fig4, use_container_width=True)

# ── 자본배분 & 현금흐름 품질 ───────────────────────────────────────────────
st.markdown(section_title("자본배분 & 현금흐름 품질"), unsafe_allow_html=True)

ext_df = get_financials_extended(company["corp_code"], fs_div_code)
ext_annual = ext_df[ext_df["분기"] == 4].copy() if not ext_df.empty else pd.DataFrame()

if ext_annual.empty:
    st.markdown(no_data("확장 재무 데이터가 없습니다."), unsafe_allow_html=True)
else:
    # 최근 연도 값 추출
    latest_ext = ext_annual.iloc[-1]
    prev_ext = ext_annual.iloc[-2] if len(ext_annual) >= 2 else None

    def _fv(v, unit="%", prec=1):
        if v is None or pd.isna(v):
            return "-"
        if unit == "억원":
            return f"{v:,.0f}억원"
        if unit == "배":
            return f"{v:.2f}x"
        return f"{v:.{prec}f}%"

    roic_v = latest_ext.get("ROIC")
    fcfm_v = latest_ext.get("FCF마진")
    capex_ocf_v = latest_ext.get("CapEx_OCF")
    cfo_ni_v = latest_ext.get("CFO_NI")
    fcf_v = latest_ext.get("FCF")
    int_cov_v = latest_ext.get("이자보상배율")

    def _ratio_risk(v, good_min=None, bad_max=None):
        if v is None or pd.isna(v):
            return "ok"
        if bad_max is not None and v < bad_max:
            return "bad"
        if good_min is not None and v < good_min:
            return "warn"
        return "ok"

    kc1, kc2, kc3, kc4 = st.columns(4)
    kc1.markdown(kpi_card(
        "ROIC",
        _fv(roic_v, "%"),
        "투하자본 수익률 (세후)",
        _ratio_risk(roic_v, good_min=5, bad_max=0),
        tooltip="ROIC = 영업이익×(1-0.22) / (자산총계 - 현금성자산). 5% 미만 시 자본비용 회수 미달 가능성. 법인세율 22% 가정.",
    ), unsafe_allow_html=True)
    kc2.markdown(kpi_card(
        "FCF 마진",
        _fv(fcfm_v, "%"),
        f"FCF {fcf_v:,.0f}억원" if fcf_v is not None and pd.notna(fcf_v) else "FCF -",
        _ratio_risk(fcfm_v, good_min=3, bad_max=0),
        tooltip="FCF 마진 = (영업CF - CapEx) / 매출액. 사업 운영에서 실제 남는 현금 비중. 마이너스는 외부 자금 의존 상태.",
    ), unsafe_allow_html=True)
    kc3.markdown(kpi_card(
        "CapEx / OCF",
        _fv(capex_ocf_v, "%"),
        "자본적지출 재투자 비율",
        "warn" if (capex_ocf_v is not None and pd.notna(capex_ocf_v) and capex_ocf_v > 100) else "ok",
        tooltip="CapEx / 영업CF. 100% 초과 시 영업현금으로 투자활동 충당 불가 — 외부 조달 의존.",
    ), unsafe_allow_html=True)
    kc4.markdown(kpi_card(
        "CFO / NI",
        f"{cfo_ni_v:.2f}x" if cfo_ni_v is not None and pd.notna(cfo_ni_v) else "-",
        "이익의 현금 전환 배수",
        "bad" if (cfo_ni_v is not None and pd.notna(cfo_ni_v) and cfo_ni_v < 0.8) else "ok",
        tooltip="영업CF / 순이익. 1.0 미만이 지속되면 이익이 현금으로 실현되지 않음을 시사 — 발생액 품질 저하.",
    ), unsafe_allow_html=True)

    # 이자보상배율 추가 (별도 라인)
    kc5, _, _, _ = st.columns(4)
    kc5.markdown(kpi_card(
        "이자보상배율",
        f"{int_cov_v:.1f}x" if int_cov_v is not None and pd.notna(int_cov_v) else "-",
        "영업이익 / 이자비용",
        "bad" if (int_cov_v is not None and pd.notna(int_cov_v) and int_cov_v < 1.0) else (
            "warn" if (int_cov_v is not None and pd.notna(int_cov_v) and int_cov_v < 3.0) else "ok"
        ),
        tooltip="이자보상배율 = 영업이익 / 이자비용. 1.0 미만 → 영업이익으로 이자도 못 갚는 한계기업 후보. 3.0 미만 → 재무 부담 관찰 필요.",
    ), unsafe_allow_html=True)

    # ROIC & FCF 마진 추이 차트
    col_cap1, col_cap2 = st.columns(2)
    with col_cap1:
        roic_series = pd.to_numeric(ext_annual["ROIC"], errors="coerce")
        fcfm_series = pd.to_numeric(ext_annual["FCF마진"], errors="coerce")
        if roic_series.notna().any() or fcfm_series.notna().any():
            fig_cap = go.Figure()
            if roic_series.notna().any():
                fig_cap.add_scatter(
                    x=ext_annual["연도"], y=roic_series,
                    mode="lines+markers+text", name="ROIC",
                    line=dict(color=PRIMARY, width=2.5), marker_size=8,
                    text=[f"{v:.1f}%" if pd.notna(v) else "" for v in roic_series],
                    textposition="top center", textfont_size=10,
                )
            if fcfm_series.notna().any():
                fig_cap.add_scatter(
                    x=ext_annual["연도"], y=fcfm_series,
                    mode="lines+markers", name="FCF 마진",
                    line=dict(color=ORANGE, width=2, dash="dot"), marker_size=7,
                )
            fig_cap.add_hline(y=0, line_color="#CCCCCC", line_dash="dash", line_width=1)
            fig_cap.update_layout(
                title=dict(text="ROIC & FCF 마진 추이 (%)", font_size=12, font_color="#333333"),
                height=270, margin=dict(t=35, b=10, l=0, r=0),
                plot_bgcolor="white", paper_bgcolor="white", font=dict(color="#333333"),
                xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
                yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
                legend=dict(orientation="h", y=-0.15, font_size=11),
            )
            st.plotly_chart(fig_cap, use_container_width=True)
        else:
            st.markdown(insight("ROIC/FCF 마진 계산에 필요한 XBRL 계정이 탐지되지 않았습니다.", "warn"), unsafe_allow_html=True)

    with col_cap2:
        # CapEx vs 영업CF 비교 차트
        capex_series = pd.to_numeric(ext_annual["CapEx"], errors="coerce")
        ocf_series = pd.to_numeric(ext_annual["영업CF"], errors="coerce")
        if capex_series.notna().any() and ocf_series.notna().any():
            fig_cx = go.Figure()
            fig_cx.add_bar(
                x=ext_annual["연도"], y=ocf_series,
                name="영업CF", marker_color=PRIMARY,
            )
            fig_cx.add_bar(
                x=ext_annual["연도"], y=capex_series,
                name="CapEx", marker_color=ORANGE,
            )
            fig_cx.update_layout(
                barmode="group",
                title=dict(text="영업CF vs CapEx (억원)", font_size=12, font_color="#333333"),
                height=270, margin=dict(t=35, b=10, l=0, r=0),
                plot_bgcolor="white", paper_bgcolor="white", font=dict(color="#333333"),
                xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
                yaxis=dict(title="억원", gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
                legend=dict(orientation="h", y=-0.15, font_size=11),
            )
            st.plotly_chart(fig_cx, use_container_width=True)
        else:
            st.markdown(insight("CapEx 또는 영업CF 데이터가 없어 차트를 표시할 수 없습니다.", "warn"), unsafe_allow_html=True)

# ── 운전자본 사이클 ───────────────────────────────────────────────────────
if not ext_annual.empty:
    # CCC 계산 가능 여부 확인: DIO/DSO/DPO 중 하나라도 값이 있어야 표시
    dio_has = pd.to_numeric(ext_annual["DIO"], errors="coerce").notna().any()
    dso_has = pd.to_numeric(ext_annual["DSO"], errors="coerce").notna().any()
    dpo_has = pd.to_numeric(ext_annual["DPO"], errors="coerce").notna().any()

    if dio_has or dso_has or dpo_has:
        st.markdown(section_title("운전자본 사이클 (Cash Conversion Cycle)"), unsafe_allow_html=True)
        st.caption(
            "DIO = 365 × 재고 / 매출원가 · DSO = 365 × 매출채권 / 매출 · DPO = 365 × 매입채무 / 매출원가 · CCC = DIO + DSO − DPO. "
            "CCC가 감소 추세면 운전자본 효율이 개선되는 것이고, 급격한 증가는 재고 적체·회수 지연 신호일 수 있습니다."
        )

        latest_ccc = latest_ext.get("CCC")
        dio_v = latest_ext.get("DIO")
        dso_v = latest_ext.get("DSO")
        dpo_v = latest_ext.get("DPO")

        wc1, wc2, wc3, wc4 = st.columns(4)
        wc1.markdown(kpi_card(
            "DIO (재고회전일)",
            f"{dio_v:.0f}일" if dio_v is not None and pd.notna(dio_v) else "-",
            "365 × 재고 / 매출원가",
            "ok",
        ), unsafe_allow_html=True)
        wc2.markdown(kpi_card(
            "DSO (매출채권회전일)",
            f"{dso_v:.0f}일" if dso_v is not None and pd.notna(dso_v) else "-",
            "365 × 매출채권 / 매출",
            "ok",
        ), unsafe_allow_html=True)
        wc3.markdown(kpi_card(
            "DPO (매입채무회전일)",
            f"{dpo_v:.0f}일" if dpo_v is not None and pd.notna(dpo_v) else "-",
            "365 × 매입채무 / 매출원가",
            "ok",
        ), unsafe_allow_html=True)
        wc4.markdown(kpi_card(
            "CCC",
            f"{latest_ccc:.0f}일" if latest_ccc is not None and pd.notna(latest_ccc) else "-",
            "현금 전환 주기",
            "warn" if (latest_ccc is not None and pd.notna(latest_ccc) and latest_ccc > 120) else "ok",
        ), unsafe_allow_html=True)

        # 운전자본 사이클 추이 차트
        dio_s = pd.to_numeric(ext_annual["DIO"], errors="coerce")
        dso_s = pd.to_numeric(ext_annual["DSO"], errors="coerce")
        dpo_s = pd.to_numeric(ext_annual["DPO"], errors="coerce")
        ccc_s = pd.to_numeric(ext_annual["CCC"], errors="coerce")

        fig_wc = go.Figure()
        if dio_s.notna().any():
            fig_wc.add_scatter(x=ext_annual["연도"], y=dio_s, mode="lines+markers", name="DIO",
                               line=dict(color=PRIMARY, width=2), marker_size=6)
        if dso_s.notna().any():
            fig_wc.add_scatter(x=ext_annual["연도"], y=dso_s, mode="lines+markers", name="DSO",
                               line=dict(color=NAVY, width=2), marker_size=6)
        if dpo_s.notna().any():
            fig_wc.add_scatter(x=ext_annual["연도"], y=dpo_s, mode="lines+markers", name="DPO",
                               line=dict(color=ORANGE, width=2), marker_size=6)
        if ccc_s.notna().any():
            fig_wc.add_scatter(x=ext_annual["연도"], y=ccc_s, mode="lines+markers+text", name="CCC",
                               line=dict(color=RED, width=2.5), marker_size=8,
                               text=[f"{v:.0f}" if pd.notna(v) else "" for v in ccc_s],
                               textposition="top center", textfont_size=10)
        fig_wc.update_layout(
            title=dict(text="운전자본 회전일 추이 (일)", font_size=12, font_color="#333333"),
            height=280, margin=dict(t=35, b=10, l=0, r=0),
            plot_bgcolor="white", paper_bgcolor="white", font=dict(color="#333333"),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(title="일", gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
            legend=dict(orientation="h", y=-0.15, font_size=11),
        )
        st.plotly_chart(fig_wc, use_container_width=True)

# ── 업종 벤치마킹 ──────────────────────────────────────────────────────────
st.markdown(section_title("업종 벤치마킹"), unsafe_allow_html=True)

_induty_code = company.get("induty_code")
if not _induty_code:
    st.markdown(
        insight(
            "업종코드(induty_code)가 비어 있어 업종 비교가 불가합니다. "
            "'dart enrich-market' 명령으로 업종코드를 수집하세요.",
            level="warn",
        ),
        unsafe_allow_html=True,
    )
else:
    import kreports as _kr

    _bench_metrics = ["영업이익률", "순이익률", "ROE", "ROA", "부채비율", "자기자본비율", "매출성장률", "Beneish_M"]
    _bench_prefix_len = st.radio(
        "업종 매칭 범위",
        options=[2, 3],
        format_func=lambda n: f"KSIC {n}자리 (" + ("대분류" if n == 2 else "중분류") + ")",
        horizontal=True,
        index=0,
    )

    _bench_rows = []
    _bench_raw = {}
    for _m in _bench_metrics:
        _agg = _kr.get_industry_aggregates(
            _induty_code,
            metric=_m,
            prefix_len=_bench_prefix_len,
            fs_div=fs_div,
            include_peers=True,
            peer_limit=50,
        )
        _bench_raw[_m] = _agg
        _me_val = None
        for _p in _agg.get("peers") or []:
            if _p.get("corp_code") == company["corp_code"]:
                _me_val = _p.get("value")
                break
        _bench_rows.append({
            "지표": _m,
            "단위": _agg.get("unit") or "",
            "기준 기업": _me_val,
            "P25": (_agg.get("quantiles") or {}).get("p25"),
            "중앙값(P50)": (_agg.get("quantiles") or {}).get("p50"),
            "P75": (_agg.get("quantiles") or {}).get("p75"),
            "min": (_agg.get("quantiles") or {}).get("min"),
            "max": (_agg.get("quantiles") or {}).get("max"),
            "peer 수": _agg.get("n"),
        })

    # 업종명 + 커버리지 인디케이터
    _first_meta = _bench_raw[_bench_metrics[0]]
    _ind_name = _first_meta.get("industry_name", _first_meta.get("match_prefix", ""))
    _total_ind = _first_meta.get("total_in_industry", 0)
    _n_peers = _first_meta.get("n", 0)
    _cov = _first_meta.get("coverage_pct", 0)

    st.caption(
        f"업종: **{_ind_name}** (KSIC {_first_meta.get('match_prefix')}) · "
        f"기준 연도 {_first_meta.get('year') or '-'} · {fs_div} · "
        f"peer {_n_peers} / 전체 {_total_ind}개 ({_cov}% 커버리지)"
    )

    # 데이터 상태 경고
    if _n_peers == 0:
        st.markdown(
            insight(
                f"업종 '{_ind_name}' 내 수집된 재무데이터가 없습니다. "
                "`kreports collect-seed` 명령으로 핵심 기업 데이터를 수집하세요 (~20분 소요).",
                level="warn",
            ),
            unsafe_allow_html=True,
        )
    elif _n_peers < 10:
        st.markdown(
            insight(
                f"peer {_n_peers}개로 통계적 의미가 제한적입니다. "
                "`kreports collect-seed --size medium`으로 추가 수집 권장.",
                level="info",
            ),
            unsafe_allow_html=True,
        )

    # 벤치마킹 테이블
    _bench_df = pd.DataFrame(_bench_rows)
    def _fmt_pct(v):
        return f"{v:.1f}" if v is not None and not pd.isna(v) else "-"
    _display_df = _bench_df.copy()
    for _col in ("기준 기업", "P25", "중앙값(P50)", "P75", "min", "max"):
        _display_df[_col] = _display_df[_col].apply(_fmt_pct)
    _display_df["peer 수"] = _display_df["peer 수"].apply(
        lambda n: f"{int(n):,}" if pd.notna(n) else "-"
    )
    st.dataframe(_display_df.set_index("지표"), use_container_width=True)

    # 박스플롯 — peer가 3개 이상인 metric만
    _plottable = [m for m in _bench_metrics if (_bench_raw[m].get("n") or 0) >= 3]
    if _plottable:
        import plotly.graph_objects as _go
        from plotly.subplots import make_subplots

        _n_plots = len(_plottable)
        _fig = make_subplots(rows=1, cols=_n_plots, subplot_titles=_plottable)

        for _i, _m in enumerate(_plottable, 1):
            _vals = [p["value"] for p in _bench_raw[_m].get("peers", []) if p.get("value") is not None]
            if not _vals:
                continue
            _fig.add_trace(
                _go.Box(y=_vals, name=_m, boxmean=True, marker_color=PRIMARY),
                row=1, col=_i,
            )
            # 기준 기업 위치 (빨간 점)
            _me_val = None
            for _p in _bench_raw[_m].get("peers", []):
                if _p.get("corp_code") == company["corp_code"]:
                    _me_val = _p.get("value")
                    break
            if _me_val is not None:
                _fig.add_trace(
                    _go.Scatter(
                        x=[_m], y=[_me_val], mode="markers",
                        marker=dict(color=RED, size=10, symbol="diamond"),
                        name=company["corp_name"],
                        showlegend=(_i == 1),
                    ),
                    row=1, col=_i,
                )

        _fig.update_layout(
            height=350,
            showlegend=True,
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333", size=11),
            margin=dict(t=40, b=20, l=30, r=10),
        )
        st.plotly_chart(_fig, use_container_width=True)

    # peer 리스트
    _best_metric = max(_bench_metrics, key=lambda m: _bench_raw[m].get("n") or 0)
    _best_agg = _bench_raw[_best_metric]
    if (_best_agg.get("n") or 0) >= 1:
        with st.expander(f"peer 기업 리스트 ({_best_metric} 기준, {_best_agg['n']}개사)"):
            _peer_df = pd.DataFrame(_best_agg.get("peers") or [])
            if not _peer_df.empty:
                _peer_df = _peer_df.rename(columns={
                    "corp_name": "기업명", "corp_code": "corp_code",
                    "induty_code": "KSIC",
                    "value": f"{_best_metric} ({_best_agg.get('unit') or ''})",
                })
                _peer_df = _peer_df[["기업명", "corp_code", "KSIC",
                                     f"{_best_metric} ({_best_agg.get('unit') or ''})"]]
                _peer_df.reset_index(drop=True, inplace=True)
                _peer_df.index += 1
                st.dataframe(_peer_df, use_container_width=True)

# ── 인사이트 ──────────────────────────────────────────────────────────────
st.markdown(section_title("자동 인사이트"), unsafe_allow_html=True)

insights = []
if not annual.empty and len(annual) >= 2:
    last = annual.iloc[-1]
    prev2 = annual.iloc[-2]

    if pd.notna(last["영업이익률"]) and last["영업이익률"] < 0:
        insights.append(("risk", f"최근 연도 영업이익률이 음수({last['영업이익률']:.1f}%)입니다. 영업 적자 상태입니다."))
    if pd.notna(last["부채비율"]) and last["부채비율"] > 200:
        insights.append(("warn", f"부채비율 {last['부채비율']:.0f}%로 200% 초과. 재무 레버리지가 높습니다."))
    if pd.notna(last["ROE"]) and pd.notna(prev2["ROE"]) and last["ROE"] < prev2["ROE"] - 5:
        insights.append(("warn", f"ROE가 {prev2['ROE']:.1f}% → {last['ROE']:.1f}%로 하락했습니다."))
    if pd.notna(last.get("매출성장률")) and last["매출성장률"] < -10:
        insights.append(("warn", f"매출액이 전년 대비 {last['매출성장률']:.1f}% 감소했습니다."))
    if not insights:
        insights.append(("info", "특이 이슈가 감지되지 않았습니다."))

for level, text in insights:
    st.markdown(insight(text, level), unsafe_allow_html=True)

# ── 분기 상세 테이블 ──────────────────────────────────────────────────────
with st.expander("분기별 상세 데이터"):
    show_cols = ["기간", "매출액", "영업이익", "순이익", "영업CF", "발생액비율", "영업이익률", "순이익률", "부채비율", "ROE", "ROA", "매핑신뢰도"]
    show_cols = [c for c in show_cols if c in df.columns]
    tbl = df[show_cols].copy()
    for col in ["매출액", "영업이익", "순이익", "영업CF"]:
        if col in tbl.columns:
            tbl[col] = tbl[col].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "-")
    for col in ["영업이익률", "순이익률", "부채비율", "ROE", "ROA"]:
        if col in tbl.columns:
            tbl[col] = tbl[col].apply(lambda x: f"{x:.1f}%" if pd.notna(x) else "-")
    if "발생액비율" in tbl.columns:
        tbl["발생액비율"] = tbl["발생액비율"].apply(lambda x: f"{x:.2f}" if pd.notna(x) else "-")
    if "매핑신뢰도" in tbl.columns:
        tbl["매핑신뢰도"] = tbl["매핑신뢰도"].apply(lambda x: f"{x:.0%}" if pd.notna(x) else "-")
    st.dataframe(tbl.set_index("기간"), use_container_width=True)

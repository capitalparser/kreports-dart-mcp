import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from dashboard.db import (
    get_financials_both, get_auditors, get_audit_fees, get_risk_summary, get_company,
    get_going_concern_score, get_restatement_delta,
)
from dashboard.styles import CSS, page_header, kpi_card, section_title, insight, no_data, PRIMARY, NAVY, RED, GREEN, ORANGE

st.set_page_config(page_title="위험 신호", layout="wide")
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
    f"위험 신호 — {company['corp_name']}",
    "감사 계획 수립 및 위험 평가 지원 · K-IFRS 기반 이상 탐지"
), unsafe_allow_html=True)

summary = get_risk_summary(company["corp_code"])

if not summary["has_data"]:
    st.markdown(no_data(f"{company['corp_name']} 재무 데이터가 아직 수집되지 않았습니다."), unsafe_allow_html=True)
    from dashboard.collector import render_collect_button
    render_collect_button(company["corp_code"], stock)
    st.stop()

# ── Going Concern Scorecard ──────────────────────────────────────────────
gc = get_going_concern_score(company["corp_code"])
if gc.get("has_data"):
    st.markdown(section_title("Going Concern Scorecard"), unsafe_allow_html=True)
    st.caption(
        "6개 인자(자본잠식·연속영업손실·부채비율·이자보상배율·영업CF·감사의견)를 감점 방식으로 합산해 100점 만점으로 산출합니다. "
        "개별 플래그보다 전체 등급으로 계속기업 가정의 건전성을 즉시 파악할 수 있도록 설계되었습니다."
    )

    gc_col1, gc_col2 = st.columns([1, 3])

    with gc_col1:
        # 큰 점수 카드
        grade_color = {"안정": GREEN, "주의": ORANGE, "경고": ORANGE, "위험": RED}.get(gc["grade"], GREEN)
        st.markdown(f"""
        <div class="kpi-card" style="border-top:4px solid {grade_color}; text-align:center; padding:1.2rem;">
          <div class="kpi-label">종합 점수</div>
          <div class="kpi-value" style="color:{grade_color}; font-size:2.2rem;">{gc['score']}<span style="font-size:1rem;color:#888;"> / 100</span></div>
          <div class="kpi-sub" style="color:{grade_color}; font-weight:700; font-size:1rem;">{gc['grade']}</div>
        </div>
        """, unsafe_allow_html=True)

    with gc_col2:
        # 6인자 breakdown — 2x3 그리드
        factors = gc.get("factors", [])
        f_rows = [factors[:3], factors[3:]]
        for f_row in f_rows:
            cols_f = st.columns(3)
            for col, f in zip(cols_f, f_row):
                hit_color = RED if f["hit"] else GREEN
                bg = "#FFF0F0" if f["hit"] else "#F0FFF4"
                mark = "⚠" if f["hit"] else "✓"
                penalty_txt = f"-{f['penalty']}점" if f["hit"] else "통과"
                col.markdown(f"""
                <div style="background:{bg}; border-left:3px solid {hit_color};
                            padding:0.55rem 0.8rem; border-radius:4px; margin-bottom:0.45rem;">
                  <div style="color:{hit_color}; font-weight:700; font-size:0.78rem;">
                    {mark} {f['name']} · {penalty_txt}
                  </div>
                  <div style="color:#5A6480; font-size:0.72rem; margin-top:0.2rem;">
                    {f['detail']}
                  </div>
                </div>
                """, unsafe_allow_html=True)

    st.markdown("<div style='margin:0.6rem 0'></div>", unsafe_allow_html=True)

# ── 위험 신호 요약 카드 ──────────────────────────────────────────────────
st.markdown(section_title("위험 신호 요약"), unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)

def _risk(v, t=0): return "bad" if v > t else "ok"

c1.markdown(kpi_card(
    "CFS/OFS 순이익 괴리",
    f"{summary['cfs_ofs_gap_count']}건",
    "연결·별도 차이 >20%",
    _risk(summary["cfs_ofs_gap_count"]),
), unsafe_allow_html=True)

c2.markdown(kpi_card(
    "계정 매핑 불완전",
    f"{summary['low_map_conf_count']}건",
    "핵심 계정 매핑 <80%",
    _risk(summary["low_map_conf_count"]),
), unsafe_allow_html=True)

c3.markdown(kpi_card(
    "감사인 교체",
    f"{summary['auditor_change_count']}회",
    "최근 5개년",
    _risk(summary["auditor_change_count"]),
), unsafe_allow_html=True)

c4.markdown(kpi_card(
    "비적정 감사의견",
    f"{summary['non_clean_opinion_count']}건",
    "한정·부적정·의견거절",
    _risk(summary["non_clean_opinion_count"]),
), unsafe_allow_html=True)

c5, c6, c7, c8 = st.columns(4)

c5.markdown(kpi_card(
    "자본잠식",
    f"{summary['equity_negative_count']}건",
    "자본총계 < 0",
    _risk(summary["equity_negative_count"]),
), unsafe_allow_html=True)

c6.markdown(kpi_card(
    "계속기업 우려",
    f"{summary['going_concern_count']}건",
    "영업이익·순이익 동시 적자",
    _risk(summary["going_concern_count"]),
), unsafe_allow_html=True)

c7.markdown(kpi_card(
    "영업·순이익 괴리",
    f"{summary['op_net_divergence_count']}건",
    "영업이익>0 & 순이익<0",
    _risk(summary["op_net_divergence_count"]),
), unsafe_allow_html=True)

_beneish_m = summary.get("latest_beneish_m")
_beneish_v = f"{_beneish_m:.2f}" if _beneish_m is not None else "-"
_beneish_risk = "bad" if summary["beneish_alert_count"] > 0 else "ok"
_BENEISH_TOOLTIP = (
    "Beneish(1999) 개발 이익조작 탐지 모델. "
    "8개 재무 인덱스(DSRI·GMI·AQI·SGI·DEPI·SGAI·TATA·LVGI)로 M-Score 산출. "
    "M > -1.78 이면 이익조작 가능성. "
    "미국 GAAP 기반 — K-IFRS 적용 시 별도 판단 필요."
)
c8.markdown(kpi_card(
    "Beneish M-Score",
    _beneish_v,
    f"이익조작탐지 임계 -1.78 ({summary['beneish_alert_count']}건 초과)",
    _beneish_risk,
    tooltip=_BENEISH_TOOLTIP,
), unsafe_allow_html=True)

# 3행: 추가 플래그 (기존에 계산됐지만 미표시)
c9, c10, c11, c12, c13 = st.columns(5)

c9.markdown(kpi_card(
    "매출 급변",
    f"{summary['revenue_vol_count']}건",
    "|매출성장률| ≥ 30%",
    _risk(summary["revenue_vol_count"]),
    tooltip="전년 동기 대비 매출 증감률 절댓값이 30% 이상인 분기 수. 급격한 변동은 사업 환경 변화 또는 비경상 거래 가능성을 시사합니다.",
), unsafe_allow_html=True)

c10.markdown(kpi_card(
    "영업CF 괴리",
    f"{summary['op_cf_divergence_count']}건",
    "영업이익>0 & 영업CF<0",
    _risk(summary["op_cf_divergence_count"]),
    tooltip="영업이익은 흑자인데 영업현금흐름이 적자인 기간 수. 이익의 현금 전환이 안 되는 구조 — 발생주의 이익 조작 또는 운전자본 악화 시그널.",
), unsafe_allow_html=True)

c11.markdown(kpi_card(
    "발생액 이상",
    f"{summary['high_accrual_count']}건",
    "|발생액비율| > 1.0",
    _risk(summary["high_accrual_count"]),
    tooltip="발생액비율 = (순이익 - 영업CF) / |순이익|. 절댓값 1.0 초과 시 이익 품질 저하. 높을수록 현금 뒷받침 없는 장부 이익 비중이 큼 (Sloan 1996).",
), unsafe_allow_html=True)

c12.markdown(kpi_card(
    "정정공시 빈발",
    f"{summary['amendment_heavy_count']}건",
    "연간 정정공시 ≥ 3건",
    _risk(summary["amendment_heavy_count"]),
    tooltip="해당 사업연도에 정정·수정 공시가 3건 이상 발생한 연도 수. 반복적 정정은 내부통제 취약 또는 고의적 오기재 가능성을 시사합니다.",
), unsafe_allow_html=True)

c13.markdown(kpi_card(
    "비감사보수 위험",
    f"{summary['nas_risk_count']}건",
    "NAS ratio > 1.0",
    _risk(summary["nas_risk_count"]),
    tooltip="비감사보수/감사보수 비율(NAS ratio)이 1.0 초과한 연도 수. 비감사용역 의존도가 높을수록 감사인 독립성 저하 위험이 증가합니다.",
), unsafe_allow_html=True)

# ── 재무 이상 탐지 ──────────────────────────────────────────────────────
st.markdown(section_title("재무 이상 탐지"), unsafe_allow_html=True)

df = get_financials_both(company["corp_code"])
if not df.empty:
    # 영업CF 억원 변환 (raw 단위 → 억원)
    _UNIT = 1e8
    if "영업CF" in df.columns:
        df["영업CF"] = pd.to_numeric(df["영업CF"], errors="coerce") / _UNIT

    # CFS 기준 분기별 이상 플래그 시각화
    cfs_df = df[df["구분"] == "CFS"].copy() if "구분" in df.columns else df.copy()

    if not cfs_df.empty and "매출액" in cfs_df.columns:
        annual_cfs = cfs_df[cfs_df["분기"] == 4].copy()
        if not annual_cfs.empty:
            ch1, ch2 = st.columns(2)

            with ch1:
                # 영업이익 vs 순이익 괴리 차트 (일회성 항목 감지용)
                fig = go.Figure()
                fig.add_bar(
                    x=annual_cfs["연도"], y=annual_cfs["영업이익"],
                    name="영업이익", marker_color=PRIMARY,
                )
                fig.add_scatter(
                    x=annual_cfs["연도"], y=annual_cfs["순이익"],
                    mode="lines+markers", name="순이익",
                    line=dict(color=RED, width=2.5), marker_size=8,
                )
                fig.add_hline(y=0, line_color="#AAAAAA", line_dash="dot", line_width=1)
                fig.update_layout(
                    title=dict(text="영업이익 vs 순이익 (억원) — 괴리 시 일회성 항목", font_size=12, font_color="#333333"),
                    height=270, margin=dict(t=35, b=10, l=0, r=0),
                    plot_bgcolor="white", paper_bgcolor="white",
                    font=dict(color="#333333"),
                    xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
                    yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
                    legend=dict(orientation="h", y=-0.2, font_size=11),
                )
                st.plotly_chart(fig, use_container_width=True)

            with ch2:
                # 영업이익 vs 영업CF 비교 차트
                if "영업CF" in annual_cfs.columns and annual_cfs["영업CF"].notna().any():
                    fig_cf = go.Figure()
                    fig_cf.add_bar(
                        x=annual_cfs["연도"], y=annual_cfs["영업이익"],
                        name="영업이익", marker_color=PRIMARY,
                    )
                    fig_cf.add_scatter(
                        x=annual_cfs["연도"], y=annual_cfs["영업CF"],
                        mode="lines+markers", name="영업CF",
                        line=dict(color=ORANGE, width=2.5), marker_size=8,
                    )
                    fig_cf.add_hline(y=0, line_color="#AAAAAA", line_dash="dot", line_width=1)
                    fig_cf.update_layout(
                        title=dict(text="영업이익 vs 영업CF (억원) — 괴리 시 발생액 이상", font_size=12, font_color="#333333"),
                        height=270, margin=dict(t=35, b=10, l=0, r=0),
                        plot_bgcolor="white", paper_bgcolor="white",
                        font=dict(color="#333333"),
                        xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
                        yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
                        legend=dict(orientation="h", y=-0.2, font_size=11),
                    )
                    st.plotly_chart(fig_cf, use_container_width=True)

            # 발생액비율 추세 차트
            if "발생액비율" in annual_cfs.columns and annual_cfs["발생액비율"].notna().any():
                accrual_vals = pd.to_numeric(annual_cfs["발생액비율"], errors="coerce")
                fig_ac = go.Figure()
                fig_ac.add_scatter(
                    x=annual_cfs["연도"], y=accrual_vals,
                    mode="lines+markers+text",
                    name="발생액비율",
                    line=dict(color=PRIMARY, width=2.5),
                    marker=dict(
                        size=10,
                        color=[RED if (pd.notna(v) and abs(v) > 1.0) else PRIMARY for v in accrual_vals],
                    ),
                    text=[f"{v:.2f}" if pd.notna(v) else "" for v in accrual_vals],
                    textposition="top center",
                    textfont=dict(size=10, color="#333333"),
                )
                fig_ac.add_hline(
                    y=1.0, line_color=RED, line_dash="dash", line_width=1.5,
                    annotation_text="+1.0 임계", annotation_position="right",
                    annotation_font_color=RED,
                )
                fig_ac.add_hline(
                    y=-1.0, line_color=RED, line_dash="dash", line_width=1.5,
                    annotation_text="-1.0 임계", annotation_position="right",
                    annotation_font_color=RED,
                )
                fig_ac.add_hline(y=0, line_color="#AAAAAA", line_dash="dot", line_width=1)
                fig_ac.update_layout(
                    title=dict(text="발생액비율 추세 — |1.0| 초과 시 이익 품질 저하 (Sloan 1996)", font_size=12, font_color="#333333"),
                    height=240, margin=dict(t=35, b=10, l=0, r=0),
                    plot_bgcolor="white", paper_bgcolor="white",
                    font=dict(color="#333333"),
                    xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
                    yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
                    showlegend=False,
                )
                st.plotly_chart(fig_ac, use_container_width=True)

    # CFS/OFS 괴리 플래그 테이블
    st.markdown("**CFS / OFS 분기별 플래그**")
    flag_cols = ["연도", "분기", "구분"]
    if "CFS_OFS괴리" in df.columns:
        flag_cols.append("CFS_OFS괴리")
    if "매핑신뢰도" in df.columns:
        flag_cols.append("매핑신뢰도")

    tbl = df[flag_cols].copy()
    if "CFS_OFS괴리" in tbl.columns:
        tbl["CFS_OFS괴리"] = tbl["CFS_OFS괴리"].apply(
            lambda x: "⚠️ 괴리 감지" if x is True else ("정상" if x is False else "미계산")
        )
    if "매핑신뢰도" in tbl.columns:
        tbl["매핑신뢰도"] = pd.to_numeric(tbl["매핑신뢰도"], errors="coerce").apply(
            lambda x: f"{x:.0%}" if pd.notna(x) else "-"
        )

    def _highlight(row):
        styles = [""] * len(row)
        cols = list(row.index)
        if "CFS_OFS괴리" in cols and "괴리 감지" in str(row.get("CFS_OFS괴리", "")):
            return [f"background-color:#FFF0F0; color:{RED}" for _ in styles]
        return styles

    st.dataframe(
        tbl.style.apply(_highlight, axis=1),
        use_container_width=True,
        hide_index=True,
        height=min(len(tbl) * 35 + 50, 350),
    )
else:
    st.info("재무 데이터가 없습니다.")

# ── 감사 품질 지표 ──────────────────────────────────────────────────────
st.markdown(section_title("감사 품질 지표"), unsafe_allow_html=True)

aud_df = get_auditors(company["corp_code"])
if aud_df.empty:
    st.markdown(no_data("감사인 이력이 없습니다."), unsafe_allow_html=True)
    from dashboard.collector import render_collect_button
    render_collect_button(company["corp_code"], stock, "감사인 데이터 수집")
else:
    col_a, col_b = st.columns(2)

    with col_a:
        # 감사인 연속 연수 (독립성 위험)
        cfs_aud = aud_df[aud_df["구분"] == "CFS"] if "구분" in aud_df.columns else aud_df
        if not cfs_aud.empty and "연속연수" in cfs_aud.columns:
            max_tenure = cfs_aud["연속연수"].max()
            color = RED if max_tenure >= 6 else (ORANGE if max_tenure >= 4 else GREEN)
            st.markdown(f"""
            <div class="kpi-card" style="border-top:3px solid {color}; text-align:left; padding:1rem;">
              <div class="kpi-label">최장 연속 감사 연수 (독립성 위험)</div>
              <div class="kpi-value" style="color:{color}">{max_tenure}년</div>
              <div class="kpi-sub">{'⚠️ 6년 이상 — 독립성 저하 위험' if max_tenure >= 6 else '✓ 정상 범위'}</div>
            </div>
            """, unsafe_allow_html=True)

    with col_b:
        # 비적정 의견 존재 여부
        non_clean = aud_df[~aud_df["감사의견"].isin(["-", "적정"])]
        if non_clean.empty:
            st.markdown(f"""
            <div class="kpi-card risk-ok" style="text-align:left; padding:1rem;">
              <div class="kpi-label">감사의견 이력</div>
              <div class="kpi-value" style="color:{GREEN}">전기간 적정</div>
              <div class="kpi-sub">수집된 기간 내 비적정 의견 없음</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown(f"""
            <div class="kpi-card risk-bad" style="text-align:left; padding:1rem;">
              <div class="kpi-label">감사의견 이력</div>
              <div class="kpi-value" style="color:{RED}">비적정 {len(non_clean)}건</div>
              <div class="kpi-sub">{', '.join(non_clean['감사의견'].unique().tolist())}</div>
            </div>
            """, unsafe_allow_html=True)

    # 감사인 이력 요약
    st.markdown("<br>", unsafe_allow_html=True)

    def _aud_style(row):
        if row.get("교체여부") == "교체":
            return [f"background-color:#FFF0F0; color:{RED}" for _ in row]
        if str(row.get("감사의견", "-")) not in ("-", "적정"):
            return [f"background-color:#FFF0F0; color:{RED}" for _ in row]
        return [""] * len(row)

    st.dataframe(
        aud_df.style.apply(_aud_style, axis=1),
        use_container_width=True,
        hide_index=True,
    )

# ── Beneish M-Score 추세 ───────────────────────────────────────────────────
if not df.empty and "Beneish_M" in df.columns:
    cfs_df2 = df[df["구분"] == "CFS"].copy() if "구분" in df.columns else df.copy()
    annual_beneish = cfs_df2[(cfs_df2["분기"] == 4) & cfs_df2["Beneish_M"].notna()].copy()

    if not annual_beneish.empty:
        st.markdown(section_title("Beneish M-Score — 이익 조작 탐지"), unsafe_allow_html=True)
        st.caption(
            "⚠ Beneish M-Score는 미국 상장사 데이터(1999)로 개발된 모델입니다. "
            "K-IFRS 환경 및 한국 시장 특성에 대한 별도 보정이 적용되지 않았으며, "
            "임계값(-1.78)은 참고 기준일 뿐 확정적 판단 근거로 사용해서는 안 됩니다. "
            "감사 절차 강화 여부는 다른 위험 신호와 종합적으로 판단하십시오."
        )

        fig_b = go.Figure()
        fig_b.add_scatter(
            x=annual_beneish["연도"],
            y=annual_beneish["Beneish_M"],
            mode="lines+markers+text",
            name="M-Score",
            line=dict(color=PRIMARY, width=2.5),
            marker_size=9,
            text=[f"{v:.2f}" for v in annual_beneish["Beneish_M"]],
            textposition="top center",
            textfont=dict(size=11, color="#333333"),
        )
        fig_b.add_hline(
            y=-1.78, line_color=RED, line_dash="dash", line_width=1.5,
            annotation_text="임계값 -1.78 (이익조작 가능성)", annotation_position="bottom right",
            annotation_font_color=RED,
        )
        fig_b.update_layout(
            title=dict(text="연간 Beneish M-Score 추세 (M > -1.78 → 이익 조작 가능성)", font_size=12, font_color="#333333"),
            height=260, margin=dict(t=35, b=10, l=0, r=0),
            plot_bgcolor="white", paper_bgcolor="white",
            font=dict(color="#333333"),
            xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
            yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
            showlegend=False,
        )
        st.plotly_chart(fig_b, use_container_width=True)

        with st.expander("M-Score 구성 지표 보기"):
            beneish_cols = ["연도", "Beneish_M"]
            for col in ["Beneish_DSRI", "Beneish_GMI", "Beneish_AQI", "Beneish_SGI",
                        "Beneish_DEPI", "Beneish_SGAI", "Beneish_LVGI", "Beneish_TATA"]:
                display_col = col.replace("Beneish_", "beneish_").lower()
                # 실제 컬럼명 매핑
                raw_map = {
                    "Beneish_DSRI": "beneish_dsri", "Beneish_GMI": "beneish_gmi",
                    "Beneish_AQI": "beneish_aqi", "Beneish_SGI": "beneish_sgi",
                    "Beneish_DEPI": "beneish_depi", "Beneish_SGAI": "beneish_sgai",
                    "Beneish_LVGI": "beneish_lvgi", "Beneish_TATA": "beneish_tata",
                }

            # 실제 DB에서 상세 인덱스 가져오기
            from dashboard.db import get_session
            from dart_platform.db.models import Financial, Company
            with get_session() as _sess:
                _corp_code = company["corp_code"]
                _fin_rows = _sess.query(Financial).filter_by(
                    corp_code=_corp_code, quarter=4, fs_div="CFS"
                ).order_by(Financial.year).all()
                _beneish_data = [
                    {
                        "연도": r.year,
                        "M-Score": f"{r.beneish_m_score:.3f}" if r.beneish_m_score is not None else "-",
                        "DSRI": f"{r.beneish_dsri:.3f}" if r.beneish_dsri is not None else "-",
                        "GMI": f"{r.beneish_gmi:.3f}" if r.beneish_gmi is not None else "-",
                        "AQI": f"{r.beneish_aqi:.3f}" if r.beneish_aqi is not None else "-",
                        "SGI": f"{r.beneish_sgi:.3f}" if r.beneish_sgi is not None else "-",
                        "DEPI": f"{r.beneish_depi:.3f}" if r.beneish_depi is not None else "-",
                        "SGAI": f"{r.beneish_sgai:.3f}" if r.beneish_sgai is not None else "-",
                        "LVGI": f"{r.beneish_lvgi:.3f}" if r.beneish_lvgi is not None else "-",
                        "TATA": f"{r.beneish_tata:.3f}" if r.beneish_tata is not None else "-",
                        "조작위험": "⚠️" if r.beneish_flag else "정상",
                    }
                    for r in _fin_rows
                ]
            if _beneish_data:
                import pandas as _pd
                st.dataframe(_pd.DataFrame(_beneish_data), use_container_width=True, hide_index=True)

# ── 감사보수 / NAS ratio ────────────────────────────────────────────────────
audit_fee_df = get_audit_fees(company["corp_code"])

if not audit_fee_df.empty:
    st.markdown(section_title("감사보수 및 독립성 지표 (DS002)"), unsafe_allow_html=True)
    st.caption(
        "비감사보수/감사보수 비율(NAS ratio)이 1.0을 초과하면 감사인 독립성 위협 요인으로 간주합니다. "
        "PCAOB 및 IAASB 가이드라인 참고 — 단독 판단 근거로 사용하지 마십시오."
    )

    # NAS ratio 추세 차트
    has_ratio = audit_fee_df["NAS_ratio"].notna().any()
    has_fees = audit_fee_df["감사보수(백만원)"].notna().any()

    if has_fees:
        col_fee1, col_fee2 = st.columns(2)

        with col_fee1:
            fig_fee = go.Figure()
            fig_fee.add_bar(
                x=audit_fee_df["회계연도"],
                y=audit_fee_df["감사보수(백만원)"],
                name="감사보수",
                marker_color=PRIMARY,
            )
            if audit_fee_df["비감사보수(백만원)"].notna().any():
                fig_fee.add_bar(
                    x=audit_fee_df["회계연도"],
                    y=audit_fee_df["비감사보수(백만원)"],
                    name="비감사보수",
                    marker_color=ORANGE,
                )
            fig_fee.update_layout(
                barmode="group",
                title=dict(text="감사보수 vs 비감사보수 (백만원)", font_size=12, font_color="#333333"),
                height=260, margin=dict(t=35, b=10, l=0, r=0),
                plot_bgcolor="white", paper_bgcolor="white",
                font=dict(color="#333333"),
                xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
                yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
                legend=dict(orientation="h", y=-0.2, font_size=11),
            )
            st.plotly_chart(fig_fee, use_container_width=True)

        with col_fee2:
            if has_ratio:
                fig_nas = go.Figure()
                fig_nas.add_scatter(
                    x=audit_fee_df["회계연도"],
                    y=audit_fee_df["NAS_ratio"],
                    mode="lines+markers+text",
                    name="NAS ratio",
                    line=dict(color=ORANGE, width=2.5),
                    marker_size=9,
                    text=[f"{v:.2f}" if pd.notna(v) else "" for v in audit_fee_df["NAS_ratio"]],
                    textposition="top center",
                    textfont=dict(size=11),
                )
                fig_nas.add_hline(
                    y=1.0, line_color=RED, line_dash="dash", line_width=1.5,
                    annotation_text="독립성 위험 임계 1.0", annotation_position="bottom right",
                    annotation_font_color=RED,
                )
                fig_nas.update_layout(
                    title=dict(text="NAS ratio 추세 (비감사보수/감사보수)", font_size=12, font_color="#333333"),
                    height=260, margin=dict(t=35, b=10, l=0, r=0),
                    plot_bgcolor="white", paper_bgcolor="white",
                    font=dict(color="#333333"),
                    xaxis=dict(tickmode="linear", dtick=1, tickfont=dict(color="#333333")),
                    yaxis=dict(gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
                    showlegend=False,
                )
                st.plotly_chart(fig_nas, use_container_width=True)
            else:
                st.info("비감사보수 데이터가 없어 NAS ratio를 계산할 수 없습니다.")

    # 감사보수 상세 테이블
    with st.expander("감사보수 상세 데이터"):
        tbl_fee = audit_fee_df.copy()
        for col in ["감사보수(백만원)", "감사시간", "비감사보수(백만원)", "비감사시간"]:
            if col in tbl_fee.columns:
                tbl_fee[col] = tbl_fee[col].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else "-")
        if "NAS_ratio" in tbl_fee.columns:
            tbl_fee["NAS_ratio"] = tbl_fee["NAS_ratio"].apply(
                lambda x: f"{x:.2f}" if pd.notna(x) else "-"
            )
        if "독립성위험" in tbl_fee.columns:
            tbl_fee["독립성위험"] = tbl_fee["독립성위험"].apply(
                lambda x: "위험" if x is True else ("정상" if x is False else "-")
            )

        def _fee_style(row):
            if row.get("독립성위험") == "위험":
                return [f"background-color:#FFF0F0; color:{RED}" for _ in row]
            return [""] * len(row)

        st.dataframe(
            tbl_fee.style.apply(_fee_style, axis=1),
            use_container_width=True,
            hide_index=True,
        )

# ── 소급 재작성 감지 ──────────────────────────────────────────────────────
st.markdown(section_title("소급 재작성 감지 (과거 연도 값 변경)"), unsafe_allow_html=True)
st.caption(
    "각 사업연도의 '전기값'(frmtrm_amount)과 직전 연도 보고서의 '당기값'(thstrm_amount)을 비교하여 "
    "차이가 큰 계정을 탐지합니다. "
    "⚠️ K-IFRS 기준 변경 · 사업결합 재측정 · 회계정책 변경은 오류가 아닌 정상적 재작성일 수 있습니다. "
    "자동 경보로 간주하지 말고 원인 확인이 필요한 항목으로 활용하세요."
)

rest_df = get_restatement_delta(company["corp_code"], threshold_pct=1.0, top_n=10)
if rest_df.empty:
    st.markdown(
        insight("탐지 임계(변동률 ≥1%)를 초과하는 소급 재작성 항목이 없습니다.", "info"),
        unsafe_allow_html=True,
    )
else:
    tbl_rest = rest_df.copy()
    for col in ["원본값", "재작성값", "차이"]:
        tbl_rest[col] = tbl_rest[col].apply(lambda x: f"{x:,.1f}" if pd.notna(x) else "-")
    tbl_rest["변동률"] = tbl_rest["변동률"].apply(lambda x: f"{x:+.1f}%" if pd.notna(x) else "-")

    def _rest_style(row):
        return [f"background-color:#FFF7F0; color:{ORANGE}" for _ in row]

    st.dataframe(
        tbl_rest.style.apply(_rest_style, axis=1),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "금액 단위: 억원 · '원본값'은 재작성되기 전 보고된 값, '재작성값'은 이후 보고서에서 갱신된 값입니다."
    )

# ── 종합 의견 ──────────────────────────────────────────────────────────────
st.markdown(section_title("종합 위험 평가"), unsafe_allow_html=True)

total_risk = (
    summary["cfs_ofs_gap_count"]
    + summary["auditor_change_count"] * 2
    + summary["non_clean_opinion_count"] * 3
    + summary["equity_negative_count"] * 4
    + summary["going_concern_count"] * 3
    + summary["beneish_alert_count"] * 2
    + summary.get("revenue_vol_count", 0) * 1
    + summary.get("op_cf_divergence_count", 0) * 2
    + summary.get("high_accrual_count", 0) * 2
    + summary.get("amendment_heavy_count", 0) * 1
    + summary.get("nas_risk_count", 0) * 2
)

if total_risk == 0:
    st.markdown(insight("수집된 데이터 기준으로 특이 위험 신호가 없습니다. 감사 절차 확대 근거 없음.", "info"), unsafe_allow_html=True)
elif total_risk <= 2:
    st.markdown(insight("일부 위험 신호가 감지됩니다. 해당 항목에 대한 추가 감사 절차를 검토하세요.", "warn"), unsafe_allow_html=True)
else:
    st.markdown(insight("복합 위험 신호가 감지됩니다. 감사 위험 평가를 상향하고 실질적인 감사 절차를 강화하세요.", "risk"), unsafe_allow_html=True)

with st.expander("판단 기준 보기"):
    st.markdown("""
| 지표 | 기준 | 점수 | 감사 함의 |
|------|------|------|----------|
| CFS/OFS 순이익 괴리 | &#124;연결-별도&#124; / &#124;별도&#124; > 20% | +1 | 자회사 간 이익 조정, 연결 범위 이슈 가능성 |
| 계정 매핑 불완전 | 핵심 6개 계정 매핑률 < 80% | +1 | 비표준 계정과목으로 수치 신뢰도 저하 |
| 감사인 교체 | 전년 대비 변경 | +2 | 회계처리 방식 변경, 의견 불일치 가능성 |
| 비적정 감사의견 | 한정·부적정·의견거절 | +3 | 계속기업 불확실성, 감사 범위 제한 |
| 자본잠식 | 자본총계 < 0 | +4 | 재무 구조 심각 악화, 계속기업 의심 |
| 계속기업 우려 | 영업이익·순이익 동시 적자 | +3 | 핵심 사업 수익성 부재 |
| 영업·순이익 괴리 | 영업이익>0 & 순이익<0 | +2 | 일회성 자산 손상, 숨겨진 비용 가능성 |
| Beneish M-Score | M > -1.78 | +2 | 재무제표 수치 조작 가능성 (Beneish 1999) |
| 매출 급변 | &#124;매출성장률&#124; ≥ 30% | +1 | 비경상 거래 또는 사업 구조 변동 가능성 |
| 영업CF 괴리 | 영업이익>0 & 영업CF<0 | +2 | 이익의 현금 전환 실패 — 발생주의 조작 또는 운전자본 악화 |
| 발생액 이상 | &#124;발생액비율&#124; > 1.0 | +2 | 현금 뒷받침 없는 장부 이익 비중 과다 (Sloan 1996) |
| 정정공시 빈발 | 연간 정정·수정 공시 ≥ 3건 | +1 | 내부통제 취약 또는 고의적 오기재 가능성 |
| 비감사보수 위험 | NAS ratio > 1.0 | +2 | 비감사용역 의존 — 감사인 독립성 저하 위험 |
| 연속 감사 연수 | 6년 이상 | - | 감사인 독립성 저하 위험 (주기적 지정 고려) |
    """)

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from dashboard.collector import render_collect_button
from dashboard.db import (
    get_audit_fees,
    get_audit_proposal_profile,
    get_company,
    get_external_audit_execution,
    get_governance_communications,
)
from dashboard.styles import (
    CSS,
    NAVY,
    PRIMARY,
    TEXT_DARK,
    insight,
    kpi_card,
    no_data,
    page_header,
    section_title,
)
from dashboard.sidebar import render_sidebar

st.set_page_config(page_title="감사제안", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
render_sidebar()

stock = st.session_state.get("selected_stock")
if not stock:
    st.warning("홈 페이지에서 종목을 먼저 선택하세요.")
    st.stop()

company = get_company(stock)
if not company:
    st.error(f"종목 {stock} 정보를 찾을 수 없습니다.")
    st.stop()

corp_code = company["corp_code"]
fs_div = st.session_state.get("fs_div", "CFS")

st.markdown(
    page_header(
        f"감사제안 프로필 · {company['corp_name']}",
        "감사제안 준비를 위한 회사 구분 · 핵심 재무 · 감사예산/실제투입 · 지배기구 커뮤니케이션",
    ),
    unsafe_allow_html=True,
)

with st.spinner("감사제안용 데이터를 불러오는 중..."):
    profile = get_audit_proposal_profile(corp_code, fs_div)
    execution_df = get_external_audit_execution(corp_code)
    governance_df = get_governance_communications(corp_code)
    fee_df = get_audit_fees(corp_code)

if not profile.get("has_data"):
    st.markdown(no_data("감사제안 프로필을 구성할 수 있는 데이터가 없습니다."), unsafe_allow_html=True)
    render_collect_button(corp_code, stock, label="감사제안 데이터 수집")
    st.stop()


def _fmt_eok(value) -> str:
    if value is None or pd.isna(value):
        return "-"
    value = float(value)
    if abs(value) >= 10000:
        return f"{value / 10000:,.1f}조"
    return f"{value:,.0f}억"


def _fmt_int(value) -> str:
    if value is None or pd.isna(value):
        return "-"
    return f"{int(value):,}"


latest_fin = profile.get("latest_financials", {})
latest_budget = profile.get("latest_budget", {})
internal_control = profile.get("internal_control", {})
classification_flags = pd.DataFrame(profile.get("classification_flags", []))
coverage = profile.get("data_coverage", {})

if not latest_fin.get("year") and execution_df.empty and governance_df.empty and fee_df.empty:
    st.markdown(
        no_data("아직 이 회사의 감사제안용 데이터가 충분히 수집되지 않았습니다."),
        unsafe_allow_html=True,
    )
    render_collect_button(corp_code, stock, label="감사제안 데이터 수집")
    st.stop()

top_cols = st.columns(5)
top_cols[0].markdown(
    kpi_card(
        "총자산",
        _fmt_eok(latest_fin.get("total_assets")),
        f"{latest_fin.get('year', '-')}" if latest_fin.get("year") else "최근 연간 기준",
    ),
    unsafe_allow_html=True,
)
top_cols[1].markdown(
    kpi_card(
        "총매출",
        _fmt_eok(latest_fin.get("revenue")),
        f"{latest_fin.get('year', '-')}" if latest_fin.get("year") else "최근 연간 기준",
    ),
    unsafe_allow_html=True,
)
top_cols[2].markdown(
    kpi_card(
        "당기순이익",
        _fmt_eok(latest_fin.get("net_income")),
        f"{latest_fin.get('year', '-')}" if latest_fin.get("year") else "최근 연간 기준",
    ),
    unsafe_allow_html=True,
)
top_cols[3].markdown(
    kpi_card(
        "실제 감사시간",
        _fmt_int(latest_budget.get("실제시간") or latest_budget.get("감사시간")),
        "최신 사업보고서 / DS002 기준",
    ),
    unsafe_allow_html=True,
)
top_cols[4].markdown(
    kpi_card(
        "커뮤니케이션",
        f"{profile.get('meeting_count', 0)}회",
        "최신 사업보고서 기준",
    ),
    unsafe_allow_html=True,
)

st.markdown(section_title("회사 구분 / 공시 체계"), unsafe_allow_html=True)
flag_cols = st.columns(4)
flag_cols[0].markdown(
    kpi_card(
        "회사 분류",
        profile["company"].get("primary_classification", "-"),
        profile["company"].get("market", "-"),
    ),
    unsafe_allow_html=True,
)
flag_cols[1].markdown(
    kpi_card(
        "금융회사 여부",
        next((r["value"] for _, r in classification_flags.iterrows() if r["label"] == "금융회사"), "미확인"),
        profile["company"].get("induty_code") or "업종코드 미확인",
    ),
    unsafe_allow_html=True,
)
flag_cols[2].markdown(
    kpi_card(
        "반기공시",
        "예" if profile["disclosure_flags"].get("has_semiannual_report") else "아니오",
        "공시 수집 기준",
    ),
    unsafe_allow_html=True,
)
flag_cols[3].markdown(
    kpi_card(
        "분기공시",
        "예" if profile["disclosure_flags"].get("has_quarterly_report") else "아니오",
        "공시 수집 기준",
    ),
    unsafe_allow_html=True,
)

st.markdown(
    insight(
        "채권상장회사 / 대형비상장사 / 중소형비상장사 구분은 현재 상장사 중심 마스터 기준이라 일부는 '판단 보류'로 표시됩니다. "
        "비상장 포함 분류 로직은 다음 단계에서 corp_code 중심 검색까지 확장하는 것이 맞습니다.",
        level="warn",
    ),
    unsafe_allow_html=True,
)

if not classification_flags.empty:
    st.dataframe(
        classification_flags.rename(columns={"label": "구분자", "value": "판정"}),
        use_container_width=True,
        hide_index=True,
    )

st.markdown(section_title("외부감사 실시내용"), unsafe_allow_html=True)
if not execution_df.empty:
    chart_df = execution_df.copy()
    chart_df["레이블"] = chart_df["회계연도"].fillna(chart_df["표시연도"]).astype(str)

    c1, c2 = st.columns(2)
    with c1:
        fig_hours = go.Figure()
        fig_hours.add_bar(
            x=chart_df["레이블"],
            y=chart_df["계약시간"],
            name="계약시간",
            marker_color="#B8C9F5",
        )
        fig_hours.add_bar(
            x=chart_df["레이블"],
            y=chart_df["실제시간"],
            name="실제시간",
            marker_color=PRIMARY,
        )
        fig_hours.update_layout(
            barmode="group",
            height=300,
            margin=dict(t=10, b=10, l=10, r=10),
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(color=TEXT_DARK),
            legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig_hours, use_container_width=True, key="audit_execution_hours")

    with c2:
        fig_fee = go.Figure()
        fig_fee.add_bar(
            x=chart_df["레이블"],
            y=chart_df["계약보수(백만원)"],
            name="계약보수",
            marker_color="#D6DFF9",
        )
        fig_fee.add_bar(
            x=chart_df["레이블"],
            y=chart_df["실제보수(백만원)"],
            name="실제보수",
            marker_color=NAVY,
        )
        fig_fee.update_layout(
            barmode="group",
            height=300,
            margin=dict(t=10, b=10, l=10, r=10),
            plot_bgcolor="white",
            paper_bgcolor="white",
            font=dict(color=TEXT_DARK),
            legend=dict(orientation="h", y=-0.2),
        )
        st.plotly_chart(fig_fee, use_container_width=True, key="audit_execution_fees")

    exec_view = execution_df[
        [
            "표시연도",
            "감사인",
            "내용",
            "계약보수(백만원)",
            "계약시간",
            "실제보수(백만원)",
            "실제시간",
            "시간차이",
        ]
    ].copy()
    st.dataframe(exec_view, use_container_width=True, hide_index=True)

    st.download_button(
        "CSV 다운로드 (외부감사 실시내용)",
        data=execution_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{company['corp_name']}_외부감사실시내용.csv",
        mime="text/csv",
    )
elif not fee_df.empty:
    st.info("사업보고서에서 실제수행내역(AUD_SIGK)은 잡히지 않았지만, DS002 감사보수/감사시간 데이터는 확인됩니다.")
    st.dataframe(fee_df, use_container_width=True, hide_index=True)
else:
    st.info("외부감사 실시내용이 아직 수집되지 않았습니다. 사업보고서와 감사보수 데이터를 다시 수집해보세요.")

st.markdown(section_title("지배기구와 커뮤니케이션"), unsafe_allow_html=True)
if not governance_df.empty:
    latest_note = governance_df.iloc[0]["주요 논의 내용"]
    st.markdown(
        insight(f"최신 커뮤니케이션 핵심: {latest_note}", level="info"),
        unsafe_allow_html=True,
    )
    st.dataframe(
        governance_df[["일자", "참석자", "방식", "주요 논의 내용"]],
        use_container_width=True,
        hide_index=True,
    )
    st.download_button(
        "CSV 다운로드 (커뮤니케이션)",
        data=governance_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"{company['corp_name']}_지배기구커뮤니케이션.csv",
        mime="text/csv",
    )
else:
    st.info("최신 사업보고서에서 내부감사기구와 회계감사인 논의 내역(DIS_CUS)을 찾지 못했습니다.")

st.markdown(section_title("내부회계관리제도"), unsafe_allow_html=True)
ic_cols = st.columns(3)
ic_cols[0].markdown(
    kpi_card(
        "대상 구분",
        internal_control.get("scope") or "판단 보류",
        internal_control.get("scope_detail") or "감사보고서 / 사업보고서 기준",
    ),
    unsafe_allow_html=True,
)
ic_cols[1].markdown(
    kpi_card(
        "경영진 평가",
        internal_control.get("management_evaluation") or "-",
        "운영실태 보고서 기준",
    ),
    unsafe_allow_html=True,
)
ic_cols[2].markdown(
    kpi_card(
        "중요한 취약점",
        internal_control.get("significant_weakness") or "-",
        "최신 사업보고서 기준",
    ),
    unsafe_allow_html=True,
)

detail_rows = []
for label, value in [
    ("감사보고서 값", internal_control.get("audit_report_value")),
    ("연결 내부회계 경영진 평가", internal_control.get("consolidated_management_evaluation")),
    ("시정조치계획", internal_control.get("corrective_plan")),
]:
    if value:
        detail_rows.append({"항목": label, "내용": value})

if detail_rows:
    st.dataframe(pd.DataFrame(detail_rows), use_container_width=True, hide_index=True)
else:
    st.caption("내부회계관리제도 세부 값은 현재 문서 파싱 범위 내에서 확인된 내용만 표시합니다.")

with st.expander("데이터 커버리지 / 재수집", expanded=False):
    coverage_rows = [
        {"항목": "재무 수집 연도", "내용": ", ".join(map(str, coverage.get("financial_years", []))) or "-"},
        {"항목": "최신 사업보고서 연도", "내용": coverage.get("business_report_year") or "-"},
        {"항목": "외부감사 실시내용", "내용": "예" if coverage.get("has_execution_data") else "아니오"},
        {
            "항목": "지배기구 커뮤니케이션",
            "내용": "예" if coverage.get("has_governance_communications") else "아니오",
        },
    ]
    st.dataframe(pd.DataFrame(coverage_rows), use_container_width=True, hide_index=True)
    render_collect_button(corp_code, stock, label="감사제안 데이터 재수집")

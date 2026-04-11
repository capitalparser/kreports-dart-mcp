import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
import plotly.graph_objects as go
import pandas as pd
from dashboard.db import get_auditors, get_company, get_audit_fees, get_audit_fee_history, get_auditors_for_corp_codes, get_companies_by_corp_codes, get_subsidiaries_with_auditors
from dashboard.styles import CSS, page_header, kpi_card, section_title, insight, no_data, PRIMARY, NAVY, RED, GREEN, ORANGE, WHITE, BORDER, TEXT_DARK, TEXT_MID, LIGHT_BG

st.set_page_config(page_title="감사인 이력", layout="wide")
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
    f"감사인 이력 — {company['corp_name']}",
    "감사인 교체·연속 연수·감사의견 타임라인"
), unsafe_allow_html=True)

with st.spinner("감사인 이력 로딩 중..."):
    df = get_auditors(company["corp_code"])
if df.empty:
    st.markdown(no_data("감사인 이력이 없습니다."), unsafe_allow_html=True)
    if st.button("감사인 이력 수집", type="primary", use_container_width=True):
        with st.spinner("감사인 이력 수집 중..."):
            from dart_platform.collector.audit_collector import collect_auditors
            result = collect_auditors(company["corp_code"])
        st.success(f"완료: 저장 {result['saved']}건, 스킵 {result['skipped']}건")
        st.rerun()
    st.stop()

# CFS 우선, 없으면 OFS — 감사인은 연결/별도 구분 없이 동일하게 선임됨
if "구분" in df.columns:
    cfs = df[df["구분"] == "CFS"]
    sub = cfs if not cfs.empty else df[df["구분"] == "OFS"]
    # 그래도 비어있으면 전체 사용
    if sub.empty:
        sub = df
else:
    sub = df

sub = sub.sort_values("회계연도").copy()

# KPI
col1, col2, col3 = st.columns(3)
max_tenure = sub["연속연수"].max() if "연속연수" in sub.columns else 0
auditor_changes = (sub["교체여부"] == "교체").sum()
current_auditor = sub.iloc[-1]["감사인"] if not sub.empty else "-"
current_tenure = sub.iloc[-1]["연속연수"] if not sub.empty else 0

tenure_risk = "bad" if max_tenure >= 6 else ("warn" if max_tenure >= 4 else "ok")
change_risk = "bad" if auditor_changes > 1 else ("warn" if auditor_changes == 1 else "ok")

col1.markdown(kpi_card("현재 감사인", current_auditor, f"연속 {current_tenure}년", tenure_risk), unsafe_allow_html=True)
col2.markdown(kpi_card("감사인 교체", f"{auditor_changes}회", "수집 기간 내", change_risk), unsafe_allow_html=True)
col3.markdown(kpi_card("최장 연속 연수", f"{max_tenure}년", "⚠️ 독립성 위험" if max_tenure >= 6 else "정상 범위", tenure_risk), unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# 타임라인 차트 — Gantt 스타일
st.markdown(section_title("감사인 교체 타임라인"), unsafe_allow_html=True)

unique_auditors = sub["감사인"].unique().tolist()
palette = [PRIMARY, NAVY, "#7B9FE8", "#4A6DB5", "#B8C9F5", "#6B7280"]
color_map = {a: palette[i % len(palette)] for i, a in enumerate(unique_auditors)}

fig = go.Figure()
for _, row in sub.iterrows():
    color = RED if row["교체여부"] == "교체" else color_map.get(row["감사인"], PRIMARY)
    marker_symbol = "star" if row["교체여부"] == "교체" else "circle"
    fig.add_scatter(
        x=[row["회계연도"]],
        y=[row["감사인"]],
        mode="markers+text",
        marker=dict(size=18, color=color, symbol=marker_symbol,
                    line=dict(width=2, color="white")),
        text=[str(int(row["연속연수"])) + "년"],
        textposition="middle center",
        textfont=dict(size=9, color="white"),
        name=row["감사인"],
        hovertemplate=(
            f"<b>{row['회계연도']}년</b><br>"
            f"감사인: {row['감사인']}<br>"
            f"교체여부: {row['교체여부']}<br>"
            f"연속연수: {row['연속연수']}년<br>"
            + (f"감사의견: {row['감사의견']}" if row["감사의견"] != "-" else "")
            + "<extra></extra>"
        ),
        showlegend=False,
    )
    if row["교체여부"] == "교체":
        fig.add_annotation(
            x=row["회계연도"], y=row["감사인"],
            text="교체", showarrow=True, arrowhead=2,
            arrowcolor=RED, font=dict(color=RED, size=10),
            yshift=25, ax=0, ay=-20,
        )

for auditor in unique_auditors:
    aud_rows = sub[sub["감사인"] == auditor].sort_values("회계연도")
    if len(aud_rows) > 1:
        fig.add_scatter(
            x=aud_rows["회계연도"], y=[auditor] * len(aud_rows),
            mode="lines",
            line=dict(color=color_map.get(auditor, PRIMARY), width=3, dash="solid"),
            showlegend=False, hoverinfo="skip",
        )

fig.update_layout(
    height=max(220, len(unique_auditors) * 80 + 80),
    margin=dict(t=20, b=20, l=10, r=10),
    plot_bgcolor="white", paper_bgcolor="white",
    font=dict(color="#333333"),
    xaxis=dict(
        tickmode="linear", dtick=1,
        gridcolor="#F0F0F0", title="회계연도",
        tickfont=dict(color="#333333"),
    ),
    yaxis=dict(title="", gridcolor="#F0F0F0", tickfont=dict(color="#333333")),
)
st.plotly_chart(fig, use_container_width=True, key="auditor_timeline")

# 상세 테이블 — 구분 컬럼 제외
st.markdown(section_title("연도별 상세"), unsafe_allow_html=True)

def _style_aud(row):
    if row.get("교체여부") == "교체":
        return [f"background:#FFF0F0; color:{RED}; font-weight:600"] * len(row)
    if str(row.get("감사의견", "-")) not in ("-", "적정"):
        return [f"background:#FFF0F0; color:{RED}"] * len(row)
    if row.get("연속연수", 0) >= 6:
        return [f"background:#FFF8E8; color:{ORANGE}"] * len(row)
    return [""] * len(row)

disp_cols = ["회계연도", "감사인", "감사의견", "교체여부", "연속연수"]
disp = sub[disp_cols].copy()
st.dataframe(disp.style.apply(_style_aud, axis=1), use_container_width=True, hide_index=True)

# 인사이트
st.markdown(section_title("감사인 관점 인사이트"), unsafe_allow_html=True)
if auditor_changes >= 2:
    st.markdown(insight(f"감사인이 {auditor_changes}회 교체됐습니다. 잦은 교체는 회계처리 방식 변경 또는 감사인-피감사인 간 의견 불일치를 시사할 수 있습니다.", "risk"), unsafe_allow_html=True)
elif auditor_changes == 1:
    st.markdown(insight("감사인이 1회 교체됐습니다. 교체 전후 회계정책 변경 여부를 확인하세요.", "warn"), unsafe_allow_html=True)
if max_tenure >= 6:
    st.markdown(insight(f"동일 감사인이 {max_tenure}년 연속 감사를 수행했습니다. 감사인 독립성 저하 위험 — 주기적 지정제 적용 대상 여부를 확인하세요.", "warn"), unsafe_allow_html=True)
non_clean_rows = sub[~sub["감사의견"].isin(["-", "적정"])]
if not non_clean_rows.empty:
    for _, nr in non_clean_rows.iterrows():
        st.markdown(insight(f"{int(nr['회계연도'])}년 감사의견: {nr['감사의견']} — 해당 연도 재무제표 주석 및 계속기업 불확실성 공시를 검토하세요.", "risk"), unsafe_allow_html=True)
if auditor_changes == 0 and max_tenure < 6:
    st.markdown(insight("수집된 데이터 기준으로 특이 위험 신호가 없습니다. 감사 절차 확대 근거 없음."), unsafe_allow_html=True)

# 감사용역 체결현황
st.markdown(section_title("감사용역 체결현황"), unsafe_allow_html=True)
fee_df = get_audit_fees(company["corp_code"])
if fee_df.empty:
    with st.spinner("감사용역 현황 조회 중 (API 호출)..."):
        fee_df = get_audit_fee_history(company["corp_code"])
if not fee_df.empty:
    st.dataframe(fee_df[['사업연도', '감사인', '보수(백만원)']], use_container_width=True, hide_index=True)
else:
    st.info("감사용역 데이터가 없습니다.")

# ── 종속회사·지분법 회사 감사인 현황 ─────────────────────────────────────
st.markdown("---")
st.markdown(section_title("종속회사·지분법 회사 감사인 현황"), unsafe_allow_html=True)
st.caption("최신 사업보고서 기준 — 종속회사·지분법 회사의 감사인을 DB에서 조회합니다.")

if st.button("종속·지분법 회사 감사인 조회", use_container_width=True):
    st.session_state["show_affiliates"] = True

if st.session_state.get("show_affiliates"):
    with st.spinner("사업보고서에서 종속/지분법 회사 파싱 중..."):
        sub_data = get_subsidiaries_with_auditors(company["corp_code"])

    items = sub_data.get("items", [])
    parse_errors = sub_data.get("parse_errors", [])
    rcept_no = sub_data.get("rcept_no", "")
    bsns_year = sub_data.get("bsns_year")

    # 파싱 오류 표시
    if parse_errors:
        with st.expander("파싱 진단 정보", expanded=not items):
            for err in parse_errors:
                st.warning(err)

    if not items:
        st.info("사업보고서에서 종속회사/타법인출자 정보를 찾지 못했습니다.")
    else:
        # 메타 정보
        meta_txt = f"{bsns_year}년 사업보고서 기준" if bsns_year else "사업보고서 기준"
        if rcept_no:
            meta_txt += f" (접수번호: {rcept_no})"
        st.caption(meta_txt)

        # KPI
        sub_items = [it for it in items if it["relation"] == "종속"]
        eq_items = [it for it in items if it["relation"] == "지분법"]
        matched_items = [it for it in items if it["auditor"] is not None]

        kc1, kc2, kc3 = st.columns(3)
        kc1.markdown(kpi_card("종속회사", f"{len(sub_items)}개사", "연결대상", "ok"), unsafe_allow_html=True)
        kc2.markdown(kpi_card("지분법 회사", f"{len(eq_items)}개사", "20~50% 지분", "ok"), unsafe_allow_html=True)
        kc3.markdown(
            kpi_card(
                "감사인 확인",
                f"{len(matched_items)} / {len(items)}개사",
                "DB 수집 기준",
                "ok" if len(matched_items) == len(items) else "warn",
            ),
            unsafe_allow_html=True,
        )

        st.markdown("<div style='margin:0.5rem 0'></div>", unsafe_allow_html=True)

        def _render_group(group_items: list, group_label: str) -> None:
            if not group_items:
                return
            st.markdown(
                f'<div style="font-size:0.85rem;font-weight:700;color:{NAVY};'
                f'margin:0.8rem 0 0.3rem;">▸ {group_label} ({len(group_items)}개사)</div>',
                unsafe_allow_html=True,
            )
            rows_html = ""
            for it in group_items:
                name = it["name"]
                pct = f"{it['ownership_pct']:.1f}%" if it.get("ownership_pct") is not None else "-"
                mkt_map = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}
                # '상장' 판정은 오직 stock_code(국내 KOSPI/KOSDAQ/KONEX)로만.
                # listed_yn=Y는 DART 사업보고서 XML 상 '상장' 표기지만 해외 상장일 수도 있으므로,
                # 국내 미매칭 + listed_yn=Y 인 경우 "해외상장(추정)"으로 구분한다.
                if it.get("stock_code"):
                    market_label = mkt_map.get(it.get("market") or "", "상장")
                elif it.get("listed_yn") == "Y":
                    market_label = "해외상장(추정)"
                elif it.get("corp_code"):
                    market_label = "비상장(DART)"
                else:
                    market_label = "비상장"
                aud = it.get("auditor")
                if aud:
                    aud_nm = aud["name"]
                    aud_yr = aud["year"]
                    aud_op = aud["opinion"] or "-"
                    is_same = aud_nm == current_auditor
                    badge_bg = "#E5F5EE" if is_same else "#EEF2FF"
                    badge_color = GREEN if is_same else PRIMARY
                    badge_text = "동일" if is_same else "상이"
                    op_color = RED if aud_op not in ("-", "적정") else TEXT_DARK
                    aud_cell = (
                        f'<td style="padding:0.4rem 0.7rem;border-bottom:1px solid #E5E7EB;color:{TEXT_DARK};">{aud_nm}</td>'
                        f'<td style="padding:0.4rem 0.7rem;border-bottom:1px solid #E5E7EB;color:{op_color};">{aud_op} ({aud_yr})</td>'
                        f'<td style="padding:0.4rem 0.7rem;border-bottom:1px solid #E5E7EB;">'
                        f'<span style="background:{badge_bg};color:{badge_color};font-size:0.7rem;font-weight:700;'
                        f'padding:0.15rem 0.5rem;border-radius:4px;">{badge_text}</span></td>'
                    )
                else:
                    if it.get("corp_code"):
                        no_data_msg = "미수집 (감사인 조회 가능)"
                    else:
                        no_data_msg = "DART 미등록"
                    aud_cell = (
                        f'<td colspan="3" style="padding:0.4rem 0.7rem;border-bottom:1px solid #E5E7EB;'
                        f'color:{TEXT_MID};font-size:0.78rem;">{no_data_msg}</td>'
                    )
                rows_html += (
                    f'<tr>'
                    f'<td style="padding:0.4rem 0.7rem;border-bottom:1px solid #E5E7EB;color:{TEXT_DARK};font-weight:600;">{name}</td>'
                    f'<td style="padding:0.4rem 0.7rem;border-bottom:1px solid #E5E7EB;color:{TEXT_MID};font-size:0.78rem;">{pct}</td>'
                    f'<td style="padding:0.4rem 0.7rem;border-bottom:1px solid #E5E7EB;color:{TEXT_MID};font-size:0.78rem;">{market_label}</td>'
                    + aud_cell +
                    f'</tr>'
                )
            st.markdown(f"""
<table style="width:100%;border-collapse:collapse;font-size:0.82rem;">
  <thead>
    <tr>
      <th style="background:{NAVY};color:white;padding:0.5rem 0.7rem;text-align:left;font-weight:600;">회사명</th>
      <th style="background:{NAVY};color:white;padding:0.5rem 0.7rem;text-align:left;font-weight:600;">지분율</th>
      <th style="background:{NAVY};color:white;padding:0.5rem 0.7rem;text-align:left;font-weight:600;">시장구분</th>
      <th style="background:{NAVY};color:white;padding:0.5rem 0.7rem;text-align:left;font-weight:600;">감사인</th>
      <th style="background:{NAVY};color:white;padding:0.5rem 0.7rem;text-align:left;font-weight:600;">감사의견 (연도)</th>
      <th style="background:{NAVY};color:white;padding:0.5rem 0.7rem;text-align:left;font-weight:600;">모회사 대비</th>
    </tr>
  </thead>
  <tbody>{rows_html}</tbody>
</table>
""", unsafe_allow_html=True)

        _render_group(sub_items, "종속회사")
        _render_group(eq_items, "지분법 회사")
        other_items = [it for it in items if it["relation"] not in ("종속", "지분법")]
        if other_items:
            _render_group(other_items, "기타 투자")

        # CSV 다운로드
        def _build_csv(rows: list[dict]) -> bytes:
            import csv, io
            cols = ["관계", "회사명", "지분율", "시장구분", "감사인", "감사의견", "감사연도", "모회사대비"]
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            mkt_map = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}
            for it in rows:
                aud = it.get("auditor")
                if it.get("stock_code"):
                    mkt = mkt_map.get(it.get("market") or "", "상장")
                elif it.get("listed_yn") == "Y":
                    mkt = "해외상장(추정)"
                elif it.get("corp_code"):
                    mkt = "비상장(DART)"
                else:
                    mkt = "DART미등록"
                w.writerow({
                    "관계": it["relation"],
                    "회사명": it["name"],
                    "지분율": f"{it['ownership_pct']:.1f}" if it.get("ownership_pct") is not None else "",
                    "시장구분": mkt,
                    "감사인": aud["name"] if aud else "",
                    "감사의견": aud["opinion"] if aud else "",
                    "감사연도": aud["year"] if aud else "",
                    "모회사대비": ("동일" if aud and aud["name"] == current_auditor else "상이") if aud else "",
                })
            return buf.getvalue().encode("utf-8-sig")

        fname = f"{company['corp_name']}_종속지분법_{bsns_year or 'NA'}.csv"
        st.download_button(
            label=f"CSV 다운로드 ({len(items)}개사)",
            data=_build_csv(items),
            file_name=fname,
            mime="text/csv",
            use_container_width=True,
        )

        # 미수집 기업 일괄 수집 버튼 (상장사 + DART 등록 비상장사)
        uncollected = [it for it in items if it["auditor"] is None and it.get("corp_code")]
        if uncollected:
            st.markdown("<br>", unsafe_allow_html=True)
            st.markdown(
                insight(
                    f"감사인 미수집 {len(uncollected)}개사 (상장사·비상장 DART 등록사 포함) — 아래 버튼으로 일괄 수집합니다.",
                    level="warn",
                ),
                unsafe_allow_html=True,
            )
            if st.button(f"미수집 감사인 일괄 수집 ({len(uncollected)}개)", use_container_width=True):
                from dart_platform.collector.audit_collector import collect_auditors
                results = []
                prog = st.progress(0)
                for i, it in enumerate(uncollected):
                    with st.spinner(f"수집 중: {it['name']} ..."):
                        r = collect_auditors(it["corp_code"])
                        results.append(f"{it['name']}: 저장 {r['saved']}건")
                    prog.progress((i + 1) / len(uncollected))
                st.success("\n".join(results))
                get_subsidiaries_with_auditors.clear()
                st.rerun()

        # 감사인 분포 요약
        audited = [it for it in items if it.get("auditor")]
        if audited:
            same_count = sum(1 for it in audited if it["auditor"]["name"] == current_auditor)
            diff_count = len(audited) - same_count
            no_data_count = len(items) - len(audited)
            col_s, col_d, col_n = st.columns(3)
            col_s.markdown(kpi_card("모회사와 동일 감사인", f"{same_count}개사", "네트워크 일관성", "ok"), unsafe_allow_html=True)
            col_d.markdown(kpi_card("상이 감사인", f"{diff_count}개사", "컴포넌트 감사인 존재", "warn" if diff_count > 0 else "ok"), unsafe_allow_html=True)
            col_n.markdown(kpi_card("미수집", f"{no_data_count}개사", "데이터 없음", "warn" if no_data_count > 0 else "ok"), unsafe_allow_html=True)


# 감사인 이력 재수집
with st.expander("감사인 이력 재수집"):
    if st.button("감사인 이력 재수집 (덮어쓰기)", use_container_width=True):
        with st.spinner("수집 중..."):
            from dart_platform.collector.audit_collector import collect_auditors
            result = collect_auditors(company["corp_code"])
        st.success(f"완료: 저장 {result['saved']}건, 스킵 {result['skipped']}건")
        st.rerun()

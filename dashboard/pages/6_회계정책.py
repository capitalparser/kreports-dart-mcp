import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import streamlit as st
from dashboard.db import get_company, get_financials, get_accounting_policy, get_financial_facts_years, get_years_with_business_report
from dashboard.styles import (
    CSS, page_header, section_title, insight, no_data, kpi_card,
    RED, ORANGE, GREEN, NAVY, PRIMARY, WHITE, BORDER, TEXT_DARK, TEXT_MID, LIGHT_BG,
)

st.set_page_config(page_title="회계정책", layout="wide")
st.markdown(CSS, unsafe_allow_html=True)
from dashboard.sidebar import render_sidebar
render_sidebar()

# ---------------------------------------------------------------------------
# 업종 매핑 임포트
# ---------------------------------------------------------------------------
from dart_platform.processor.sector_policy_map import (
    get_sector_for_induty_code,
    get_induty_name,
    get_critical_items,
    SECTOR_POLICY_MAP,
    DEFAULT_POLICY_ITEMS,
)


def _critical_items_for_sector(sector_key: str | None) -> list[tuple]:
    """sector 알파벳으로 중요 회계처리 항목 반환 (DEFAULT 포함)."""
    items = []
    if sector_key and sector_key in SECTOR_POLICY_MAP:
        items = list(SECTOR_POLICY_MAP[sector_key]["critical_items"])
    existing_keys = {it[0] for it in items}
    for default_item in DEFAULT_POLICY_ITEMS:
        if default_item[0] not in existing_keys:
            items.append(default_item)
    return items

# ---------------------------------------------------------------------------
# 상태 확인
# ---------------------------------------------------------------------------
stock_code = st.session_state.get("selected_stock")
if not stock_code:
    st.warning("홈 페이지에서 종목을 먼저 선택하세요.")
    st.stop()

company = get_company(stock_code)
if not company:
    st.markdown(no_data(f"'{stock_code}' 기업 정보를 찾을 수 없습니다."), unsafe_allow_html=True)
    st.stop()

corp_code = company["corp_code"]
corp_name = company["corp_name"]
induty_code = company.get("induty_code")  # KSIC 업종코드 5자리

# ---------------------------------------------------------------------------
# 헤더
# ---------------------------------------------------------------------------
st.markdown(
    page_header(
        f"{corp_name} — 회계정책 현황",
        f"업종별 중요 회계처리 대비 회사 회계정책 확인 | 종목코드 {stock_code}",
    ),
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# 연도 / 구분 선택
# ---------------------------------------------------------------------------
# 사업보고서가 실제 수집된 연도만 노출 (FinancialFact 연도는 분기 포함이므로 부적합)
years = get_years_with_business_report(corp_code)
if not years:
    st.markdown(
        insight(
            "수집된 사업보고서가 없습니다. CLI에서 'dart collect-disclosures' 실행 후 다시 확인하세요. "
            "재무 정보는 분기보고서까지 있지만, 회계정책 분석은 연간 사업보고서가 필요합니다.",
            level="warn",
        ),
        unsafe_allow_html=True,
    )
    st.stop()

col_y, col_div, col_space = st.columns([2, 2, 6])
with col_y:
    sel_year = st.selectbox("사업연도", years, index=0)
with col_div:
    sel_div = st.selectbox("재무제표 구분", ["CFS (연결)", "OFS (별도)"], index=0)
fs_div = "CFS" if sel_div.startswith("CFS") else "OFS"

st.markdown("<hr style='border:none;border-top:1px solid #D1D8F0;margin:0.8rem 0;'>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 업종 결정: induty_code → sector 알파벳 / 없으면 수동 선택
# ---------------------------------------------------------------------------
sector_key: str | None = None
induty_name: str = "-"
manual_mode = False

if induty_code:
    sector_key = get_sector_for_induty_code(induty_code)
    induty_name = get_induty_name(induty_code) or induty_code

if not sector_key:
    manual_mode = True
    st.markdown(
        insight(
            "업종 정보(induty_code)가 없습니다. "
            "'dart enrich-market' 명령으로 업종코드를 수집하거나 아래에서 직접 선택하세요.",
            level="warn",
        ),
        unsafe_allow_html=True,
    )
    sector_options = {v["label"]: k for k, v in SECTOR_POLICY_MAP.items()}
    sector_label_sel = st.selectbox(
        "업종 직접 선택",
        list(sector_options.keys()),
        index=list(sector_options.keys()).index("제조업") if "제조업" in sector_options else 0,
    )
    sector_key = sector_options[sector_label_sel]
    induty_name = sector_label_sel

sector_info = SECTOR_POLICY_MAP.get(sector_key, {})
sector_label = sector_info.get("label", sector_key)
critical_items = _critical_items_for_sector(sector_key)  # includes DEFAULT_POLICY_ITEMS

# ---------------------------------------------------------------------------
# 회계정책 데이터 로딩
# ---------------------------------------------------------------------------
with st.spinner("사업보고서에서 회계정책 주석을 추출 중..."):
    policy_data = get_accounting_policy(corp_code, sel_year, fs_div)

items_found: dict[str, str] = policy_data["items"] if policy_data else {}
matched_count = sum(1 for key, *_ in critical_items if key in items_found)
total_count = len(critical_items)

# ---------------------------------------------------------------------------
# KPI 카드 (3개)
# ---------------------------------------------------------------------------
k1, k2, k3 = st.columns(3)
with k1:
    sector_display = f"{sector_label}({sector_key})"
    sub1 = f"KSIC: {induty_code}" if induty_code else ("직접 선택" if manual_mode else "-")
    st.markdown(
        kpi_card("업종 대분류", sector_display, sub=sub1, risk_level="ok"),
        unsafe_allow_html=True,
    )
with k2:
    st.markdown(
        kpi_card(
            "핵심 감사 항목",
            f"{total_count}건",
            sub=f"HIGH {sum(1 for _, _, p, _ in critical_items if p == 'high')}건 포함",
            risk_level="ok",
        ),
        unsafe_allow_html=True,
    )
with k3:
    if not policy_data:
        match_label = "원문 미수집"
        risk = "warn"
        match_sub = "사업보고서 수집 필요"
    else:
        match_label = f"{matched_count} / {total_count} 확인"
        unmatched = total_count - matched_count
        risk = "ok" if unmatched == 0 else ("warn" if unmatched <= 2 else "bad")
        match_sub = "모든 항목 확인됨" if unmatched == 0 else f"{unmatched}건 원문 직접 확인 필요"
    st.markdown(
        kpi_card("매칭 현황", match_label, sub=match_sub, risk_level=risk),
        unsafe_allow_html=True,
    )

st.markdown("<div style='margin:0.8rem 0'></div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 업종별 핵심 회계처리 체크리스트
# ---------------------------------------------------------------------------
st.markdown(section_title(f"업종별 핵심 회계처리 체크리스트 — {sector_label}"), unsafe_allow_html=True)

if not policy_data:
    st.markdown(
        insight(
            f"{sel_year}년 사업보고서 회계정책을 수집할 수 없습니다. "
            "공시 수집('dart collect-disclosures')과 사업보고서 수집 여부를 확인하세요.",
            level="warn",
        ),
        unsafe_allow_html=True,
    )

for item_key, item_label, priority, item_desc in critical_items:
    excerpt = items_found.get(item_key)
    matched = excerpt is not None

    # 우선순위 배지
    if priority == "high":
        badge_bg = "#FFE5E5"
        badge_color = RED
        badge_text = "HIGH"
    else:
        badge_bg = "#FFF0E0"
        badge_color = ORANGE
        badge_text = "MED"

    badge_html = (
        f'<span style="background:{badge_bg};color:{badge_color};font-size:0.68rem;'
        f'font-weight:700;padding:0.15rem 0.4rem;border-radius:3px;'
        f'margin-right:0.5rem;">{badge_text}</span>'
    )

    # 매칭 상태 아이콘
    status_icon = (
        f'<span style="color:{GREEN};font-weight:700;margin-right:0.4rem;">✓</span>'
        if matched else
        f'<span style="color:{RED};font-weight:700;margin-right:0.4rem;">!</span>'
    )

    # 카드 렌더링
    card_border = f"1px solid {BORDER}"
    card_top = f"3px solid {GREEN}" if matched else f"3px solid {ORANGE if priority == 'medium' else RED}"

    st.markdown(
        f'<div style="background:{WHITE};border:{card_border};border-top:{card_top};'
        f'border-radius:8px;padding:0.9rem 1.1rem;margin-bottom:0.7rem;">'
        f'<div style="display:flex;align-items:center;margin-bottom:0.4rem;">'
        f'{status_icon}{badge_html}'
        f'<span style="color:{TEXT_DARK};font-weight:700;font-size:0.9rem;">{item_label}</span>'
        f'</div>',
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns(2)
    with col_left:
        st.markdown(
            f'<div style="font-size:0.78rem;color:{TEXT_MID};font-weight:600;'
            f'margin-bottom:0.3rem;">업종 일반 관행</div>'
            f'<div style="font-size:0.82rem;color:{TEXT_DARK};'
            f'background:{LIGHT_BG};border-radius:5px;padding:0.6rem 0.8rem;">'
            f'{item_desc}</div>',
            unsafe_allow_html=True,
        )
    with col_right:
        if matched:
            # excerpt는 dict({heading, body}) 또는 str(legacy) — 둘 다 지원
            if isinstance(excerpt, dict):
                heading_html = excerpt.get("heading", "")
                body_html = excerpt.get("body", "")
            else:
                heading_html = ""
                body_html = excerpt
            st.markdown(
                f'<div style="font-size:0.78rem;color:{TEXT_MID};font-weight:600;'
                f'margin-bottom:0.3rem;">회사 정책 발췌</div>'
                f'<div style="font-size:0.82rem;color:{TEXT_DARK};'
                f'background:#F0FFF4;border-radius:5px;padding:0.6rem 0.8rem;'
                f'border-left:3px solid {GREEN};">'
                + (
                    f'<div style="font-weight:700;color:{NAVY};font-size:0.85rem;'
                    f'padding-bottom:0.35rem;margin-bottom:0.45rem;'
                    f'border-bottom:1px dashed #B8C9F5;">{heading_html}</div>'
                    if heading_html else ""
                )
                + f'<div style="line-height:1.5;">{body_html}</div>'
                + '</div>',
                unsafe_allow_html=True,
            )
        else:
            msg = (
                "해당 항목을 사업보고서 주석에서 자동 확인하지 못했습니다. "
                "아래 원문을 직접 검토하세요."
                if policy_data else
                "사업보고서 데이터가 없습니다."
            )
            st.markdown(
                insight(msg, level="warn"),
                unsafe_allow_html=True,
            )

    st.markdown("</div>", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# 정책 변화 — 연도별 동일 item_key 비교 (AccountingPolicyItem 테이블 기반)
# ---------------------------------------------------------------------------
st.markdown("<div style='margin:1rem 0'></div>", unsafe_allow_html=True)
st.markdown(section_title("정책 변화 (연도 비교)"), unsafe_allow_html=True)

from dart_platform.db.engine import get_session as _gs
from dart_platform.db.models import AccountingPolicyItem as _APItem

with _gs() as _session:
    _history_rows = (
        _session.query(
            _APItem.bsns_year,
            _APItem.fs_div,
            _APItem.item_key,
            _APItem.heading,
            _APItem.body,
            _APItem.body_hash,
            _APItem.body_length,
        )
        .filter_by(corp_code=corp_code)
        .order_by(_APItem.item_key, _APItem.bsns_year)
        .all()
    )

if not _history_rows:
    st.markdown(
        insight(
            "이 기업의 영속화된 회계정책 이력이 없습니다. "
            "`dart collect-policies <종목코드>` CLI로 사업보고서를 파싱해 저장하세요.",
            level="info",
        ),
        unsafe_allow_html=True,
    )
else:
    # 그룹핑: item_key → [{year, fs_div, heading, body, hash, length}, ...]
    _grouped: dict[str, list[dict]] = {}
    for r in _history_rows:
        if r.fs_div != fs_div:
            continue  # 현재 선택된 fs_div만
        _grouped.setdefault(r.item_key, []).append({
            "year": r.bsns_year,
            "heading": r.heading,
            "body": r.body,
            "hash": r.body_hash,
            "length": r.body_length,
        })

    _years_seen = sorted({r.bsns_year for r in _history_rows if r.fs_div == fs_div})
    st.caption(
        f"영속화된 연도: {', '.join(str(y) for y in _years_seen)} ({fs_div}) · "
        f"총 item_key 수: {len(_grouped)}"
    )

    # 요약 매트릭스: item_key × 연도 → body_length 표 + 변화 플래그
    import pandas as _pd

    _matrix_rows = []
    for item_key, entries in sorted(_grouped.items()):
        row = {"item_key": item_key}
        by_year = {e["year"]: e for e in entries}
        for y in _years_seen:
            e = by_year.get(y)
            row[str(y)] = f"{e['length']:,}" if e else "-"
        # 변화 감지: 연속된 연도 간 hash 비교
        hashes = [by_year[y]["hash"] for y in _years_seen if y in by_year]
        if len(hashes) >= 2:
            changes = sum(1 for i in range(1, len(hashes)) if hashes[i] != hashes[i-1])
            row["변화"] = f"{changes}회" if changes else "동일"
        else:
            row["변화"] = "-"
        _matrix_rows.append(row)

    _matrix_df = _pd.DataFrame(_matrix_rows).set_index("item_key")
    st.caption("item_key별 연도별 body 길이 (문자 수) · '변화' 컬럼은 연속 연도 간 해시 비교")
    st.dataframe(_matrix_df, use_container_width=True)

    # 변화가 있는 item_key만 expander로 상세 표시
    _changed_keys = [
        k for k in _grouped
        if len({e["hash"] for e in _grouped[k]}) > 1 and len(_grouped[k]) >= 2
    ]
    if _changed_keys:
        st.markdown(
            f"<div style='margin-top:0.8rem;font-size:0.85rem;color:{PRIMARY};'>"
            f"변화가 감지된 item_key: {len(_changed_keys)}개</div>",
            unsafe_allow_html=True,
        )
        for item_key in _changed_keys:
            entries = sorted(_grouped[item_key], key=lambda x: x["year"])
            with st.expander(f"{item_key} — {len(entries)}개 연도"):
                for e in entries:
                    st.markdown(
                        f"<div style='font-weight:600;color:{TEXT_DARK};margin-top:0.4rem;'>"
                        f"[{e['year']}] {e['heading'] or '(제목 없음)'} "
                        f"<span style='color:{TEXT_MID};font-weight:400;font-size:0.75rem;'>"
                        f"· {e['length']:,}자 · hash={e['hash'][:8]}</span></div>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f"<div style='font-size:0.8rem;color:{TEXT_MID};"
                        f"background:#FAFBFF;padding:0.5rem 0.7rem;border-left:2px solid {PRIMARY};"
                        f"margin:0.3rem 0;max-height:200px;overflow-y:auto;'>"
                        f"{e['body'][:1000]}{'…' if len(e['body']) > 1000 else ''}"
                        f"</div>",
                        unsafe_allow_html=True,
                    )
    else:
        st.markdown(
            insight(
                "영속화된 연도 간 본문 해시 변화가 감지되지 않았습니다. "
                "정책 텍스트가 일관됩니다.",
                level="ok",
            ),
            unsafe_allow_html=True,
        )


# ---------------------------------------------------------------------------
# 회계정책 원문 전체
# ---------------------------------------------------------------------------
st.markdown("<div style='margin:1rem 0'></div>", unsafe_allow_html=True)
st.markdown(section_title("회계정책 원문"), unsafe_allow_html=True)

if policy_data and policy_data.get("raw_html"):
    rcept_no = policy_data.get("rcept_no", "")
    dart_link = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
    with st.expander(
        f"회계정책 원문 펼치기 — {sel_year}년 {fs_div} 사업보고서",
        expanded=False,
    ):
        if dart_link:
            st.markdown(
                f'<a href="{dart_link}" target="_blank" '
                f'style="font-size:0.78rem;color:{PRIMARY};">DART 원문 보기 ({rcept_no})</a>',
                unsafe_allow_html=True,
            )
        st.markdown(
            f'<div style="font-size:0.82rem;color:{TEXT_DARK};">'
            f'{policy_data["raw_html"]}</div>',
            unsafe_allow_html=True,
        )
else:
    st.markdown(
        no_data(
            f"{sel_year}년 {fs_div} 사업보고서 회계정책 원문을 불러올 수 없습니다."
            if policy_data is None else
            "회계정책 섹션을 사업보고서에서 찾지 못했습니다. 비표준 구조일 수 있습니다."
        ),
        unsafe_allow_html=True,
    )

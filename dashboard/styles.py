"""공유 CSS 스타일 및 헬퍼."""

PRIMARY = "#1E49E2"
NAVY = "#00338D"
WHITE = "#FFFFFF"
LIGHT_BG = "#F4F6FB"
BORDER = "#D1D8F0"
TEXT_DARK = "#1A1A2E"
TEXT_MID = "#5A6480"
RED = "#D0021B"
GREEN = "#1A7A4A"
ORANGE = "#C45000"

CSS = f"""
<style>
  /* 전체 배경 */
  .stApp {{ background-color: {LIGHT_BG}; }}

  /* 메인 영역 텍스트: 위젯 레이블, 캡션, 일반 텍스트 */
  .stApp p, .stApp label, .stApp span,
  .stApp div[data-testid="stMarkdownContainer"] p,
  div[data-testid="stRadio"] label p,
  div[data-testid="stRadio"] label span,
  div[data-testid="stSelectbox"] label,
  div[data-testid="stTextInput"] label,
  div[data-testid="stNumberInput"] label,
  div[data-testid="stCheckbox"] label p,
  .stCaption, .stCaption p {{
    color: {TEXT_DARK} !important;
  }}

  /* 사이드바 배경 (config.toml secondaryBackgroundColor와 동일) */
  section[data-testid="stSidebar"] {{
    background-color: {NAVY} !important;
  }}

  /* 사이드바 텍스트: 최대한 많은 선택자로 흰색 강제 */
  section[data-testid="stSidebar"],
  section[data-testid="stSidebar"] *,
  section[data-testid="stSidebar"] a,
  section[data-testid="stSidebar"] a *,
  section[data-testid="stSidebar"] li,
  section[data-testid="stSidebar"] li *,
  section[data-testid="stSidebar"] nav,
  section[data-testid="stSidebar"] nav *,
  section[data-testid="stSidebar"] ul,
  section[data-testid="stSidebar"] ul *,
  section[data-testid="stSidebar"] p,
  section[data-testid="stSidebar"] span,
  section[data-testid="stSidebar"] label,
  section[data-testid="stSidebar"] div,
  section[data-testid="stSidebar"] small,
  section[data-testid="stSidebar"] strong {{
    color: {WHITE} !important;
  }}

  /* 사이드바 링크 배경 투명화 (검은색 배경 제거) */
  section[data-testid="stSidebar"] a,
  section[data-testid="stSidebar"] li,
  section[data-testid="stSidebar"] nav > div,
  section[data-testid="stSidebar"] ul > li {{
    background-color: transparent !important;
  }}

  /* 사이드바 네비게이션 탭 텍스트 흰색 */
  section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] span,
  section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] p,
  section[data-testid="stSidebar"] [data-testid="stSidebarNavLink"] div,
  section[data-testid="stSidebarNavLink"] span,
  section[data-testid="stSidebarNavLink"] p,
  [data-testid="stSidebarNavLink"] span {{
    color: {WHITE} !important;
  }}

  /* 활성·호버 */
  section[data-testid="stSidebar"] a:hover,
  section[data-testid="stSidebar"] a:hover * {{
    background-color: rgba(255,255,255,0.12) !important;
    color: {WHITE} !important;
  }}

  /* 흰 배경 위젯(input, selectbox) 내부 글자 검은색 */
  div[data-testid="stTextInput"] input,
  .stTextInput input,
  input[type="text"],
  input[type="search"] {{
    color: {TEXT_DARK} !important;
    background-color: {WHITE} !important;
  }}

  /* 사이드바 내 모든 위젯 레이블 흰색 (전역 규칙 예외) */
  section[data-testid="stSidebar"] label,
  section[data-testid="stSidebar"] label *,
  section[data-testid="stSidebar"] [data-testid="stWidgetLabel"],
  section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] *,
  section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
  section[data-testid="stSidebar"] [data-testid="stWidgetLabel"] span,
  section[data-testid="stSidebar"] div[data-testid="stTextInput"] label,
  section[data-testid="stSidebar"] div[data-testid="stTextInput"] label *,
  section[data-testid="stSidebar"] div[data-testid="stSelectbox"] label,
  section[data-testid="stSidebar"] div[data-testid="stSelectbox"] label * {{
    color: {WHITE} !important;
  }}
  section[data-testid="stSidebar"] div[data-testid="stTextInput"] input::placeholder {{
    color: rgba(255,255,255,0.55) !important;
  }}

  /* 셀렉트박스 — 배경 흰색 고정 + 텍스트 검은색 */
  div[data-testid="stSelectbox"] div[data-baseweb="select"],
  div[data-testid="stSelectbox"] div[data-baseweb="select"] > div {{
    background-color: {WHITE} !important;
    border-color: {BORDER} !important;
  }}
  div[data-testid="stSelectbox"] div[data-baseweb="select"] *,
  div[data-testid="stSelectbox"] span,
  div[data-testid="stSelectbox"] div[class*="ValueContainer"] *,
  div[data-testid="stSelectbox"] div[class*="singleValue"],
  div[data-testid="stSelectbox"] input {{
    color: {TEXT_DARK} !important;
    background-color: transparent !important;
  }}

  /* 버튼 텍스트 — 파란 배경 위에 흰색 */
  div[data-testid="stButton"] button,
  div[data-testid="stButton"] button p,
  .stButton > button,
  .stButton > button p {{
    color: {WHITE} !important;
    background-color: {PRIMARY} !important;
    border-color: {PRIMARY} !important;
  }}
  div[data-testid="stButton"] button:hover,
  .stButton > button:hover {{
    background-color: {NAVY} !important;
    border-color: {NAVY} !important;
    color: {WHITE} !important;
  }}

  /* 헤더 */
  .dash-header {{
    background: linear-gradient(135deg, {NAVY} 0%, {PRIMARY} 100%);
    color: {WHITE};
    padding: 1.2rem 1.6rem;
    border-radius: 10px;
    margin-bottom: 1.2rem;
  }}
  .dash-header h1 {{
    color: {WHITE};
    margin: 0;
    font-size: 1.5rem;
    font-weight: 700;
    letter-spacing: -0.3px;
  }}
  .dash-header .sub {{
    color: rgba(255,255,255,0.75);
    font-size: 0.82rem;
    margin-top: 0.2rem;
  }}

  /* KPI 카드 */
  .kpi-card {{
    background: {WHITE};
    border: 1px solid {BORDER};
    border-radius: 10px;
    padding: 1rem 1.2rem;
    text-align: center;
    box-shadow: 0 1px 4px rgba(0,51,141,0.06);
  }}
  .kpi-label {{
    color: {TEXT_MID};
    font-size: 0.75rem;
    font-weight: 600;
    letter-spacing: 0.5px;
    text-transform: uppercase;
    margin-bottom: 0.3rem;
  }}
  .kpi-value {{
    color: {TEXT_DARK};
    font-size: 1.6rem;
    font-weight: 700;
    line-height: 1.2;
  }}
  .kpi-sub {{
    color: {TEXT_MID};
    font-size: 0.72rem;
    margin-top: 0.15rem;
  }}

  /* 위험 카드 */
  .risk-ok {{ border-top: 3px solid {GREEN}; }}
  .risk-warn {{ border-top: 3px solid {ORANGE}; }}
  .risk-bad {{ border-top: 3px solid {RED}; }}

  /* 섹션 제목 */
  .section-title {{
    color: {NAVY};
    font-size: 0.9rem;
    font-weight: 700;
    letter-spacing: 0.3px;
    border-left: 3px solid {PRIMARY};
    padding-left: 0.6rem;
    margin: 1.2rem 0 0.6rem 0;
  }}

  /* 인사이트 박스 */
  .insight-box {{
    background: #EEF2FF;
    border: 1px solid {BORDER};
    border-left: 4px solid {PRIMARY};
    border-radius: 6px;
    padding: 0.7rem 1rem;
    font-size: 0.82rem;
    color: {TEXT_DARK};
    margin-bottom: 0.5rem;
  }}
  .insight-box.warn {{
    background: #FFF4EC;
    border-left-color: {ORANGE};
  }}
  .insight-box.risk {{
    background: #FFF0F0;
    border-left-color: {RED};
  }}

  /* 테이블 */
  .dataframe tbody tr:hover {{ background-color: #EEF2FF !important; }}

  /* 배지 */
  .badge {{
    display: inline-block;
    padding: 0.2rem 0.5rem;
    border-radius: 4px;
    font-size: 0.72rem;
    font-weight: 600;
  }}
  .badge-risk {{ background: #FFE5E5; color: {RED}; }}
  .badge-ok {{ background: #E5F5EE; color: {GREEN}; }}
  .badge-neutral {{ background: #F0F0F0; color: #555; }}

  /* 툴팁 */
  .kpi-tooltip-wrap {{
    position: relative;
    display: inline-block;
    cursor: help;
  }}
  .kpi-tooltip-wrap .kpi-tooltip-text,
  .kpi-tooltip-wrap .kpi-tooltip-text * {{
    visibility: hidden;
    opacity: 0;
    color: #FFFFFF !important;
  }}
  .kpi-tooltip-wrap .kpi-tooltip-text {{
    width: 260px;
    background: #1A1A2E !important;
    font-size: 0.72rem;
    line-height: 1.5;
    text-align: left;
    border-radius: 6px;
    padding: 0.5rem 0.7rem;
    position: absolute;
    z-index: 9999;
    bottom: 130%;
    left: 50%;
    transform: translateX(-50%);
    transition: opacity 0.15s;
    white-space: normal;
    pointer-events: none;
    box-shadow: 0 4px 12px rgba(0,0,0,0.25);
    font-weight: 400;
    text-transform: none;
    letter-spacing: normal;
  }}
  .kpi-tooltip-wrap:hover .kpi-tooltip-text,
  .kpi-tooltip-wrap:hover .kpi-tooltip-text * {{
    visibility: visible;
    opacity: 1;
    color: #FFFFFF !important;
  }}
  .kpi-tooltip-icon {{
    font-size: 0.65rem;
    opacity: 0.6;
    margin-left: 3px;
    vertical-align: super;
  }}

  /* 정보 없음 */
  .no-data-box {{
    background: {WHITE};
    border: 1px dashed {BORDER};
    border-radius: 10px;
    padding: 2rem;
    text-align: center;
    color: {TEXT_MID};
  }}

  /* 탭 버튼 — secondaryBackgroundColor(네이비) 위에 흰색 텍스트 */
  div[data-testid="stTabs"] button[data-baseweb="tab"],
  div[data-testid="stTabs"] button[data-baseweb="tab"] p,
  div[data-testid="stTabs"] button[data-baseweb="tab"] span {{
    color: {WHITE} !important;
  }}

  /* 활성 탭 — 흰색 하단 밑줄로 강조 */
  div[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {{
    color: {WHITE} !important;
    border-bottom-color: {WHITE} !important;
  }}

  /* 비활성 탭 — 약간 투명 처리로 구분 */
  div[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="false"] {{
    color: rgba(255,255,255,0.65) !important;
    border-bottom-color: transparent !important;
  }}

  /* 탭 패널(콘텐츠 영역) — 밝은 배경 위 어두운 텍스트 */
  div[data-testid="stTabsContent"],
  div[data-testid="stTabsContent"] p,
  div[data-testid="stTabsContent"] span,
  div[data-testid="stTabsContent"] label {{
    color: {TEXT_DARK} !important;
  }}

  /* st.caption — secondaryBg(네이비) 위에 흰색, 일반 배경 위에는 TEXT_MID */
  .stCaption, .stCaption p, [data-testid="stCaptionContainer"],
  [data-testid="stCaptionContainer"] p {{
    color: {TEXT_MID} !important;
    background-color: transparent !important;
  }}

  /* st.info — 파란 배경 위 흰색 텍스트 */
  div[data-testid="stAlert"],
  div[data-testid="stAlert"] p,
  div[data-testid="stAlert"] span,
  div[data-testid="stAlert"] div {{
    color: {TEXT_DARK} !important;
  }}
  /* info 타입(파란 계열) — 흰 텍스트로 강제 */
  div[data-testid="stAlert"][data-baseweb="notification"],
  div[class*="stAlert"] div[class*="alertContainer"] p,
  div[class*="stInfo"] p,
  div[class*="stInfo"] span {{
    color: {TEXT_DARK} !important;
  }}

  /* st.info / st.warning 내부 배경 명시적으로 밝게 */
  div[data-testid="stAlert"] {{
    background-color: #EEF2FF !important;
    border-left: 4px solid {PRIMARY} !important;
    border-radius: 6px !important;
  }}
  div[data-testid="stAlert"][aria-label*="warning"],
  div[data-testid="stAlert"][kind="warning"] {{
    background-color: #FFF4EC !important;
    border-left-color: {ORANGE} !important;
  }}
  div[data-testid="stAlert"][aria-label*="error"],
  div[data-testid="stAlert"][kind="error"] {{
    background-color: #FFF0F0 !important;
    border-left-color: {RED} !important;
  }}
  div[data-testid="stAlert"][aria-label*="success"],
  div[data-testid="stAlert"][kind="success"] {{
    background-color: #E5F5EE !important;
    border-left-color: {GREEN} !important;
  }}

  /* checkbox 텍스트 */
  div[data-testid="stCheckbox"] label p,
  div[data-testid="stCheckbox"] span {{
    color: {TEXT_DARK} !important;
  }}
</style>
"""


def page_header(title: str, subtitle: str = ""):
    sub_html = (
        f'<div style="color:rgba(255,255,255,0.8);font-size:0.82rem;margin-top:0.3rem;">{subtitle}</div>'
        if subtitle else ""
    )
    return f"""
    <div style="background:linear-gradient(135deg,{NAVY} 0%,{PRIMARY} 100%);
                color:{WHITE};padding:1.2rem 1.6rem;border-radius:10px;
                margin-bottom:1.2rem;">
      <div style="color:{WHITE};margin:0;font-size:1.4rem;font-weight:700;
                  letter-spacing:-0.3px;">{title}</div>
      {sub_html}
    </div>
    """


def kpi_card(label: str, value: str, sub: str = "", risk_level: str = "ok", tooltip: str = "") -> str:
    top_color = {"ok": GREEN, "warn": ORANGE, "bad": RED}.get(risk_level, GREEN)
    if tooltip:
        label_html = (
            f'<span class="kpi-tooltip-wrap">'
            f'{label}<sup class="kpi-tooltip-icon">?</sup>'
            f'<span class="kpi-tooltip-text">{tooltip}</span>'
            f'</span>'
        )
    else:
        label_html = label
    # 값 길이에 따라 폰트 크기 자동 조절 (한 줄 유지)
    vlen = len(value)
    if vlen > 12:
        val_size = "1.0rem"
    elif vlen > 8:
        val_size = "1.2rem"
    elif vlen > 5:
        val_size = "1.4rem"
    else:
        val_size = "1.6rem"
    return f"""
    <div style="background:{WHITE};border:1px solid {BORDER};border-radius:10px;
                border-top:3px solid {top_color};padding:0.8rem 0.6rem;text-align:center;
                box-shadow:0 1px 4px rgba(0,51,141,0.06);min-height:90px;
                display:flex;flex-direction:column;justify-content:center;">
      <div style="color:{TEXT_MID};font-size:0.7rem;font-weight:600;
                  letter-spacing:0.3px;text-transform:uppercase;margin-bottom:0.2rem;
                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{label_html}</div>
      <div style="color:{TEXT_DARK};font-size:{val_size};font-weight:700;line-height:1.15;
                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{value}</div>
      <div style="color:{TEXT_MID};font-size:0.68rem;margin-top:0.1rem;
                  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{sub}</div>
    </div>
    """


def section_title(text: str) -> str:
    return (
        f'<div style="color:{NAVY};font-size:0.9rem;font-weight:700;letter-spacing:0.3px;'
        f'border-left:3px solid {PRIMARY};padding-left:0.6rem;'
        f'margin:1.2rem 0 0.6rem 0;">{text}</div>'
    )


def insight(text: str, level: str = "info") -> str:
    bg = {"warn": "#FFF4EC", "risk": "#FFF0F0"}.get(level, "#EEF2FF")
    border = {"warn": ORANGE, "risk": RED}.get(level, PRIMARY)
    return (
        f'<div style="background:{bg};border:1px solid {BORDER};border-left:4px solid {border};'
        f'border-radius:6px;padding:0.7rem 1rem;font-size:0.82rem;color:{TEXT_DARK};'
        f'margin-bottom:0.5rem;">{text}</div>'
    )


def no_data(msg: str = "수집된 데이터가 없습니다.", state: str = "not_collected") -> str:
    """데이터 없음 안내 박스.

    state:
        "not_collected" — 아직 수집되지 않음 (수집 버튼 안내)
        "empty" — 수집했으나 DART에 해당 데이터가 없음
    """
    if state == "empty":
        icon = "🔍"
        sub = ('<div style="font-size:0.78rem;margin-top:0.3rem;opacity:0.7;">'
               'DART 공시 데이터에 해당 항목이 없습니다.</div>')
    else:
        icon = "📭"
        sub = ('<div style="font-size:0.78rem;margin-top:0.3rem;opacity:0.7;">'
               '아래 버튼으로 데이터를 수집하세요.</div>')
    return (
        f'<div style="background:{WHITE};border:1px dashed {BORDER};border-radius:10px;'
        f'padding:2rem;text-align:center;color:{TEXT_MID};">'
        f'<div style="font-size:2rem;margin-bottom:0.5rem;">{icon}</div>'
        f'<div style="font-weight:600;color:{TEXT_DARK};">{msg}</div>{sub}</div>'
    )

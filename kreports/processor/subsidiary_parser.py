"""
subsidiary_parser.py
사업보고서 document.xml에서 종속회사·지분법 회사 테이블을 추출한다.

DART 사업보고서 구조:
- 각 데이터 셀은 ACODE / AUNIT 속성으로 필드가 태깅됨
- TABLE-GROUP의 ACLASS로 섹션 식별

종속회사  : TABLE-GROUP ACLASS="SUB_CMPN" (또는 SUB_CMPK 등)
  CRP_NM  = 법인명
  M_IND   = 주요사업
  KEY_IND = 최근사업연도말 자산총액

타법인출자: TABLE-GROUP ACLASS="INV_PRT"
  INV_PRM         = 법인명
  INV_LPR         = 기말 지분율 (%)
  INV_NTM         = 총자산
  @INV_YN (AUNIT) = 상장여부 (Y/N)
"""
from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# 블록 추출
# ---------------------------------------------------------------------------

_RE_SUB_GROUP = re.compile(
    r'<TABLE-GROUP[^>]*ACLASS="(SUB_CMP[A-Z_]*)"[^>]*>(.*?)</TABLE-GROUP>',
    re.DOTALL,
)
_RE_INV_GROUP = re.compile(
    r'<TABLE-GROUP[^>]*ACLASS="INV_PRT"[^>]*>(.*?)</TABLE-GROUP>',
    re.DOTALL,
)

def _tbody_rows(block: str) -> list[str]:
    """TBODY 안의 TR만 반환 (THEAD 제외)."""
    rows: list[str] = []
    for m in re.finditer(r'<TBODY[^>]*>(.*?)</TBODY>', block, re.DOTALL):
        rows.extend(re.findall(r'<TR[^>]*>(.*?)</TR>', m.group(1), re.DOTALL))
    return rows

# ---------------------------------------------------------------------------
# 셀 파싱
# ---------------------------------------------------------------------------

_RE_ACODE = re.compile(
    r'<T[EDUH][^>]*\sACODE="([^"]+)"[^>]*>(.*?)</T[EDUH]>', re.DOTALL
)
_RE_AUNIT = re.compile(
    r'<T[EDUH][^>]*\sAUNIT="([^"]+)"[^>]*\sAUNITVALUE="([^"]*)"', re.DOTALL
)

def _row_fields(row_html: str) -> dict[str, str]:
    """TR → {ACODE: 텍스트, '@AUNIT': AUNITVALUE}"""
    out: dict[str, str] = {}
    for m in _RE_ACODE.finditer(row_html):
        code = m.group(1).upper()
        val = re.sub(r'<[^>]+>', ' ', m.group(2))
        val = re.sub(r'\s+', ' ', val).strip()
        out.setdefault(code, val)
    for m in _RE_AUNIT.finditer(row_html):
        out.setdefault(f'@{m.group(1).upper()}', m.group(2))
    return out

# ---------------------------------------------------------------------------
# 회사명 정리
# ---------------------------------------------------------------------------

_FOOTNOTE = re.compile(r'\(주\s*\d+\)|\(주\)\s*\d+|\[주\d+\]')

def _clean_name(raw: str) -> str:
    name = _FOOTNOTE.sub('', raw)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def _skip(name: str) -> bool:
    return not name or len(name) < 2 or bool(re.match(r'^(합계|계|소계|총계)$', name))

# ---------------------------------------------------------------------------
# 지분율 → 관계 분류
# ---------------------------------------------------------------------------

def _pct(s: str) -> float | None:
    cleaned = re.sub(r'[^\d.\-]', '', s or '')
    try:
        return float(cleaned) if cleaned not in ('', '-') else None
    except ValueError:
        return None

def _relation(pct: float | None) -> str:
    if pct is None:
        return "기타"
    return "종속" if pct >= 50 else ("지분법" if pct >= 20 else "기타")

# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def extract_subsidiaries(content: str) -> list[dict]:
    """종속회사 현황 파싱."""
    items: list[dict] = []
    for m in _RE_SUB_GROUP.finditer(content):
        for row in _tbody_rows(m.group(2)):
            source_ordinal = len(items)
            f = _row_fields(row)
            original_name = f.get('CRP_NM') or f.get('CORP_NM') or ''
            name = _clean_name(original_name)
            if _skip(name):
                continue
            items.append({
                'name': name,
                'original_name': original_name.strip(),
                'parent_name': (f.get('PARENT_NM') or f.get('PRNT_NM') or '').strip(),
                'corp_code': (f.get('CORP_CODE') or f.get('CRP_CD') or '').strip(),
                'ownership_pct': None,
                'listed_yn': '',
                'business': f.get('M_IND', ''),
                'assets': f.get('KEY_IND', ''),
                'revenue': f.get('REV_AMT', '') or f.get('REVENUE', ''),
                'asset_unit': f.get('KEY_UNIT') or None,
                'revenue_unit': f.get('REV_UNIT') or None,
                'period': f.get('PERIOD') or None,
                'fs_div': f.get('FS_DIV') or None,
                'elimination_basis': f.get('ELIM_BASIS') or None,
                'denominator_elimination_basis': (
                    f.get('DENOM_ELIM_BASIS') or None
                ),
                'relation': '종속',
                'source': m.group(1),
                'source_ordinal': source_ordinal,
            })
    return items


def extract_equity_investments(content: str) -> list[dict]:
    """타법인출자 현황 파싱. 지분율 기준 종속/지분법/기타 분류."""
    items: list[dict] = []
    for m in _RE_INV_GROUP.finditer(content):
        for row in _tbody_rows(m.group(1)):
            source_ordinal = len(items)
            f = _row_fields(row)
            original_name = f.get('INV_PRM') or f.get('CRP_NM') or ''
            name = _clean_name(original_name)
            if _skip(name):
                continue
            p = _pct(f.get('INV_LPR', ''))
            items.append({
                'name': name,
                'original_name': original_name.strip(),
                'parent_name': (f.get('PARENT_NM') or f.get('PRNT_NM') or '').strip(),
                'corp_code': (f.get('CORP_CODE') or f.get('CRP_CD') or '').strip(),
                'ownership_pct': p,
                'listed_yn': f.get('@INV_YN', ''),
                'business': f.get('INV_OBJ', ''),
                'assets': f.get('INV_NTM', ''),
                'revenue': f.get('REV_AMT', '') or f.get('REVENUE', ''),
                'asset_unit': f.get('INV_UNIT') or None,
                'revenue_unit': f.get('REV_UNIT') or None,
                'period': f.get('PERIOD') or None,
                'fs_div': f.get('FS_DIV') or None,
                'elimination_basis': f.get('ELIM_BASIS') or None,
                'denominator_elimination_basis': (
                    f.get('DENOM_ELIM_BASIS') or None
                ),
                'relation': _relation(p),
                'source': 'INV_PRT',
                'source_ordinal': source_ordinal,
            })
    return items


def extract_affiliates_from_report(content: str) -> list[dict]:
    """Return every disclosed source claim; identity resolution happens later."""
    return [
        *extract_subsidiaries(content),
        *extract_equity_investments(content),
    ]

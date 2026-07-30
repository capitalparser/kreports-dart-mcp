"""
DART XBRL 인스턴스 문서 파서.

fnlttXbrlDs003 API가 반환하는 ZIP 안의 .xbrl 파일을 파싱하여
FinancialFact 형식의 dict 리스트를 반환한다.

XBRL 인스턴스 문서 구조:
  <xbrl>
    <context id="...">  ← 기간/연결구분 정의
    <unit id="KRW">     ← 통화 단위
    <ifrs-full:Revenue contextRef="..." decimals="-6" unitRef="KRW">
      17300000000000
    </ifrs-full:Revenue>
    ...
  </xbrl>
"""
import io
import logging
import re
import zipfile
from lxml import etree

logger = logging.getLogger(__name__)

# DART XBRL에서 사용하는 주요 네임스페이스
_NS_XBRLI = "http://www.xbrl.org/2003/instance"
_NS_XBRLDI = "http://xbrl.org/2006/xbrldi"

# 연결/별도 구분 dimension (DART 확장 taxonomy)
_FS_DIV_PATTERNS = {
    "ConsolidatedMember": "CFS",
    "SeparateMember": "OFS",
}

# 재무제표 종류 → sj_div 매핑 (element 네임스페이스 prefix 기준)
# DART는 StatementOfFinancialPosition, IncomeStatement 등의 role로 구분
_ROLE_TO_SJ: dict[str, str] = {
    "StatementOfFinancialPosition": "BS",
    "BalanceSheet": "BS",
    "IncomeStatement": "IS",
    "StatementOfComprehensiveIncome": "CIS",
    "StatementOfCashFlows": "CF",
    "StatementOfChangesInEquity": "SCE",
}


def parse_xbrl_zip(
    zip_bytes: bytes,
    corp_code: str,
    bsns_year: int,
    reprt_code: str,
    fs_div: str,
) -> list[dict]:
    """
    XBRL ZIP bytes를 파싱하여 FinancialFact 형식의 dict 리스트 반환.

    ZIP 안에 여러 .xbrl 파일이 있을 수 있다:
    - *_CFS.xbrl (연결), *_OFS.xbrl (별도), 또는 단일 파일

    Args:
        zip_bytes: DART fnlttXbrlDs003 응답 ZIP
        corp_code: 기업코드
        bsns_year: 사업연도
        reprt_code: 보고서 코드
        fs_div: 수집 대상 구분 (CFS/OFS)

    Returns:
        FinancialFact dict 리스트 (빈 리스트 = 파싱 실패)
    """
    results: list[dict] = []
    try:
        label_map = extract_label_map(zip_bytes)
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            xbrl_files = [n for n in zf.namelist() if n.lower().endswith(".xbrl")]
            if not xbrl_files:
                logger.warning("XBRL ZIP에 .xbrl 파일이 없습니다.")
                return []

            for fname in xbrl_files:
                # CFS/OFS 필터: 파일명으로 구분 가능한 경우
                fup = fname.upper()
                if "CFS" in fup and fs_div == "OFS":
                    continue
                if "OFS" in fup and fs_div == "CFS":
                    continue

                with zf.open(fname) as f:
                    raw = f.read()

                facts = _parse_xbrl_instance(raw, corp_code, bsns_year, reprt_code, fs_div, label_map)
                results.extend(facts)
                logger.info("XBRL 파싱 완료 [%s]: %d facts", fname, len(facts))

    except zipfile.BadZipFile:
        logger.error("유효하지 않은 ZIP 파일입니다.")
    except Exception as e:
        logger.error("XBRL ZIP 파싱 오류: %s", e)

    return results


def _parse_xbrl_instance(
    xml_bytes: bytes,
    corp_code: str,
    bsns_year: int,
    reprt_code: str,
    fs_div: str,
    label_map: dict[str, str] | None = None,
) -> list[dict]:
    """XBRL 인스턴스 XML bytes → FinancialFact dict 리스트."""
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as e:
        logger.error("XBRL XML 파싱 오류: %s", e)
        return []

    label_map = label_map or {}

    # 1. context 파싱: context_id → {period_start, period_end, period_type, fs_div}
    contexts = _parse_contexts(root)
    units = _parse_units(root)

    # 2. 대상 period 결정 (당기 = bsns_year)
    target_year = str(bsns_year)

    # 3. 모든 금액 fact 추출
    results: list[dict] = []
    seen: set[tuple] = set()

    for elem in root:
        tag = elem.tag
        if not tag.startswith("{"):
            continue

        # 네임스페이스와 로컬명 분리
        ns_uri, local = tag[1:].split("}", 1)
        # xbrli 시스템 요소 제외
        if ns_uri == _NS_XBRLI:
            continue
        # 텍스트 값 없으면 제외
        text = (elem.text or "").strip()
        if not text:
            continue
        # 숫자 값만 처리
        try:
            amount = int(float(text))
        except ValueError:
            continue

        context_ref = elem.get("contextRef", "")
        ctx = contexts.get(context_ref, {})
        unit_ref = elem.get("unitRef", "")
        if unit_ref and not _is_krw_unit(unit_ref, units):
            continue

        # 당기 기간만 처리
        period_end = ctx.get("period_end", "")
        if target_year not in period_end:
            continue

        # fs_div 일치 확인
        ctx_fs = ctx.get("fs_div", "")
        if ctx_fs and ctx_fs != fs_div:
            continue

        # account_id: 네임스페이스 prefix_로컬명 형식
        ns_prefix = _ns_to_prefix(ns_uri)
        account_id = f"{ns_prefix}_{local}" if ns_prefix else local

        # sj_div 추론 (element 이름 기반)
        sj_div = _infer_sj_div(local, ctx)

        # decimals → 원화 단위 변환 (decimals=-6이면 백만원 단위 → 원 단위)
        decimals = elem.get("decimals", "0")
        try:
            dec_val = int(decimals)
            if dec_val < 0:
                amount = amount * (10 ** (-dec_val))
        except ValueError:
            pass

        dedup = (sj_div, account_id, context_ref, unit_ref)
        if dedup in seen:
            continue
        seen.add(dedup)

        results.append({
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
            "sj_div": sj_div,
            "account_id": account_id,
            "account_nm": label_map.get(account_id, local),
            "ord": None,
            "thstrm_amount": amount,
            "frmtrm_amount": None,
            "bfefrmtrm_amount": None,
            "thstrm_add_amount": None,
        })

    return results


def _parse_contexts(root: etree._Element) -> dict[str, dict]:
    """
    <context> 요소를 파싱하여 context_id → 메타 dict 반환.
    메타: period_start, period_end, period_type(instant/duration), fs_div(CFS/OFS/None)
    """
    contexts: dict[str, dict] = {}
    xbrli_ns = _NS_XBRLI

    for ctx_elem in root.findall(f"{{{xbrli_ns}}}context"):
        ctx_id = ctx_elem.get("id", "")
        meta: dict = {"period_type": "duration", "period_start": "", "period_end": "", "fs_div": ""}

        period = ctx_elem.find(f"{{{xbrli_ns}}}period")
        if period is not None:
            instant = period.find(f"{{{xbrli_ns}}}instant")
            if instant is not None and instant.text:
                meta["period_type"] = "instant"
                meta["period_end"] = instant.text.strip()
            else:
                start = period.find(f"{{{xbrli_ns}}}startDate")
                end = period.find(f"{{{xbrli_ns}}}endDate")
                if start is not None and start.text:
                    meta["period_start"] = start.text.strip()
                if end is not None and end.text:
                    meta["period_end"] = end.text.strip()

        # segment/scenario → CFS/OFS 구분
        entity = ctx_elem.find(f"{{{xbrli_ns}}}entity")
        containers = []
        if entity is not None:
            segment = entity.find(f"{{{xbrli_ns}}}segment")
            if segment is not None:
                containers.append(segment)
        scenario = ctx_elem.find(f"{{{xbrli_ns}}}scenario")
        if scenario is not None:
            containers.append(scenario)

        dimensions: list[str] = []
        for container in containers:
            for member_elem in container.iter():
                member_text = (member_elem.text or "").strip()
                if member_text:
                    dimensions.append(member_text)
                for pattern, div in _FS_DIV_PATTERNS.items():
                    if pattern in member_text:
                        meta["fs_div"] = div
                        break
        meta["dimensions"] = dimensions

        contexts[ctx_id] = meta

    return contexts


def _parse_units(root: etree._Element) -> dict[str, set[str]]:
    """XBRL unit id별 measure 목록을 반환한다."""
    units: dict[str, set[str]] = {}
    for unit_elem in root.findall(f"{{{_NS_XBRLI}}}unit"):
        unit_id = unit_elem.get("id", "")
        if not unit_id:
            continue
        measures: set[str] = set()
        for measure in unit_elem.findall(f".//{{{_NS_XBRLI}}}measure"):
            text = (measure.text or "").strip()
            if text:
                measures.add(text)
        units[unit_id] = measures
    return units


def _is_krw_unit(unit_ref: str, units: dict[str, set[str]]) -> bool:
    """KRW monetary unit이면 True. unit 정의가 없으면 보수적으로 id만 확인한다."""
    measures = units.get(unit_ref)
    if not measures:
        return unit_ref.upper() in {"KRW", "KRW_UNIT"}
    return any(measure.upper().endswith(":KRW") or measure.upper() == "KRW" for measure in measures)


def _ns_to_prefix(ns_uri: str) -> str:
    """네임스페이스 URI → 짧은 prefix."""
    if "ifrs.org" in ns_uri or "ifrs-full" in ns_uri:
        return "ifrs-full"
    if "dart.fss" in ns_uri or "/dart" in ns_uri:
        return "dart"
    if "kr-ifrs" in ns_uri:
        return "dart"
    return ""


def _infer_sj_div(local_name: str, ctx: dict) -> str:
    """element 이름과 context에서 재무제표 종류(sj_div)를 추론한다."""
    period_type = ctx.get("period_type", "duration")

    # Balance sheet 항목은 instant
    if period_type == "instant":
        return "BS"

    # 이름 패턴으로 추론
    name_upper = local_name.upper()
    if any(k in name_upper for k in ("CASHFLOW", "CASHEQUIV", "OPERATINGCASH")):
        return "CF"
    if "COMPREHENSIVE" in name_upper:
        return "CIS"
    if "EQUITY" in name_upper and "CHANGES" in name_upper:
        return "SCE"

    return "IS"


def extract_label_map(zip_bytes: bytes) -> dict[str, str]:
    """
    XBRL ZIP 안의 레이블 파일(*_lab.xml 또는 *label*.xml)에서
    element_id → 한글 레이블 매핑을 추출한다.
    """
    labels: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            lab_files = [n for n in zf.namelist()
                         if "label" in n.lower() and n.lower().endswith(".xml")]
            for fname in lab_files:
                with zf.open(fname) as f:
                    _extract_labels_from_xml(f.read(), labels)
    except Exception as e:
        logger.warning("레이블 파일 파싱 오류: %s", e)
    return labels


def _extract_labels_from_xml(xml_bytes: bytes, labels: dict[str, str]) -> None:
    """xlink:label 기반 한글 레이블 추출."""
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return

    xlink_ns = "http://www.w3.org/1999/xlink"
    lang_ko = "ko"

    for label_elem in root.iter():
        tag_local = etree.QName(label_elem).localname
        if tag_local != "label":
            continue
        lang = label_elem.get("{http://www.w3.org/XML/1998/namespace}lang", "")
        if lang != lang_ko:
            continue
        xlink_label = label_elem.get(f"{{{xlink_ns}}}label", "")
        text = (label_elem.text or "").strip()
        if xlink_label and text:
            # label 속성에서 element ID 추출: "label_ifrs-full_Revenue" → "ifrs-full_Revenue"
            element_id = re.sub(r"^label_", "", xlink_label)
            labels[element_id] = text

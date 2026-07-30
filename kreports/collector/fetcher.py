import html
import logging
import time
import zipfile
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from kreports.config import settings

logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"
DART_WEB_BASE = "https://dart.fss.or.kr"
CORP_CODE_ZIP_URL = f"{DART_BASE}/corpCode.xml"  # zip 반환
_DART_LIMIT_MARKERS = ("사용한도", "초과", "limit")

# 로컬 캐시 경로 (30일 유효)
_CACHE_DIR = Path(__file__).parent.parent.parent / ".cache"
_CORP_ZIP_CACHE = _CACHE_DIR / "corp_code.zip"
_CACHE_MAX_AGE_DAYS = 30


class DartApiLimitExceeded(RuntimeError):
    """Raised when DART reports that the API key has exhausted its call quota."""


def _get_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


def _decode_dart_text(content: bytes, fallback_encoding: str | None = None) -> str:
    for enc in ("utf-8", fallback_encoding, "euc-kr", "cp949"):
        if not enc:
            continue
        try:
            return content.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return content.decode("utf-8", errors="replace")


def _looks_like_dart_error_xml(text: str) -> bool:
    return bool(
        re.search(r"<status>\s*(?!000\b)[^<]+</status>", text or "", flags=re.IGNORECASE)
        and re.search(r"<message\b", text or "", flags=re.IGNORECASE)
    )


def _dart_error_from_xml(text: str) -> tuple[str | None, str | None]:
    status_match = re.search(r"<status>\s*([^<]+?)\s*</status>", text or "", flags=re.IGNORECASE)
    message_match = re.search(r"<message>\s*([^<]+?)\s*</message>", text or "", flags=re.IGNORECASE)
    status = html.unescape(status_match.group(1).strip()) if status_match else None
    message = html.unescape(message_match.group(1).strip()) if message_match else None
    if status == "000":
        return None, None
    return status, message


def _is_dart_limit_error(status: str | None, message: str | None) -> bool:
    if not status:
        return False
    msg = (message or "").lower()
    return status != "000" and any(marker in msg for marker in _DART_LIMIT_MARKERS)


def _raise_if_dart_limit(status: str | None, message: str | None) -> None:
    if _is_dart_limit_error(status, message):
        raise DartApiLimitExceeded(message or "DART API limit exceeded")


def _looks_like_report_document_xml(text: str) -> bool:
    return bool(
        re.search(r"<DOCUMENT\b", text or "", flags=re.IGNORECASE)
        or re.search(r"<DOCUMENT-NAME\b", text or "", flags=re.IGNORECASE)
    )


def _raw_document_xml_from_response(content: bytes, fallback_encoding: str | None = None) -> str | None:
    text = _decode_dart_text(content, fallback_encoding).strip()
    if not text or _looks_like_dart_error_xml(text):
        return None
    if _looks_like_report_document_xml(text):
        return text
    return None


def _check_api_key() -> None:
    if not settings.dart_api_key:
        raise ValueError(
            "DART_API_KEY가 설정되지 않았습니다. "
            ".env 파일에 DART_API_KEY=your_key 를 추가하세요."
        )


# ---------------------------------------------------------------------------
# 기업 코드 목록 (corp_code.zip)
# ---------------------------------------------------------------------------

def fetch_corp_code_xml() -> list[dict]:
    """
    DART에서 기업코드 ZIP을 다운로드하고 XML을 파싱하여 기업 목록을 반환한다.
    로컬 캐시가 30일 이내면 캐시를 사용한다.

    Returns:
        [{"corp_code": str, "corp_name": str, "stock_code": str|None,
          "market": str|None}, ...]
    """
    _check_api_key()
    _CACHE_DIR.mkdir(exist_ok=True)

    # 캐시 유효성 확인
    if _CORP_ZIP_CACHE.exists():
        age_days = (time.time() - _CORP_ZIP_CACHE.stat().st_mtime) / 86400
        if age_days < _CACHE_MAX_AGE_DAYS:
            logger.info("기업코드 캐시 사용 (%.1f일 경과)", age_days)
            return _parse_corp_zip(_CORP_ZIP_CACHE.read_bytes())

    logger.info("기업코드 ZIP 다운로드 중...")
    with _get_client() as client:
        resp = client.get(
            CORP_CODE_ZIP_URL,
            params={"crtfc_key": settings.dart_api_key},
        )
        resp.raise_for_status()

    _CORP_ZIP_CACHE.write_bytes(resp.content)
    logger.info("기업코드 ZIP 저장 완료 (%d bytes)", len(resp.content))
    return _parse_corp_zip(resp.content)


def _parse_corp_zip(zip_bytes: bytes) -> list[dict]:
    companies = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        xml_name = next(n for n in zf.namelist() if n.endswith(".xml"))
        with zf.open(xml_name) as f:
            tree = ET.parse(f)

    for elem in tree.getroot().iter("list"):
        stock_code = elem.findtext("stock_code", "").strip() or None
        companies.append({
            "corp_code": elem.findtext("corp_code", "").strip(),
            "corp_name": elem.findtext("corp_name", "").strip(),
            "stock_code": stock_code if stock_code and stock_code != " " else None,
            "market": None,  # corpCode.xml에는 시장 구분 없음, 별도 API로 보강 필요
        })

    logger.info("기업 목록 파싱 완료: %d개", len(companies))
    return companies


# ---------------------------------------------------------------------------
# 재무제표 API
# ---------------------------------------------------------------------------

def fetch_financial_statements(
    corp_code: str,
    bsns_year: int,
    reprt_code: str,
    fs_div: str = "CFS",
) -> dict:
    """
    DART 단일회사 전체 재무제표 API 호출.

    Args:
        corp_code: 8자리 기업코드
        bsns_year: 사업연도 (예: 2024)
        reprt_code: 11013=Q1, 11012=Q2(반기), 11014=Q3, 11011=Q4(사업보고서)
        fs_div: CFS(연결) / OFS(별도)

    Returns:
        DART API 응답 dict. status "000"이면 정상.

    Raises:
        httpx.HTTPError: 네트워크 오류
        ValueError: API 키 미설정
    """
    _check_api_key()

    params = {
        "crtfc_key": settings.dart_api_key,
        "corp_code": corp_code,
        "bsns_year": str(bsns_year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }

    for attempt in range(settings.max_retries):
        try:
            with _get_client() as client:
                resp = client.get(f"{DART_BASE}/fnlttSinglAcntAll.json", params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            if attempt < settings.max_retries - 1:
                wait = 2 ** attempt
                logger.warning("HTTP 오류 %s, %d초 후 재시도", e.response.status_code, wait)
                time.sleep(wait)
            else:
                raise
        except httpx.RequestError as e:
            if attempt < settings.max_retries - 1:
                wait = 2 ** attempt
                logger.warning("요청 오류 %s, %d초 후 재시도", e, wait)
                time.sleep(wait)
            else:
                raise

        time.sleep(settings.request_delay)

    return {"status": "ERR", "message": "최대 재시도 초과"}


def fetch_financial_summary(
    corp_code: str,
    bsns_year: int,
    reprt_code: str,
    fs_div: str = "CFS",
) -> dict:
    """
    DART 단일회사 주요계정 재무제표 API 호출 (fnlttSinglAcnt).

    fnlttSinglAcntAll 대비 행수 적고 account_id 없으나, KOSDAQ 소형주 포함
    더 넓은 커버리지를 제공한다. acntall 폴백 경로에서 사용.

    Args:
        corp_code: 8자리 기업코드
        bsns_year: 사업연도
        reprt_code: 11013/11012/11014/11011
        fs_div: CFS / OFS

    Returns:
        DART API 응답 dict. status "000"이면 정상.
    """
    _check_api_key()

    params = {
        "crtfc_key": settings.dart_api_key,
        "corp_code": corp_code,
        "bsns_year": str(bsns_year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
    }

    for attempt in range(settings.max_retries):
        try:
            with _get_client() as client:
                resp = client.get(f"{DART_BASE}/fnlttSinglAcnt.json", params=params)
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as e:
            if attempt < settings.max_retries - 1:
                wait = 2 ** attempt
                logger.warning("HTTP 오류 %s, %d초 후 재시도", e.response.status_code, wait)
                time.sleep(wait)
            else:
                raise
        except httpx.RequestError as e:
            if attempt < settings.max_retries - 1:
                wait = 2 ** attempt
                logger.warning("요청 오류 %s, %d초 후 재시도", e, wait)
                time.sleep(wait)
            else:
                raise

        time.sleep(settings.request_delay)

    return {"status": "ERR", "message": "최대 재시도 초과"}


# ---------------------------------------------------------------------------
# XBRL 재무제표 다운로드
# ---------------------------------------------------------------------------

def fetch_xbrl_zip(rcept_no: str, reprt_code: str) -> bytes | None:
    """
    DART XBRL 재무제표 ZIP 파일 다운로드.
    실패 시 None 반환 (상위 레이어에서 fnlttSinglAcntAll 폴백).

    Args:
        rcept_no: 14자리 공시 접수번호
        reprt_code: 11013/11012/11014/11011

    Returns:
        ZIP bytes 또는 None
    """
    _check_api_key()
    params = {
        "crtfc_key": settings.dart_api_key,
        "rcept_no": rcept_no,
        "reprt_code": reprt_code,
    }
    try:
        with _get_client() as client:
            resp = client.get(
                f"{DART_BASE}/fnlttXbrlDs003.zip",
                params=params,
                timeout=60.0,
            )
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "zip" in content_type or "octet-stream" in content_type:
                logger.info("XBRL ZIP 다운로드 완료 [%s] %d bytes", rcept_no, len(resp.content))
                return resp.content
            # JSON 에러 응답인 경우
            try:
                err = resp.json()
                logger.warning("XBRL ZIP 오류 [%s]: %s", rcept_no, err.get("message", content_type))
            except Exception:
                logger.warning("XBRL ZIP 비정상 응답 [%s]: content-type=%s", rcept_no, content_type)
            return None
    except httpx.HTTPStatusError as e:
        logger.warning("XBRL ZIP HTTP %d [%s]", e.response.status_code, rcept_no)
        return None
    except Exception as e:
        logger.warning("XBRL ZIP 다운로드 실패 [%s]: %s", rcept_no, e)
        return None


# ---------------------------------------------------------------------------
# 사업보고서 document.xml 다운로드
# ---------------------------------------------------------------------------

def fetch_document_zip_files(rcept_no: str) -> dict[str, str]:
    """
    DART document.xml ZIP의 모든 XML 파일을 반환한다.
    Returns: {filename: content_str} — 메인 파일 포함 전체
    """
    _check_api_key()
    params = {"crtfc_key": settings.dart_api_key, "rcept_no": rcept_no}
    try:
        with _get_client() as client:
            resp = client.get(f"{DART_BASE}/document.xml", params=params, timeout=60.0)
            resp.raise_for_status()
    except Exception as e:
        logger.warning("document.xml ZIP 수집 실패 [%s]: %s", rcept_no, e)
        return {}

    result: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            for name in zf.namelist():
                if not name.endswith(".xml"):
                    continue
                with zf.open(name) as f:
                    raw = f.read()
                for enc in ("utf-8", "euc-kr", "cp949"):
                    try:
                        result[name] = raw.decode(enc)
                        break
                    except (UnicodeDecodeError, LookupError):
                        continue
                else:
                    result[name] = raw.decode("utf-8", errors="replace")
    except Exception as e:
        raw_xml = _raw_document_xml_from_response(resp.content, resp.encoding)
        if raw_xml is not None:
            return {f"{rcept_no}.xml": raw_xml}
        text = _decode_dart_text(resp.content, resp.encoding).strip()
        status, message = _dart_error_from_xml(text)
        if status:
            _raise_if_dart_limit(status, message)
            logger.warning("document.xml DART 오류 [%s]: status=%s message=%s", rcept_no, status, message or "")
            return {}
        logger.warning("document.xml ZIP 파싱 실패 [%s]: %s", rcept_no, e)
    return result


def fetch_document_xml(rcept_no: str) -> str | None:
    """
    DART document.xml API에서 사업보고서 원문 XML을 가져온다.
    ZIP을 풀어 rcept_no.xml 파일 내용을 UTF-8 문자열로 반환한다.
    실패 시 None 반환.
    """
    _check_api_key()
    params = {"crtfc_key": settings.dart_api_key, "rcept_no": rcept_no}
    try:
        with _get_client() as client:
            resp = client.get(f"{DART_BASE}/document.xml", params=params, timeout=60.0)
            resp.raise_for_status()
            text = _decode_dart_text(resp.content, resp.encoding).strip()
            status, message = _dart_error_from_xml(text)
            if status:
                _raise_if_dart_limit(status, message)
                logger.warning("document.xml DART 오류 [%s]: status=%s message=%s", rcept_no, status, message or "")
                return None
            content_type = resp.headers.get("content-type", "")
            _BINARY_TYPES = ("zip", "octet-stream", "x-msdownload", "application/")
            if not any(t in content_type for t in _BINARY_TYPES):
                raw_xml = _raw_document_xml_from_response(resp.content, resp.encoding)
                if raw_xml is not None:
                    return raw_xml
                logger.warning("document.xml 비정상 응답 [%s]: %s", rcept_no, content_type)
                return None
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = f"{rcept_no}.xml"
            if xml_name not in zf.namelist():
                xml_name = next((n for n in zf.namelist() if n.endswith(".xml")), None)
                if xml_name is None:
                    logger.warning("document.xml ZIP에 XML 없음 [%s]", rcept_no)
                    return None
            with zf.open(xml_name) as f:
                raw = f.read()
            for enc in ("utf-8", "euc-kr", "cp949"):
                try:
                    return raw.decode(enc)
                except (UnicodeDecodeError, LookupError):
                    continue
            return raw.decode("utf-8", errors="replace")
    except Exception as e:
        raw_xml = _raw_document_xml_from_response(resp.content, getattr(resp, "encoding", None)) if "resp" in locals() else None
        if raw_xml is not None:
            return raw_xml
        if "resp" in locals():
            text = _decode_dart_text(resp.content, getattr(resp, "encoding", None)).strip()
            status, message = _dart_error_from_xml(text)
            if status:
                _raise_if_dart_limit(status, message)
                logger.warning("document.xml DART 오류 [%s]: status=%s message=%s", rcept_no, status, message or "")
                return None
        logger.warning("document.xml 수집 실패 [%s]: %s", rcept_no, e)
        return None


def fetch_dart_main_html(rcept_no: str) -> str | None:
    """Fetch DART filing main page HTML, used to discover attached dcmNo values."""
    for attempt in range(1, 4):
        try:
            with _get_client() as client:
                resp = client.get(
                    f"{DART_WEB_BASE}/dsaf001/main.do",
                    params={"rcpNo": rcept_no},
                    timeout=30.0,
                )
                resp.raise_for_status()
                return _decode_dart_text(resp.content, resp.encoding)
        except Exception as e:
            if attempt == 3:
                logger.warning("DART main HTML 수집 실패 [%s]: %s", rcept_no, e)
                return None
            time.sleep(settings.request_delay * attempt)
    return None


def parse_attachment_options(main_html: str) -> list[dict[str, str]]:
    """Parse DART attachment <option> rows into rcept_no/dcm_no/title records."""
    options: list[dict[str, str]] = []
    for match in re.finditer(
        r"<option\b[^>]*\bvalue=[\"']([^\"']+)[\"'][^>]*>(.*?)</option>",
        main_html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        raw_value = html.unescape(match.group(1)).strip()
        title = re.sub(r"<[^>]+>", " ", match.group(2))
        title = html.unescape(re.sub(r"\s+", " ", title)).strip()
        if not raw_value or "dcmNo=" not in raw_value:
            continue
        params = parse_qs(raw_value, keep_blank_values=True)
        rcept_no = (params.get("rcpNo") or params.get("rcept_no") or [""])[0]
        dcm_no = (params.get("dcmNo") or [""])[0]
        if rcept_no and dcm_no:
            options.append({"rcept_no": rcept_no, "dcm_no": dcm_no, "title": title})
    return options


def parse_viewer_tree_nodes(main_html: str) -> list[dict[str, str]]:
    """Parse top-level DART viewer tree nodes from a filing main page."""
    nodes: list[dict[str, str]] = []
    for match in re.finditer(
        r"var\s+node1\s*=\s*\{\};(?P<body>.*?)(?=var\s+node1\s*=\s*\{\};|\Z)",
        main_html or "",
        flags=re.IGNORECASE | re.DOTALL,
    ):
        body = match.group("body")
        node: dict[str, str] = {}
        for key in ("text", "rcpNo", "dcmNo", "eleId", "offset", "length", "dtd"):
            field = re.search(
                rf"node1\[['\"]{key}['\"]\]\s*=\s*[\"'](.*?)[\"']",
                body,
                flags=re.IGNORECASE | re.DOTALL,
            )
            if field:
                value = html.unescape(field.group(1))
                node[key] = re.sub(r"\s+", " ", value).strip()
        if node.get("rcpNo") and node.get("dcmNo") and node.get("eleId"):
            nodes.append(node)
    return nodes


def fetch_viewer_html(
    rcept_no: str,
    dcm_no: str,
    *,
    ele_id: str = "0",
    offset: str = "0",
    length: str = "0",
    dtd: str = "HTML",
) -> str | None:
    """Fetch a DART attachment viewer body by rcpNo/dcmNo."""
    for attempt in range(1, 4):
        try:
            with _get_client() as client:
                resp = client.get(
                    f"{DART_WEB_BASE}/report/viewer.do",
                    params={
                        "rcpNo": rcept_no,
                        "dcmNo": dcm_no,
                        "eleId": ele_id,
                        "offset": offset,
                        "length": length,
                        "dtd": dtd,
                    },
                    timeout=60.0,
                )
                resp.raise_for_status()
                time.sleep(settings.request_delay)
                return _decode_dart_text(resp.content, resp.encoding)
        except Exception as e:
            if attempt == 3:
                logger.warning("DART viewer HTML 수집 실패 [%s/%s]: %s", rcept_no, dcm_no, e)
                return None
            time.sleep(settings.request_delay * attempt)
    return None


# ---------------------------------------------------------------------------
# 계열회사 목록 API
# ---------------------------------------------------------------------------

def fetch_affiliates(corp_code: str) -> list[dict]:
    """
    DART affcoInfo.json — 계열회사(종속·지분법 포함) 목록 조회.

    Returns:
        [{"corp_code": str, "corp_name": str, "stock_code": str|None,
          "corp_cls": str}, ...]
        실패 시 빈 리스트
    """
    _check_api_key()
    params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code}
    try:
        with _get_client() as client:
            resp = client.get(f"{DART_BASE}/affcoInfo.json", params=params)
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "000":
            logger.info("affcoInfo 데이터 없음 [%s]: %s", corp_code, data.get("message", ""))
            return []
        items = data.get("list", [])
        result = []
        for item in items:
            sc = (item.get("stock_code") or "").strip()
            result.append({
                "corp_code": item.get("corp_code", "").strip(),
                "corp_name": item.get("corp_name", "").strip(),
                "stock_code": sc if sc else None,
                "corp_cls": item.get("corp_cls", ""),
            })
        return result
    except Exception as e:
        logger.warning("affcoInfo 조회 실패 [%s]: %s", corp_code, e)
        return []


# ---------------------------------------------------------------------------
# 기업 상세 정보 API (corp_cls 포함)
# ---------------------------------------------------------------------------

_CORP_CLS_MAP = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}


def fetch_company_info(corp_code: str) -> dict | None:
    """
    DART 기업 상세 정보 API 호출.
    corp_cls: Y=KOSPI, K=KOSDAQ, N=KONEX, E=기타(비상장)

    Returns:
        {"corp_cls": str, "market": str|None} or None on failure
    """
    _check_api_key()
    params = {"crtfc_key": settings.dart_api_key, "corp_code": corp_code}
    try:
        with _get_client() as client:
            resp = client.get(f"{DART_BASE}/company.json", params=params)
            resp.raise_for_status()
            data = resp.json()
        if data.get("status") != "000":
            return None
        corp_cls = data.get("corp_cls", "")
        induty_code = (data.get("induty_code") or "").strip() or None
        return {
            "corp_cls": corp_cls,
            "market": _CORP_CLS_MAP.get(corp_cls),
            "induty_code": induty_code,
        }
    except Exception as e:
        logger.warning("company.json 조회 실패 [%s]: %s", corp_code, e)
        return None


# ---------------------------------------------------------------------------
# 공시 목록 API
# ---------------------------------------------------------------------------

def fetch_audit_fee(corp_code: str, bsns_year: int) -> dict:
    """
    DART 정기보고서 주요정보의 감사용역/비감사용역 API 호출.

    Args:
        corp_code: 8자리 기업코드
        bsns_year: 사업연도 (예: 2024)

    Returns:
        DART API 응답 dict. status "000"이면 정상.
        list는 adtServcCnclsSttus(감사용역), non_audit_list는
        accnutAdtorNonAdtServcCnclsSttus(비감사용역) 원문 list.
    """
    _check_api_key()
    params = {
        "crtfc_key": settings.dart_api_key,
        "corp_code": corp_code,
        "bsns_year": str(bsns_year),
        "reprt_code": "11011",  # 사업보고서
    }
    try:
        with _get_client() as client:
            audit_resp = client.get(f"{DART_BASE}/adtServcCnclsSttus.json", params=params)
            audit_resp.raise_for_status()
            audit_data = audit_resp.json()

            non_audit_resp = client.get(
                f"{DART_BASE}/accnutAdtorNonAdtServcCnclsSttus.json",
                params=params,
            )
            non_audit_resp.raise_for_status()
            non_audit_data = non_audit_resp.json()

            if audit_data.get("status") != "000":
                return audit_data

            if non_audit_data.get("status") == "000":
                audit_data["non_audit_list"] = non_audit_data.get("list", [])
            elif non_audit_data.get("status") == "013":
                audit_data["non_audit_list"] = []
            else:
                audit_data["non_audit_status"] = non_audit_data.get("status")
                audit_data["non_audit_message"] = non_audit_data.get("message")

            return audit_data
    except Exception as e:
        logger.warning("감사용역 API 조회 실패 [%s %s]: %s", corp_code, bsns_year, e)
        return {"status": "ERR", "message": str(e)}


def fetch_disclosure_list(
    corp_code: str | None,
    start_date: str,
    end_date: str,
    disc_type: str = "",
) -> list[dict]:
    """
    DART 공시 목록 API 호출 (페이징 자동 처리).

    Args:
        corp_code: 8자리 기업코드. None이면 기간 전체 공시 목록을 조회한다.
        start_date: 조회 시작일 (YYYYMMDD)
        end_date: 조회 종료일 (YYYYMMDD)
        disc_type: 공시유형 필터 (빈 문자열 = 전체)

    Returns:
        공시 목록 dict 리스트
    """
    _check_api_key()

    results = []
    page = 1
    page_count = 100  # DART 최대값

    while True:
        params = {
            "crtfc_key": settings.dart_api_key,
            "bgn_de": start_date,
            "end_de": end_date,
            "page_no": page,
            "page_count": page_count,
        }
        if corp_code:
            params["corp_code"] = corp_code
        if disc_type:
            params["pblntf_ty"] = disc_type

        with _get_client() as client:
            resp = client.get(f"{DART_BASE}/list.json", params=params)
            resp.raise_for_status()
            data = resp.json()

        status = data.get("status")
        if status == "013":
            break
        if status != "000":
            message = data.get("message") or "unknown error"
            raise RuntimeError(f"DART list.json status={status}: {message}")

        items = data.get("list", [])
        results.extend(items)

        total = int(data.get("total_count", 0))
        if len(results) >= total:
            break

        page += 1
        time.sleep(settings.request_delay)

    return results

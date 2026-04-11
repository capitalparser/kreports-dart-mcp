import asyncio
import logging
import time
import zipfile
import io
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from kreports.config import settings

logger = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"
CORP_CODE_ZIP_URL = f"{DART_BASE}/corpCode.xml"  # zip 반환

# 로컬 캐시 경로 (30일 유효)
_CACHE_DIR = Path(__file__).parent.parent.parent / ".cache"
_CORP_ZIP_CACHE = _CACHE_DIR / "corp_code.zip"
_CACHE_MAX_AGE_DAYS = 30


def _get_client() -> httpx.Client:
    return httpx.Client(timeout=30.0)


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
            content_type = resp.headers.get("content-type", "")
            _BINARY_TYPES = ("zip", "octet-stream", "x-msdownload", "application/")
            if not any(t in content_type for t in _BINARY_TYPES):
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
        logger.warning("document.xml 수집 실패 [%s]: %s", rcept_no, e)
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
    DART DS002 회계감사용역계약 체결현황 API 호출.

    Args:
        corp_code: 8자리 기업코드
        bsns_year: 사업연도 (예: 2024)

    Returns:
        DART API 응답 dict. status "000"이면 정상.
        주요 응답 필드(list 항목):
          adt_fee   - 감사보수 (백만원)
          adt_time  - 감사시간 (시간)
          nadt_fee  - 비감사보수 (백만원)
          nadt_time - 비감사시간 (시간)
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
            resp = client.get(f"{DART_BASE}/hmvAuditFee.json", params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("hmvAuditFee 조회 실패 [%s %s]: %s", corp_code, bsns_year, e)
        return {"status": "ERR", "message": str(e)}


def fetch_disclosure_list(
    corp_code: str,
    start_date: str,
    end_date: str,
    disc_type: str = "",
) -> list[dict]:
    """
    DART 공시 목록 API 호출 (페이징 자동 처리).

    Args:
        corp_code: 8자리 기업코드
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
            "corp_code": corp_code,
            "bgn_de": start_date,
            "end_de": end_date,
            "page_no": page,
            "page_count": page_count,
        }
        if disc_type:
            params["pblntf_ty"] = disc_type

        with _get_client() as client:
            resp = client.get(f"{DART_BASE}/list.json", params=params)
            resp.raise_for_status()
            data = resp.json()

        if data.get("status") != "000":
            break

        items = data.get("list", [])
        results.extend(items)

        total = int(data.get("total_count", 0))
        if len(results) >= total:
            break

        page += 1
        time.sleep(settings.request_delay)

    return results

"""User-keyed on-demand DART disclosure fetch and cache."""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, datetime

import httpx

from kreports.collector.fetcher import DART_BASE, _decode_dart_text
from kreports.collector.report_document_collector import _persist_source_document, _sha1
from kreports.db.engine import get_session
from kreports.db.models import Disclosure, SourceDocument
from kreports.storage.raw_documents import RawDocumentStore

logger = logging.getLogger(__name__)


def _clean_key(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _cached_source(rcept_no: str) -> SourceDocument | None:
    with get_session() as session:
        row = (
            session.query(SourceDocument)
            .filter_by(rcept_no=rcept_no, source_type="event_disclosure")
            .first()
        )
        if row is None:
            return None
        raw_content = row.raw_content or ""
        if not raw_content and row.storage_uri:
            raw_content = RawDocumentStore().read(row.storage_uri, expected_hash=row.doc_hash)
        return SourceDocument(
            rcept_no=row.rcept_no,
            dcm_no=row.dcm_no,
            corp_code=row.corp_code,
            bsns_year=row.bsns_year,
            source_type=row.source_type,
            report_nm=row.report_nm,
            content_type=row.content_type,
            raw_content=raw_content,
            doc_hash=row.doc_hash,
            storage_uri=row.storage_uri,
            content_length=row.content_length,
            compressed_length=row.compressed_length,
            storage_status=row.storage_status,
            fetched_at=row.fetched_at,
        )


def _disclosure_meta(rcept_no: str, *, corp_code: str | None = None, year: int | None = None) -> dict | None:
    with get_session() as session:
        row = session.query(Disclosure).filter_by(rcept_no=rcept_no).first()
        if row is None:
            if corp_code and year:
                return {
                    "rcept_no": rcept_no,
                    "corp_code": corp_code,
                    "bsns_year": int(year),
                    "source_type": "event_disclosure",
                    "report_nm": "on-demand disclosure",
                    "dcm_no": None,
                }
            return None
        disc_date = row.disc_date
        if isinstance(disc_date, str):
            bsns_year = int(disc_date[:4])
        elif isinstance(disc_date, date):
            bsns_year = disc_date.year
        else:
            bsns_year = int(year or datetime.utcnow().year)
        return {
            "rcept_no": row.rcept_no,
            "corp_code": row.corp_code,
            "bsns_year": int(year or bsns_year),
            "source_type": "event_disclosure",
            "report_nm": row.report_nm,
            "dcm_no": None,
        }


def _fetch_document_xml_with_user_key(rcept_no: str, user_dart_api_key: str) -> str | None:
    with httpx.Client(timeout=60.0) as client:
        resp = client.get(
            f"{DART_BASE}/document.xml",
            params={"crtfc_key": user_dart_api_key, "rcept_no": rcept_no},
            timeout=60.0,
        )
        resp.raise_for_status()

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = f"{rcept_no}.xml"
            if xml_name not in zf.namelist():
                xml_name = next((name for name in zf.namelist() if name.endswith(".xml")), None)
            if xml_name is None:
                return None
            with zf.open(xml_name) as fh:
                raw = fh.read()
        return _decode_dart_text(raw)
    except zipfile.BadZipFile:
        return _decode_dart_text(resp.content)


def fetch_disclosure_on_demand(
    *,
    rcept_no: str,
    user_dart_api_key: str | None = None,
    cache_policy: str = "cache_first",
    corp_code: str | None = None,
    year: int | None = None,
) -> dict:
    """Fetch one disclosure using the caller's DART key and cache the source body."""
    rcept_no = (rcept_no or "").strip()
    if not rcept_no:
        return {"error": "rcept_no is required"}

    if cache_policy not in {"cache_first", "refresh"}:
        return {"error": "cache_policy must be cache_first or refresh"}

    cached = _cached_source(rcept_no)
    if cached is not None and cache_policy == "cache_first":
        return {
            "rcept_no": rcept_no,
            "corp_code": cached.corp_code,
            "bsns_year": cached.bsns_year,
            "report_nm": cached.report_nm,
            "cached": True,
            "body_length": len(cached.raw_content or ""),
            "doc_hash": cached.doc_hash,
            "data_quality": {
                "status": "usable",
                "source": "source_documents_cache",
            },
        }

    clean_key = _clean_key(user_dart_api_key)
    if not clean_key:
        return {
            "error": "user_dart_api_key is required",
            "answer": (
                "판정: fail\n\n"
                "온디맨드 수시공시 조회에는 사용자 DART API key가 필요합니다. "
                "공개 MCP 서버의 DART_API_KEY는 사용하지 않습니다.\n\n"
                "데이터 한계:\n- key는 요청 처리에만 사용되어야 하며 저장되면 안 됩니다."
            ),
        }

    meta = _disclosure_meta(rcept_no, corp_code=corp_code, year=year)
    if meta is None:
        return {
            "error": "disclosure metadata not found",
            "answer": (
                "판정: fail\n\n"
                "로컬 disclosures 테이블에서 해당 접수번호의 회사/연도 메타데이터를 찾지 못했습니다. "
                "요청에 corp_code와 year를 함께 제공해야 캐시할 수 있습니다."
            ),
        }

    content = _fetch_document_xml_with_user_key(rcept_no, clean_key)
    if not content:
        return {
            "error": "document.xml empty",
            "rcept_no": rcept_no,
            "data_quality": {"status": "missing", "source": "user_keyed_dart_fetch"},
        }

    _persist_source_document(meta, content=content)
    return {
        "rcept_no": rcept_no,
        "corp_code": meta["corp_code"],
        "bsns_year": meta["bsns_year"],
        "report_nm": meta["report_nm"],
        "cached": False,
        "body_length": len(content),
        "doc_hash": _sha1(content),
        "data_quality": {
            "status": "usable",
            "source": "user_keyed_dart_fetch",
        },
    }

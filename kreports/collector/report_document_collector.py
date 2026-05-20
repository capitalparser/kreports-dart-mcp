"""Persist business/audit report body sections from DART source documents."""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy import text

from kreports.collector.fetcher import (
    fetch_dart_main_html,
    fetch_document_zip_files,
    fetch_document_xml,
    fetch_viewer_html,
    parse_attachment_options,
)
from kreports.db.engine import get_session
from kreports.db.models import (
    Auditor,
    BusinessAffiliateAuditor,
    Company,
    Disclosure,
    ExtractionRun,
    FetchLog,
    ReportDocument,
    ReportSection,
    SourceDocument,
)
from kreports.processor.audit_parser import parse_auditor_from_doc_xml, parse_bsns_year
from kreports.processor.audit_report_parser import extract_audit_report_sections
from kreports.processor.report_section_parser import extract_report_sections
from kreports.processor.subsidiary_parser import extract_affiliates_from_report

logger = logging.getLogger(__name__)


def _sha1(value: str) -> str:
    return hashlib.sha1((value or "").encode("utf-8")).hexdigest()


def _source_type(report_nm: str) -> str | None:
    if "사업보고서" in (report_nm or ""):
        return "business_report"
    if "감사보고서" in (report_nm or ""):
        return "audit_report"
    return None


def _is_primary_audit_attachment(title: str) -> bool:
    title = title or ""
    if "감사보고서" not in title:
        return False
    if any(skip in title for skip in ("내부회계", "감사의감사보고서", "감사의 감사보고서", "내부감시장치")):
        return False
    return True


def collect_report_sections_for_disclosure(rcept_no: str) -> dict:
    """Fetch one report document and persist useful normalized sections."""
    with get_session() as session:
        disc = session.query(Disclosure).filter_by(rcept_no=rcept_no).first()
        if disc is None:
            return {"ok": 0, "sections": 0, "error": "disclosure not found"}
        source_type = _source_type(disc.report_nm)
        if source_type is None:
            return {"ok": 0, "sections": 0, "error": "unsupported report type"}
        bsns_year = parse_bsns_year(disc.report_nm, disc.disc_date.strftime("%Y%m%d"))
        if bsns_year is None:
            return {"ok": 0, "sections": 0, "error": "business year unresolved"}
        meta = {
            "rcept_no": disc.rcept_no,
            "corp_code": disc.corp_code,
            "bsns_year": bsns_year,
            "source_type": source_type,
            "report_nm": disc.report_nm,
        }

    if source_type == "audit_report" and "감사보고서제출" in meta["report_nm"]:
        return _collect_attached_audit_reports(meta)

    if source_type == "business_report":
        zipped = _collect_business_report_zip(meta)
        if zipped.get("ok"):
            return zipped

    content = fetch_document_xml(rcept_no)
    if not content:
        _log_fetch(meta["corp_code"], source_type, bsns_year, "error", "document.xml empty")
        return {"ok": 0, "sections": 0, "error": "document.xml empty"}

    # Listed-company business reports often embed the independent auditor's
    # report/KAM summary inside the annual filing. Standalone audit-report
    # filings use the same heading language, so one parser covers both sources.
    result = _persist_report_document(meta, content=content)
    attached = {"documents": 0, "sections": 0, "errors": []}
    if source_type == "business_report":
        attached = _collect_attached_audit_reports(meta, log_fetch=False)
    _log_fetch(meta["corp_code"], source_type, bsns_year, "success", None)
    return {
        "ok": 1,
        "documents": 1 + int(attached.get("documents") or 0),
        "sections": result["sections"] + int(attached.get("sections") or 0),
        "business_report_sections": result["sections"],
        "audit_report_sections": int(attached.get("sections") or 0),
        "error": None,
        "errors": attached.get("errors", []),
        **meta,
    }


def _document_title(content: str) -> str:
    for pattern in (
        r"<DOCUMENT-NAME\b[^>]*>(.*?)</DOCUMENT-NAME>",
        r"<TITLE\b[^>]*>(.*?)</TITLE>",
    ):
        match = re.search(pattern, content or "", flags=re.IGNORECASE | re.DOTALL)
        if match:
            title = re.sub(r"<[^>]+>", " ", match.group(1))
            return re.sub(r"\s+", " ", title).strip()
    return ""


def _collect_business_report_zip(meta: dict) -> dict:
    """Collect business-report main XML and attached audit reports from document.xml ZIP."""
    files = fetch_document_zip_files(meta["rcept_no"])
    if not files:
        return {"ok": 0, "documents": 0, "sections": 0, "error": "document.xml ZIP empty", **meta}

    main_name = f"{meta['rcept_no']}.xml"
    main_content = files.get(main_name) or next(iter(files.values()))
    main_result = _persist_report_document(meta, content=main_content)
    affiliate_count = int(main_result.get("affiliate_auditors") or 0)

    audit_documents = []
    for name, content in files.items():
        if content == main_content:
            continue
        title = _document_title(content) or name
        if not _is_primary_audit_attachment(title):
            continue
        audit_documents.append((name, title, content))

    totals = {"documents": 1, "sections": int(main_result["sections"]), "audit_report_sections": 0}
    for ordinal, (name, title, content) in enumerate(audit_documents, 1):
        doc_key = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_") or f"zip{ordinal}"
        stored_rcept_no = f"{meta['rcept_no']}_{doc_key}"[:80]
        doc_meta = {
            **meta,
            "rcept_no": stored_rcept_no,
            "dcm_no": doc_key[:20],
            "source_type": "audit_report",
            "report_nm": title,
        }
        result = _persist_report_document(doc_meta, content=content)
        totals["documents"] += 1
        totals["sections"] += int(result["sections"])
        totals["audit_report_sections"] += int(result["sections"])

    if not audit_documents:
        attached = _collect_attached_audit_reports(meta, log_fetch=False)
        totals["documents"] += int(attached.get("documents") or 0)
        totals["sections"] += int(attached.get("sections") or 0)
        totals["audit_report_sections"] += int(attached.get("sections") or 0)

    _log_fetch(meta["corp_code"], "business_report", meta["bsns_year"], "success", None)
    return {
        "ok": 1,
        "documents": totals["documents"],
        "sections": totals["sections"],
        "business_report_sections": int(main_result["sections"]),
        "audit_report_sections": totals["audit_report_sections"],
        "affiliate_auditors": affiliate_count,
        "error": None,
        **meta,
    }


def collect_attached_audit_reports_for_disclosure(rcept_no: str) -> dict:
    """Collect only attached audit-report viewer bodies for a DART filing."""
    with get_session() as session:
        disc = session.query(Disclosure).filter_by(rcept_no=rcept_no).first()
        if disc is None:
            return {"ok": 0, "documents": 0, "sections": 0, "error": "disclosure not found"}
        source_type = _source_type(disc.report_nm)
        if source_type not in {"business_report", "audit_report"}:
            return {"ok": 0, "documents": 0, "sections": 0, "error": "unsupported report type"}
        bsns_year = parse_bsns_year(disc.report_nm, disc.disc_date.strftime("%Y%m%d"))
        if bsns_year is None:
            return {"ok": 0, "documents": 0, "sections": 0, "error": "business year unresolved"}
        meta = {
            "rcept_no": disc.rcept_no,
            "corp_code": disc.corp_code,
            "bsns_year": bsns_year,
            "source_type": source_type,
            "report_nm": disc.report_nm,
        }
    return _collect_attached_audit_reports(meta)


def _collect_attached_audit_reports(meta: dict, *, log_fetch: bool = True) -> dict:
    """Collect detailed audit reports attached to a DART filing page."""
    main_html = fetch_dart_main_html(meta["rcept_no"])
    if not main_html:
        if log_fetch:
            _log_fetch(meta["corp_code"], "audit_report", meta["bsns_year"], "error", "DART main HTML empty")
        return {"ok": 0, "documents": 0, "sections": 0, "error": "DART main HTML empty", **meta}

    attachments = []
    seen_dcm_nos: set[str] = set()
    for option in parse_attachment_options(main_html):
        dcm_no = option.get("dcm_no", "")
        if dcm_no in seen_dcm_nos or not _is_primary_audit_attachment(option.get("title", "")):
            continue
        seen_dcm_nos.add(dcm_no)
        attachments.append(option)
    if not attachments:
        if log_fetch:
            _log_fetch(meta["corp_code"], "audit_report", meta["bsns_year"], "error", "audit report attachment not found")
        return {"ok": 0, "documents": 0, "sections": 0, "error": "audit report attachment not found", **meta}

    totals = {"documents": 0, "sections": 0, "errors": []}
    for attachment in attachments:
        dcm_no = attachment["dcm_no"]
        content = fetch_viewer_html(meta["rcept_no"], dcm_no)
        if not content:
            totals["errors"].append({"dcm_no": dcm_no, "error": "viewer HTML empty"})
            continue
        stored_rcept_no = f"{meta['rcept_no']}_{dcm_no}"
        doc_meta = {
            **meta,
            "rcept_no": stored_rcept_no,
            "dcm_no": dcm_no,
            "source_type": "audit_report",
            "report_nm": attachment.get("title") or meta["report_nm"],
        }
        result = _persist_report_document(doc_meta, content=content)
        totals["documents"] += 1
        totals["sections"] += int(result["sections"])

    ok = 1 if totals["documents"] else 0
    status = "success" if ok else "error"
    error_msg = None if ok else "all audit report attachments failed"
    if log_fetch:
        _log_fetch(meta["corp_code"], "audit_report", meta["bsns_year"], status, error_msg)
    return {
        "ok": ok,
        "documents": totals["documents"],
        "sections": totals["sections"],
        "error": error_msg,
        "errors": totals["errors"][:20],
        **meta,
    }


def _persist_report_document(meta: dict, *, content: str) -> dict:
    """Persist one normalized report document and its extracted sections."""
    source_doc_id = _persist_source_document(meta, content=content)
    extraction = extract_document_features_from_content(meta, content=content)
    _log_extraction_run(
        source_document_id=source_doc_id,
        meta=meta,
        extractor_name="document_features",
        source_doc_hash=_sha1(content),
        status="success",
        rows_written=int(extraction.get("rows_written") or 0),
        error_msg=None,
    )
    return {
        "sections": int(extraction.get("sections") or 0),
        "auditors": int(extraction.get("auditors") or 0),
        "affiliate_auditors": int(extraction.get("affiliate_auditors") or 0),
    }


def _persist_source_document(meta: dict, *, content: str) -> int:
    """Persist raw source text so extractors can be rerun without DART calls."""
    now = datetime.utcnow()
    doc_hash = _sha1(content)
    with get_session() as session:
        stmt = sqlite_insert(SourceDocument).values({
            **meta,
            "content_type": "xml",
            "raw_content": content,
            "doc_hash": doc_hash,
            "fetched_at": now,
        })
        stmt = stmt.on_conflict_do_update(
            index_elements=["rcept_no", "source_type"],
            set_={
                "corp_code": stmt.excluded.corp_code,
                "dcm_no": stmt.excluded.dcm_no,
                "bsns_year": stmt.excluded.bsns_year,
                "report_nm": stmt.excluded.report_nm,
                "content_type": stmt.excluded.content_type,
                "raw_content": stmt.excluded.raw_content,
                "doc_hash": stmt.excluded.doc_hash,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        session.execute(stmt)
        source_doc_id = session.execute(
            text(
                "SELECT id FROM source_documents "
                "WHERE rcept_no=:rcept_no AND source_type=:source_type"
            ),
            {"rcept_no": meta["rcept_no"], "source_type": meta["source_type"]},
        ).scalar_one()
    return int(source_doc_id)


def extract_document_features_from_content(meta: dict, *, content: str) -> dict:
    """Run normalized extractors from a cached source document body."""
    sections = extract_audit_report_sections(content)
    if meta.get("source_type") == "business_report":
        sections = {**sections, **extract_report_sections(content)}

    now = datetime.utcnow()
    with get_session() as session:
        stmt = sqlite_insert(ReportDocument).values({
            **meta,
            "doc_hash": _sha1(content),
            "fetched_at": now,
        })
        stmt = stmt.on_conflict_do_update(
            index_elements=["rcept_no", "source_type"],
            set_={
                "corp_code": stmt.excluded.corp_code,
                "dcm_no": stmt.excluded.dcm_no,
                "bsns_year": stmt.excluded.bsns_year,
                "report_nm": stmt.excluded.report_nm,
                "doc_hash": stmt.excluded.doc_hash,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        session.execute(stmt)

        rows = []
        for ordinal, (section_key, section) in enumerate(sections.items()):
            body = (section.get("body_text") or "").strip()
            if not body:
                continue
            rows.append({
                "rcept_no": meta["rcept_no"],
                "dcm_no": meta.get("dcm_no"),
                "corp_code": meta["corp_code"],
                "bsns_year": meta["bsns_year"],
                "source_type": meta["source_type"],
                "section_key": section_key,
                "section_title": (section.get("title") or "")[:300] or None,
                "body_text": body,
                "body_hash": _sha1(body),
                "body_length": len(body),
                "ordinal": ordinal,
                "fetched_at": now,
            })
        session.execute(
            text(
                "DELETE FROM report_sections "
                "WHERE rcept_no=:rcept_no AND source_type=:source_type"
            ),
            {"rcept_no": meta["rcept_no"], "source_type": meta["source_type"]},
        )
        if rows:
            sec_stmt = sqlite_insert(ReportSection).values(rows)
            sec_stmt = sec_stmt.on_conflict_do_update(
                index_elements=["rcept_no", "source_type", "section_key", "ordinal"],
                set_={
                    "corp_code": sec_stmt.excluded.corp_code,
                    "dcm_no": sec_stmt.excluded.dcm_no,
                    "bsns_year": sec_stmt.excluded.bsns_year,
                    "section_title": sec_stmt.excluded.section_title,
                    "body_text": sec_stmt.excluded.body_text,
                    "body_hash": sec_stmt.excluded.body_hash,
                    "body_length": sec_stmt.excluded.body_length,
                    "fetched_at": sec_stmt.excluded.fetched_at,
                },
            )
            session.execute(sec_stmt)

    auditor_count = 0
    affiliate_count = 0
    if meta.get("source_type") == "business_report":
        auditor_count = _persist_auditors_from_business_report(meta, content=content)
        affiliate_count = _persist_business_affiliate_auditors(meta, content=content)["count"]

    return {
        "sections": len(rows),
        "auditors": auditor_count,
        "affiliate_auditors": affiliate_count,
        "rows_written": len(rows) + auditor_count + affiliate_count,
    }


def _persist_auditors_from_business_report(meta: dict, *, content: str) -> int:
    """Persist auditor/opinion rows parsed from a cached business report."""
    if meta.get("source_type") != "business_report":
        return 0
    records = parse_auditor_from_doc_xml(content)
    if not records:
        return 0
    now = datetime.utcnow()
    rows = [
        {
            "corp_code": meta["corp_code"],
            "bsns_year": meta["bsns_year"],
            "fs_div": rec["fs_div"],
            "auditor_nm": rec["auditor_nm"],
            "audit_opinion": rec.get("audit_opinion"),
            "rcept_no": meta["rcept_no"],
            "fetched_at": now,
        }
        for rec in records
        if rec.get("auditor_nm")
    ]
    if not rows:
        return 0
    with get_session() as session:
        stmt = sqlite_insert(Auditor).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["corp_code", "bsns_year", "fs_div"],
            set_={
                "auditor_nm": stmt.excluded.auditor_nm,
                "audit_opinion": stmt.excluded.audit_opinion,
                "rcept_no": stmt.excluded.rcept_no,
                "fetched_at": stmt.excluded.fetched_at,
            },
        )
        session.execute(stmt)
    return len(rows)


def _log_extraction_run(
    *,
    source_document_id: int | None,
    meta: dict,
    extractor_name: str,
    source_doc_hash: str | None,
    status: str,
    rows_written: int = 0,
    error_msg: str | None = None,
) -> None:
    with get_session() as session:
        session.add(ExtractionRun(
            source_document_id=source_document_id,
            rcept_no=meta["rcept_no"],
            source_type=meta["source_type"],
            extractor_name=extractor_name,
            extractor_version="v1",
            source_doc_hash=source_doc_hash,
            status=status,
            rows_written=rows_written,
            error_msg=error_msg,
            extracted_at=datetime.utcnow(),
        ))


def _persist_business_affiliate_auditors(meta: dict, *, content: str) -> dict:
    """Persist subsidiary/equity affiliate auditor matrix from a business report body."""
    if meta.get("source_type") != "business_report":
        return {"count": 0}

    affiliates = extract_affiliates_from_report(content)
    if not affiliates:
        with get_session() as session:
            session.execute(
                text("DELETE FROM subsidiary_auditor_matrix WHERE parent_rcept_no=:rcept_no"),
                {"rcept_no": meta["rcept_no"]},
            )
        return {"count": 0}

    names = [item["name"] for item in affiliates if item.get("name")]
    name_to_company = _match_companies_by_names_local(names)
    matched_codes = [
        info["corp_code"]
        for info in name_to_company.values()
        if info.get("corp_code")
    ]

    auditor_by_corp: dict[str, dict] = {}
    if matched_codes:
        with get_session() as session:
            auditors = (
                session.query(Auditor)
                .filter(Auditor.corp_code.in_(matched_codes))
                .filter(Auditor.bsns_year <= meta["bsns_year"])
                .order_by(
                    Auditor.corp_code,
                    Auditor.bsns_year.desc(),
                    Auditor.fs_div.asc(),
                )
                .all()
            )
            auditors = [
                {
                    "corp_code": auditor.corp_code,
                    "auditor_nm": auditor.auditor_nm,
                    "audit_opinion": auditor.audit_opinion,
                    "fs_div": auditor.fs_div,
                    "bsns_year": auditor.bsns_year,
                }
                for auditor in auditors
            ]
        for auditor in auditors:
            if auditor["corp_code"] in auditor_by_corp:
                continue
            auditor_by_corp[auditor["corp_code"]] = {
                "auditor_nm": auditor["auditor_nm"],
                "audit_opinion": auditor["audit_opinion"],
                "auditor_fs_div": auditor["fs_div"],
                "auditor_year": auditor["bsns_year"],
            }

    now = datetime.utcnow()
    rows = []
    for ordinal, item in enumerate(affiliates):
        name = item.get("name")
        if not name:
            continue
        company = name_to_company.get(name) or {}
        corp_code = company.get("corp_code")
        auditor = auditor_by_corp.get(corp_code or "", {})
        rows.append({
            "parent_corp_code": meta["corp_code"],
            "parent_rcept_no": meta["rcept_no"],
            "bsns_year": meta["bsns_year"],
            "name": name,
            "relation": item.get("relation"),
            "ownership_pct": item.get("ownership_pct"),
            "listed_yn": item.get("listed_yn"),
            "business": item.get("business"),
            "assets": item.get("assets"),
            "source": item.get("source"),
            "corp_code": corp_code,
            "stock_code": company.get("stock_code"),
            "market": company.get("market"),
            "auditor_nm": auditor.get("auditor_nm"),
            "audit_opinion": auditor.get("audit_opinion"),
            "auditor_fs_div": auditor.get("auditor_fs_div"),
            "auditor_year": auditor.get("auditor_year"),
            "ordinal": ordinal,
            "fetched_at": now,
        })

    with get_session() as session:
        session.execute(
            text("DELETE FROM subsidiary_auditor_matrix WHERE parent_rcept_no=:rcept_no"),
            {"rcept_no": meta["rcept_no"]},
        )
        if rows:
            stmt = sqlite_insert(BusinessAffiliateAuditor).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["parent_rcept_no", "name"],
                set_={
                    "parent_corp_code": stmt.excluded.parent_corp_code,
                    "bsns_year": stmt.excluded.bsns_year,
                    "relation": stmt.excluded.relation,
                    "ownership_pct": stmt.excluded.ownership_pct,
                    "listed_yn": stmt.excluded.listed_yn,
                    "business": stmt.excluded.business,
                    "assets": stmt.excluded.assets,
                    "source": stmt.excluded.source,
                    "corp_code": stmt.excluded.corp_code,
                    "stock_code": stmt.excluded.stock_code,
                    "market": stmt.excluded.market,
                    "auditor_nm": stmt.excluded.auditor_nm,
                    "audit_opinion": stmt.excluded.audit_opinion,
                    "auditor_fs_div": stmt.excluded.auditor_fs_div,
                    "auditor_year": stmt.excluded.auditor_year,
                    "ordinal": stmt.excluded.ordinal,
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            session.execute(stmt)

    return {"count": len(rows)}


def _normalize_company_name(value: str) -> str:
    return re.sub(r"[\s\(\)\[\]㈜주식회사,.·-]+", "", value or "").lower()


def _match_companies_by_names_local(names: list[str]) -> dict[str, dict]:
    """Match affiliate names against the local companies table only."""
    if not names:
        return {}
    with get_session() as session:
        companies = [
            {
                "corp_code": company.corp_code,
                "corp_name": company.corp_name,
                "stock_code": company.stock_code,
                "market": company.market,
            }
            for company in session.query(Company).all()
        ]
    by_norm = {
        _normalize_company_name(company["corp_name"]): company
        for company in companies
    }
    result: dict[str, dict] = {}
    for name in names:
        norm = _normalize_company_name(name)
        if norm in by_norm:
            result[name] = by_norm[norm]
            continue
        if len(norm) < 3:
            continue
        for candidate_norm, info in by_norm.items():
            if norm in candidate_norm or candidate_norm in norm:
                result[name] = info
                break
    return result


def collect_audit_report_sections(
    *,
    year: int,
    market: str | None = None,
    limit: int | None = None,
    missing_only: bool = True,
    progress_callback=None,
) -> dict:
    """Collect audit report sections for listed companies."""
    from sqlalchemy import text

    stmt = """
        SELECT d.rcept_no, d.corp_code, c.corp_name
        FROM disclosures d
        JOIN companies c ON c.corp_code=d.corp_code
        WHERE c.stock_code IS NOT NULL
          AND d.report_nm LIKE '%감사보고서%'
          AND d.report_nm NOT LIKE '%제출 지연%'
          AND d.report_nm NOT LIKE '%제출지연%'
          AND d.disc_date BETWEEN :start_date AND :end_date
    """
    params: dict[str, object] = {
        "start_date": f"{year + 1}-01-01",
        "end_date": f"{year + 1}-12-31",
    }
    if market:
        stmt += " AND c.market=:market"
        params["market"] = market
    if missing_only:
        stmt += """
          AND NOT EXISTS (
            SELECT 1 FROM report_sections rs
            WHERE (rs.rcept_no=d.rcept_no OR rs.rcept_no LIKE d.rcept_no || '_%')
              AND rs.source_type='audit_report'
          )
        """
    stmt += " ORDER BY c.market, c.corp_name, d.disc_date DESC"
    if limit:
        stmt += " LIMIT :limit"
        params["limit"] = int(limit)

    with get_session() as session:
        targets = session.execute(text(stmt), params).all()

    totals = {"total": len(targets), "ok": 0, "failed": 0, "sections": 0, "errors": []}
    for idx, (rcept_no, _corp_code, corp_name) in enumerate(targets, 1):
        if progress_callback:
            progress_callback(idx, totals["total"], corp_name, rcept_no)
        result = collect_report_sections_for_disclosure(rcept_no)
        if result.get("ok"):
            totals["ok"] += 1
            totals["sections"] += int(result.get("sections") or 0)
        else:
            totals["failed"] += 1
            if len(totals["errors"]) < 20:
                totals["errors"].append({"rcept_no": rcept_no, "corp_name": corp_name, "error": result.get("error")})
    return totals


def collect_business_report_sections(
    *,
    year: int,
    market: str | None = None,
    limit: int | None = None,
    missing_only: bool = True,
    progress_callback=None,
) -> dict:
    """Collect embedded audit/KAM sections from business reports."""
    from sqlalchemy import text

    stmt = """
        SELECT d.rcept_no, d.corp_code, c.corp_name
        FROM disclosures d
        JOIN companies c ON c.corp_code=d.corp_code
        WHERE c.stock_code IS NOT NULL
          AND d.report_nm LIKE '%사업보고서%'
          AND d.report_nm NOT LIKE '%제출기한연장%'
          AND d.report_nm NOT LIKE '%해외증권%'
          AND d.disc_date BETWEEN :start_date AND :end_date
          AND NOT EXISTS (
            SELECT 1 FROM disclosures d2
            WHERE d2.corp_code=d.corp_code
              AND d2.report_nm LIKE '%사업보고서%'
              AND d2.report_nm NOT LIKE '%제출기한연장%'
              AND d2.report_nm NOT LIKE '%해외증권%'
              AND d2.disc_date BETWEEN :start_date AND :end_date
              AND d2.disc_date > d.disc_date
          )
    """
    params: dict[str, object] = {
        "start_date": f"{year + 1}-01-01",
        "end_date": f"{year + 1}-12-31",
    }
    if market:
        stmt += " AND c.market=:market"
        params["market"] = market
    if missing_only:
        stmt += """
          AND (
            NOT EXISTS (
            SELECT 1 FROM report_sections rs
            WHERE rs.rcept_no=d.rcept_no
              AND rs.source_type='business_report'
            )
            OR NOT EXISTS (
              SELECT 1 FROM report_sections rs
              WHERE rs.rcept_no=d.rcept_no
                AND rs.source_type='business_report'
                AND rs.section_key IN ('business_overview', 'business_description')
            )
            OR NOT EXISTS (
              SELECT 1 FROM subsidiary_auditor_matrix sam
              WHERE sam.parent_rcept_no=d.rcept_no
            )
            OR NOT EXISTS (
              SELECT 1 FROM report_sections rs
              WHERE (rs.rcept_no=d.rcept_no OR rs.rcept_no LIKE d.rcept_no || '_%')
                AND rs.source_type='audit_report'
                AND rs.section_key='kam'
            )
          )
        """
    stmt += " ORDER BY c.market, c.corp_name, d.disc_date DESC"
    if limit:
        stmt += " LIMIT :limit"
        params["limit"] = int(limit)

    with get_session() as session:
        targets = session.execute(text(stmt), params).all()

    totals = {"total": len(targets), "ok": 0, "failed": 0, "sections": 0, "errors": []}
    for idx, (rcept_no, _corp_code, corp_name) in enumerate(targets, 1):
        if progress_callback:
            progress_callback(idx, totals["total"], corp_name, rcept_no)
        result = collect_report_sections_for_disclosure(rcept_no)
        if result.get("ok"):
            totals["ok"] += 1
            totals["sections"] += int(result.get("sections") or 0)
        else:
            totals["failed"] += 1
            if len(totals["errors"]) < 20:
                totals["errors"].append({"rcept_no": rcept_no, "corp_name": corp_name, "error": result.get("error")})
    return totals


def run_document_extractors(
    *,
    year: int | None = None,
    source_type: str | None = None,
    limit: int | None = None,
    extractor: str = "all",
    progress_callback=None,
) -> dict:
    """Rerun extractors from cached raw source_documents without DART calls."""
    if source_type and source_type not in {"business_report", "audit_report"}:
        return {"total": 0, "ok": 0, "failed": 0, "rows_written": 0, "errors": [{"error": "invalid source_type"}]}
    if extractor not in {"all", "sections", "auditors", "subsidiaries"}:
        return {"total": 0, "ok": 0, "failed": 0, "rows_written": 0, "errors": [{"error": "invalid extractor"}]}

    stmt = """
        SELECT id, rcept_no, dcm_no, corp_code, bsns_year, source_type, report_nm, raw_content, doc_hash
        FROM source_documents
        WHERE 1=1
    """
    params: dict[str, object] = {}
    if year is not None:
        stmt += " AND bsns_year=:year"
        params["year"] = year
    if source_type:
        stmt += " AND source_type=:source_type"
        params["source_type"] = source_type
    stmt += " ORDER BY bsns_year, source_type, rcept_no"
    if limit:
        stmt += " LIMIT :limit"
        params["limit"] = int(limit)

    with get_session() as session:
        rows = session.execute(text(stmt), params).all()

    totals = {"total": len(rows), "ok": 0, "failed": 0, "rows_written": 0, "errors": []}
    for idx, row in enumerate(rows, 1):
        source_document_id, rcept_no, dcm_no, corp_code, bsns_year, src_type, report_nm, content, doc_hash = row
        if progress_callback:
            progress_callback(idx, totals["total"], corp_code, bsns_year, src_type, rcept_no)
        meta = {
            "rcept_no": rcept_no,
            "dcm_no": dcm_no,
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "source_type": src_type,
            "report_nm": report_nm,
        }
        try:
            if extractor == "sections":
                before = extract_document_features_from_content(meta, content=content)
                rows_written = int(before.get("sections") or 0)
                extractor_name = "sections"
            elif extractor == "auditors":
                rows_written = _persist_auditors_from_business_report(meta, content=content)
                extractor_name = "auditors"
            elif extractor == "subsidiaries":
                rows_written = _persist_business_affiliate_auditors(meta, content=content)["count"]
                extractor_name = "subsidiaries"
            else:
                extracted = extract_document_features_from_content(meta, content=content)
                rows_written = int(extracted.get("rows_written") or 0)
                extractor_name = "document_features"
            _log_extraction_run(
                source_document_id=source_document_id,
                meta=meta,
                extractor_name=extractor_name,
                source_doc_hash=doc_hash,
                status="success",
                rows_written=rows_written,
                error_msg=None,
            )
            totals["ok"] += 1
            totals["rows_written"] += rows_written
        except Exception as exc:
            _log_extraction_run(
                source_document_id=source_document_id,
                meta=meta,
                extractor_name=extractor,
                source_doc_hash=doc_hash,
                status="error",
                rows_written=0,
                error_msg=str(exc)[:4000],
            )
            totals["failed"] += 1
            if len(totals["errors"]) < 20:
                totals["errors"].append({"rcept_no": rcept_no, "error": str(exc)})
    return totals


def _log_fetch(corp_code: str, source_type: str, year: int, status: str, error_msg: str | None) -> None:
    with get_session() as session:
        session.add(FetchLog(
            task_type=f"{source_type}_section"[:20],
            corp_code=corp_code,
            year=year,
            status=status,
            error_msg=error_msg,
            fetched_at=datetime.utcnow(),
        ))

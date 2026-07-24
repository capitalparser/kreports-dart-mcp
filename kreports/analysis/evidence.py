"""Evidence and citation helpers for source-grounded MCP answers."""
from __future__ import annotations

import re
from typing import Any

DART_FILING_URL_PREFIX = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
_PLAIN_RCEPT_RE = re.compile(r"^\d{14}$")


def parent_rcept_no(rcept_no: str | None) -> str | None:
    """Return the original DART filing receipt number from plain or synthetic ids."""
    value = str(rcept_no or "").strip()
    if _PLAIN_RCEPT_RE.match(value):
        return value
    match = re.search(r"(\d{14})", value)
    return match.group(1) if match else None


def dart_filing_url(rcept_no: str | None) -> str | None:
    """Build a DART filing URL for a receipt number or synthetic attachment id."""
    parent = parent_rcept_no(rcept_no)
    return f"{DART_FILING_URL_PREFIX}{parent}" if parent else None


def evidence_reference_fields(source: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a legacy fact source into public, renderer-safe evidence fields."""
    raw_rcept_no = source.get("parent_rcept_no") or source.get("rcept_no")
    rcept_no = parent_rcept_no(str(raw_rcept_no or ""))
    source_url = dart_filing_url(raw_rcept_no)
    if not source_url:
        return None

    label_parts = [
        source.get("corp_name") or source.get("corp_code"),
        source.get("report_nm"),
    ]
    label = " ".join(str(part).strip() for part in label_parts if part).strip() or "DART 공시"
    return {
        "source_label": label,
        "source_url": source_url,
        "rcept_no": rcept_no,
        "section_title": (
            str(source.get("section_title") or source.get("section_key"))
            if source.get("section_title") or source.get("section_key")
            else None
        ),
    }


def source_line(source: dict[str, Any]) -> str:
    """Render a compact Korean source line for a confirmed fact."""
    corp_name = source.get("corp_name") or source.get("corp_code") or "대상 회사"
    report_nm = source.get("report_nm") or source.get("source_table") or "공시자료"
    section = source.get("section_title") or source.get("section_key")
    raw_rcept_no = source.get("rcept_no")
    rcept_no = source.get("parent_rcept_no") or parent_rcept_no(raw_rcept_no)

    first_line = f"출처: {corp_name} {report_nm}"
    if section:
        first_line += f", {section}"
    if rcept_no:
        first_line += f", 접수번호 {rcept_no}"

    lines = [first_line]
    url = dart_filing_url(rcept_no or raw_rcept_no)
    if url:
        lines.append(f"공시 링크: {url}")
    if raw_rcept_no and raw_rcept_no != rcept_no:
        lines.append(f"첨부문서 식별자: {raw_rcept_no}")
    return "\n".join(lines)

"""Evidence and citation helpers for source-grounded MCP answers."""
from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import unquote, urlsplit

DART_FILING_URL_PREFIX = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="
_PLAIN_RCEPT_RE = re.compile(r"^\d{14}$")
_PUBLIC_DNS_HOST_RE = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$",
    re.IGNORECASE,
)


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


def _legacy_ipv4_address(host: str) -> ipaddress.IPv4Address | None:
    """Parse legacy numeric IPv4 spellings without DNS resolution.

    Browsers and URL stacks may accept one-to-four-part decimal, octal, or
    hexadecimal forms (for example ``127.1`` and ``0x7f000001``).  Recognize
    those forms deterministically so they are subject to the same public-IP
    check as ordinary dotted-quad addresses.
    """
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or any(not part for part in parts):
        return None

    values: list[int] = []
    for part in parts:
        base = 10
        digits = part
        if part.lower().startswith("0x"):
            base, digits = 16, part[2:]
            valid = "0123456789abcdefABCDEF"
        elif len(part) > 1 and part.startswith("0"):
            base, valid = 8, "01234567"
        else:
            valid = "0123456789"
        if not digits or any(char not in valid for char in digits):
            return None
        values.append(int(digits, base))

    limits = {1: (0xFFFFFFFF,), 2: (0xFF, 0xFFFFFF), 3: (0xFF, 0xFF, 0xFFFF), 4: (0xFF,) * 4}
    if any(value > limit for value, limit in zip(values, limits[len(values)])):
        return None
    if len(values) == 1:
        numeric = values[0]
    elif len(values) == 2:
        numeric = (values[0] << 24) | values[1]
    elif len(values) == 3:
        numeric = (values[0] << 24) | (values[1] << 16) | values[2]
    else:
        numeric = sum(value << (8 * (3 - index)) for index, value in enumerate(values))
    return ipaddress.IPv4Address(numeric)


def _canonical_public_host(hostname: str) -> str | None:
    """Return a safe public-looking hostname after deterministic canonicalization."""
    if "%" in hostname or "\\" in hostname or any(ord(char) < 32 or ord(char) == 127 for char in hostname):
        return None
    host = unquote(hostname).rstrip(".").lower()
    if not host or "%" in host or "\\" in host or any(ord(char) < 32 or ord(char) == 127 for char in host):
        return None
    if host == "localhost" or host.endswith(".localhost"):
        return None

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = _legacy_ipv4_address(host)
    if address is not None:
        return host if address.is_global else None

    if not host.isascii() or not _PUBLIC_DNS_HOST_RE.fullmatch(host):
        return None
    if host.endswith((".local", ".internal", ".lan", ".home", ".test", ".invalid")):
        return None
    return host


def _public_http_url(value: str) -> str | None:
    """Return an explicitly supplied public HTTP(S) URL, or ``None``.

    Evidence links are rendered into user-facing responses.  A source URL is
    therefore data, not a browser instruction: reject non-web schemes,
    relative/protocol-relative values, credentials, and obvious local/private
    destinations before it reaches a renderer.
    """
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        # Accessing port validates malformed/out-of-range port values.
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc or not hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None

    return value if _canonical_public_host(hostname) else None


def evidence_reference_fields(source: dict[str, Any]) -> dict[str, Any] | None:
    """Normalize a legacy fact source into public, renderer-safe evidence fields."""
    raw_rcept_no = source.get("parent_rcept_no") or source.get("rcept_no")
    rcept_no = parent_rcept_no(str(raw_rcept_no or ""))
    # Some established facts legitimately rely on a non-DART public source.
    # Preserve its explicit URL instead of treating it as an uncitable fact.
    explicit_url = _public_http_url(str(source.get("source_url") or source.get("url") or "").strip())
    source_url = dart_filing_url(raw_rcept_no) or explicit_url
    if not source_url:
        return None

    label_parts = [
        source.get("corp_name") or source.get("corp_code"),
        source.get("report_nm"),
    ]
    label = " ".join(str(part).strip() for part in label_parts if part).strip()
    label = label or str(source.get("source_label") or "").strip() or "공개 출처"
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

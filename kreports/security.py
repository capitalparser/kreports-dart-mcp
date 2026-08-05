"""Small, dependency-light security boundaries shared by collectors."""
from __future__ import annotations

import re
from urllib.parse import unquote

import httpx


_SENSITIVE_FIELD_RE = re.compile(
    r"(?i)\b(?:crtfc_key|dart_api_key|api[_-]?key|access[_-]?token|"
    r"authorization|bearer|password|secret|token)\b\s*(?:=|:)"
)
_BEARER_TOKEN_RE = re.compile(r"(?i)\bbearer\b\s+\S+")
_URL_USERINFO_RE = re.compile(r"(?i)https?://[^\s/@:]*:[^\s/@]+@")
_URL_QUERY_RE = re.compile(r"https?://\S+\?")


def contains_sensitive_text(value: object) -> bool:
    """Return whether text contains a credential-bearing external diagnostic."""
    text = str(value)
    decoded = unquote(text)
    return any(
        pattern.search(candidate)
        for candidate in (text, decoded)
        for pattern in (
            _SENSITIVE_FIELD_RE,
            _BEARER_TOKEN_RE,
            _URL_USERINFO_RE,
            _URL_QUERY_RE,
        )
    )


def redact_sensitive_text(value: object) -> str:
    """Keep ordinary upstream status text but drop credential-bearing details."""
    text = str(value)
    if contains_sensitive_text(text):
        return "external error details redacted"
    return text


def redact_external_error(exc: Exception) -> str:
    """Describe an external failure without retaining URLs or credentials."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"external HTTP request failed (status={exc.response.status_code})"
    if isinstance(exc, httpx.RequestError):
        return "external HTTP request failed"
    return f"external request failed ({type(exc).__name__})"

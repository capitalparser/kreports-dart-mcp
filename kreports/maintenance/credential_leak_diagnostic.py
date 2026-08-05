"""Read-only inspection for historical credential-bearing error text."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kreports.maintenance.investor_core_backfill_plan import _open_readonly_database


MAX_RENDERED_FINDINGS = 200
_CANDIDATE_COLUMNS = {
    "fetch_log": ("error_msg",),
    "audit_fee_observations": ("source_message", "limitations_json"),
    "audit_fees": ("source_observations_json",),
}
_CREDENTIAL_RE = re.compile(
    r"(?i)(?:[?&;\"']\s*(?:crtfc_key|dart_api_key|api[_-]?key|"
    r"access[_-]?token|token|secret|password)\s*(?:=|:)|"
    r"\b(?:authorization|bearer)\b)"
)


def _existing_columns(connection: Any, table: str) -> set[str]:
    rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {str(row["name"]) for row in rows}


def diagnose_credential_leaks(db_path: str | Path) -> dict[str, object]:
    """Return bounded finding locations without exposing matched values.

    The SQLite connection is opened through the project's immutable/checkpointed
    read-only boundary. The report intentionally contains no excerpts or hashes
    of the matched text, so it is safe to attach to an incident ticket.
    """
    findings: list[dict[str, object]] = []
    total = 0
    with _open_readonly_database(db_path) as connection:
        table_names = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for table, candidate_columns in _CANDIDATE_COLUMNS.items():
            if table not in table_names:
                continue
            columns = _existing_columns(connection, table)
            for column in candidate_columns:
                if column not in columns:
                    continue
                rows = connection.execute(
                    f'SELECT rowid, "{column}" AS value FROM "{table}" '
                    f'WHERE "{column}" IS NOT NULL'
                )
                for row in rows:
                    if _CREDENTIAL_RE.search(str(row["value"])) is None:
                        continue
                    total += 1
                    if len(findings) < MAX_RENDERED_FINDINGS:
                        findings.append(
                            {
                                "table": table,
                                "column": column,
                                "rowid": int(row["rowid"]),
                            }
                        )
    return {
        "schema": "credential_leak_diagnostic_v1",
        "finding_count": total,
        "findings": findings,
        "findings_omitted_count": total - len(findings),
    }

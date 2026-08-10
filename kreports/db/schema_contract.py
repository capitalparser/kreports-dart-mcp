"""One fail-closed SQLite schema contract for runtime release and rehearsal."""
from __future__ import annotations

import sqlite3
import re


REQUIRED_TABLES = (
    "companies",
    "company_listing_periods",
    "company_year_listing_memberships",
    "disclosures",
    "financials",
    "financial_facts_compact",
    "audit_fees",
    "audit_fee_observations",
    "group_entities",
    "group_relationships",
    "group_component_metrics",
    "report_sections",
    "evidence_documents",
    "kam_items",
    "audit_procedure_items",
    "backfill_runs",
    "company_year_quality",
    "schema_migrations",
    "dataset_manifest",
    "source_documents",
    "accounting_policy_items",
    "accounting_note_chapters",
)

REQUIRED_COLUMN_SPECS = {
    "company_listing_periods": (
        "corp_code", "stock_code", "market", "listed_from", "listed_to",
        "status", "as_of", "raw_source_uri", "raw_source_checksum",
        "raw_source_retrieved_at", "raw_source_storage_uri",
        "raw_source_size_bytes", "normalized_checksum",
        "normalized_storage_uri", "normalized_size_bytes",
        "transformation_version", "source_type", "source_row_no",
    ),
    "company_year_listing_memberships": (
        "corp_code", "stock_code", "bsns_year", "market", "status",
        "evidence_basis", "as_of", "manifest_checksum",
        "manifest_storage_uri", "manifest_size_bytes",
        "manifest_raw_receipt_count", "normalized_checksum",
        "normalized_storage_uri", "normalized_size_bytes",
        "transformation_version", "source_row_no",
    ),
    "audit_fees": (
        "contract_fee_m", "contract_hours", "actual_fee_m", "actual_hours",
        "source_class", "source_rcept_no", "source_period",
        "availability_status", "quality_status", "compatibility_basis",
        "conflict_status", "source_observations_json",
    ),
    "financial_facts_compact": (
        "corp_code", "bsns_year", "fs_div", "metric_key", "amount",
        "source_account_id", "source_table", "unit", "period_type",
        "citation_rcept_no", "citation_report_nm", "citation_basis",
        "quality_status",
    ),
    "kam_items": (
        "id", "rcept_no", "dcm_no", "corp_code", "bsns_year",
        "source_type", "ordinal", "title", "normalized_topic",
        "reason_text", "audit_response_text", "related_note_references_json",
        "full_body_hash", "full_body_length", "source_basis",
        "parser_version", "quality_status",
    ),
    "audit_procedure_items": (
        "id", "rcept_no", "dcm_no", "corp_code", "bsns_year",
        "source_type", "kam_item_id", "kam_topic", "method",
        "procedure_type", "procedure_text", "procedure_hash",
        "procedure_length", "assertion_hints_json", "linked_metric_keys_json",
        "linked_note_keys_json", "linked_event_keys_json", "parser_version",
        "quality_status", "section_ordinal", "procedure_ordinal",
    ),
    "accounting_note_chapters": (
        "id", "corp_code", "bsns_year", "fs_div", "rcept_no", "dcm_no",
        "source_type", "note_no", "note_title", "section_type", "body",
        "body_hash", "body_length", "full_text_uri", "full_text_hash",
        "full_text_length", "full_text_compressed_length",
        "full_text_storage_status", "fetched_at",
    ),
    "accounting_policy_items": (
        "id", "corp_code", "bsns_year", "fs_div", "rcept_no", "item_key",
        "heading", "body", "body_hash", "body_length", "fetched_at",
    ),
}

# (table, ordered columns, unique, normalized partial predicate)
REQUIRED_INDEX_SPECS = {
    "idx_listing_period_corp_as_of": (
        "company_listing_periods", ("corp_code", "as_of"), False, None
    ),
    "uq_listing_period_normalized_row": (
        "company_listing_periods", ("normalized_checksum", "source_row_no"), True, None
    ),
    "idx_company_year_listing_membership_corp_year": (
        "company_year_listing_memberships", ("corp_code", "bsns_year"), False, None
    ),
    "idx_company_year_listing_membership_year_market": (
        "company_year_listing_memberships", ("bsns_year", "market", "status"), False, None
    ),
    "uq_company_year_listing_membership_company_year": (
        "company_year_listing_memberships", ("corp_code", "bsns_year"), True, None
    ),
    "uq_company_year_listing_membership_normalized_row": (
        "company_year_listing_memberships", ("normalized_checksum", "source_row_no"), True, None
    ),
    "idx_disclosure_corp_date_receipt": (
        "disclosures", ("corp_code", "disc_date", "rcept_no"), False, None
    ),
    "idx_company_year_quality_year_market": (
        "company_year_quality", ("bsns_year", "market"), False, None
    ),
    "uq_backfill_runs_active_lease": (
        "backfill_runs", ("lease_key",), True, "where status = 'running'"
    ),
    "idx_kam_item_corp_year": ("kam_items", ("corp_code", "bsns_year"), False, None),
    "idx_kam_item_quality_year": ("kam_items", ("bsns_year", "quality_status"), False, None),
    "idx_kam_item_receipt": ("kam_items", ("rcept_no", "source_type"), False, None),
    "idx_audit_procedure_kam_item": ("audit_procedure_items", ("kam_item_id",), False, None),
    "idx_audit_procedure_method_year": ("audit_procedure_items", ("method", "bsns_year"), False, None),
    "idx_audit_fee_availability_year": ("audit_fees", ("bsns_year", "availability_status"), False, None),
    "idx_audit_fee_observation_corp_year": ("audit_fee_observations", ("corp_code", "bsns_year"), False, None),
    "idx_audit_fee_observation_receipt": ("audit_fee_observations", ("source_rcept_no",), False, None),
    "idx_audit_fee_observation_year_quality": ("audit_fee_observations", ("bsns_year", "quality_status"), False, None),
    "uq_audit_fee_observation_current_slot": ("audit_fee_observations", ("source_slot_hash",), True, "where is_current = 1"),
    "idx_group_entity_parent_year": ("group_entities", ("parent_corp_code", "effective_year"), False, None),
    "idx_group_entity_resolved_year": ("group_entities", ("resolved_corp_code", "effective_year"), False, None),
    "idx_group_relationship_parent_year": ("group_relationships", ("parent_corp_code", "effective_year"), False, None),
    "idx_group_relationship_nodes": ("group_relationships", ("parent_entity_key", "child_entity_key"), False, None),
    "idx_group_metric_parent_year": ("group_component_metrics", ("parent_corp_code", "effective_year"), False, None),
    "idx_group_metric_entity_kind": ("group_component_metrics", ("entity_key", "metric_key"), False, None),
    "idx_group_metric_qsc_year": ("group_component_metrics", ("effective_year", "qsc_status"), False, None),
    "uq_accounting_note_chapter_identity": (
        "accounting_note_chapters",
        ("corp_code", "bsns_year", "fs_div", "note_no", "section_type"),
        True,
        None,
    ),
    "idx_note_chapter_corp_year": (
        "accounting_note_chapters", ("corp_code", "bsns_year", "fs_div"), False, None
    ),
    "idx_note_chapter_section_type": (
        "accounting_note_chapters", ("section_type",), False, None
    ),
    "idx_note_chapter_full_text_uri": (
        "accounting_note_chapters", ("full_text_uri",), False, None
    ),
    "uq_policy_item": (
        "accounting_policy_items", ("corp_code", "bsns_year", "fs_div", "item_key"), True, None
    ),
    "idx_policy_item_corp_year": (
        "accounting_policy_items", ("corp_code", "bsns_year"), False, None
    ),
    "idx_policy_item_key": (
        "accounting_policy_items", ("item_key",), False, None
    ),
}
REQUIRED_INDEXES = tuple(REQUIRED_INDEX_SPECS)


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


def _normalized_partial_predicate(sql: str) -> str | None:
    sql = sql.strip()
    match = re.search(r"\bwhere\b\s*(.+)$", sql, flags=re.IGNORECASE)
    if match is None:
        return None
    return "where " + " ".join(match.group(1).lower().split())


def index_contract_blockers(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> list[str]:
    """Return named missing/invalid index blockers using SQLite's actual DDL."""
    blockers: list[str] = []
    for name, (table, columns, unique, where) in REQUIRED_INDEX_SPECS.items():
        if table not in table_names:
            blockers.append(f"missing_required_index:{name}")
            continue
        rows = {
            str(row["name"]): row
            for row in connection.execute(f'PRAGMA index_list("{table}")')
        }
        row = rows.get(name)
        if row is None:
            blockers.append(f"missing_required_index:{name}")
            continue
        actual_columns = tuple(
            str(item["name"])
            for item in connection.execute(f'PRAGMA index_info("{name}")')
        )
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_schema WHERE type='index' AND name=?", (name,)
        ).fetchone()
        predicate = _normalized_partial_predicate(str(sql_row[0] or "")) if sql_row else None
        if (
            actual_columns != columns
            or bool(row["unique"]) is not unique
            or predicate != where
        ):
            blockers.append(f"invalid_required_index:{name}")
    return blockers


def column_contract_blockers(
    connection: sqlite3.Connection,
    table_names: set[str],
) -> list[str]:
    blockers: list[str] = []
    for table_name, required_columns in REQUIRED_COLUMN_SPECS.items():
        if table_name not in table_names:
            continue
        actual_columns = _table_columns(connection, table_name)
        blockers.extend(
            f"missing_required_column:{table_name}.{column}"
            for column in required_columns
            if column not in actual_columns
        )
    return blockers


def schema_contract_blockers(connection: sqlite3.Connection) -> list[str]:
    """Return release-equivalent table, column, and exact-index blockers."""
    table_names = {
        str(row["name"])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )
    }
    blockers = [
        f"missing_required_table:{name}"
        for name in REQUIRED_TABLES
        if name not in table_names
    ]
    blockers.extend(column_contract_blockers(connection, table_names))
    blockers.extend(index_contract_blockers(connection, table_names))
    return sorted(set(blockers))

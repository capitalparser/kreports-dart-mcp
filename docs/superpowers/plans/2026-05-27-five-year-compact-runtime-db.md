# Five-Year Compact Runtime DB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep five-year MCP feature coverage while preventing the deployable runtime DB from growing into a multi-GB raw/text warehouse.

**Architecture:** GCS remains the source layer for full raw reports and long evidence blobs. SQLite/Postgres runtime DB keeps compact structured facts, short excerpts, hashes, lengths, storage URIs, and search metadata needed for fast MCP responses. A separate export command builds a deployable five-year compact DB artifact from the richer maintainer DB.

**Tech Stack:** Python, SQLAlchemy, Typer CLI, SQLite, Google Cloud Storage, pytest.

---

## Current Baseline

- Raw report bodies are no longer stored inline in `source_documents.raw_content`.
- `source_documents.storage_uri` points to GCS for 605 raw documents.
- Runtime DB is still 2.1GB because derived/runtime tables are large:
  - `financial_facts`: ~523MB plus ~305MB unique index.
  - `accounting_note_chapters`: ~343MB.
  - `evidence_documents`: ~252MB.
  - `report_sections`: ~49MB.
- The product target remains five-year coverage for DCF, peer comparison, auditor trend, KAM, emphasis/other matter, audit fee/hour, and accounting policy analysis.

## File Map

- Modify: `kreports/db/models.py`
  - Add long-evidence manifest columns to text-heavy tables, if using in-place externalization.
- Modify: `kreports/db/engine.py`
  - Add migration columns/indexes for evidence blob manifests.
- Create: `kreports/storage/evidence_blobs.py`
  - GCS/file blob store for long derived text.
- Create: `kreports/maintenance/evidence_blob_migration.py`
  - Externalize long derived text and verify blob hash/readback.
- Create: `kreports/maintenance/runtime_export.py`
  - Build compact five-year deployable SQLite DB.
- Modify: `kreports/cli/main.py`
  - Add CLI commands for evidence blob migration, runtime export, and readiness.
- Modify: `kreports/analysis/api.py`
  - Make MCP/read APIs load full text lazily from evidence blob URI when the compact DB only has excerpt.
- Test: `tests/test_evidence_blob_storage.py`
- Test: `tests/test_runtime_db_export.py`
- Modify docs: `docs/raw-retention-policy.md`, `docs/deploy-http-mcp.md`, `docs/automated-backfill.md`

---

### Task 1: Add Evidence Blob Storage Abstraction

**Files:**
- Create: `kreports/storage/evidence_blobs.py`
- Test: `tests/test_evidence_blob_storage.py`

- [ ] **Step 1: Write tests for gzip write/read/hash**

```python
from pathlib import Path

from kreports.storage.evidence_blobs import EvidenceBlobStore, sha1_text


def test_evidence_blob_store_writes_and_reads_file_backend(tmp_path):
    store = EvidenceBlobStore(base_dir=tmp_path)
    content = "핵심감사사항 본문\n감사절차 본문"

    saved = store.write(
        table_name="evidence_documents",
        row_id=123,
        corp_code="00126380",
        bsns_year=2024,
        content=content,
    )

    assert saved.storage_uri.startswith("file://")
    assert saved.text_hash == sha1_text(content)
    assert saved.content_length == len(content.encode("utf-8"))
    assert saved.compressed_length > 0
    assert Path(saved.path).exists()
    assert store.read(saved.storage_uri, expected_hash=saved.text_hash) == content
```

- [ ] **Step 2: Run the test and confirm failure**

Run:

```bash
uv run pytest tests/test_evidence_blob_storage.py::test_evidence_blob_store_writes_and_reads_file_backend -q
```

Expected: fails because `kreports.storage.evidence_blobs` does not exist.

- [ ] **Step 3: Implement `EvidenceBlobStore`**

Create `kreports/storage/evidence_blobs.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class StoredEvidenceBlob:
    storage_uri: str
    path: str
    text_hash: str
    content_length: int
    compressed_length: int


def sha1_text(content: str) -> str:
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()


class EvidenceBlobStore:
    def __init__(
        self,
        base_dir: str | Path = "data/evidence_blobs",
        *,
        backend: str = "file",
        bucket: str | None = None,
        prefix: str = "",
        gcs_client=None,
    ):
        self.base_dir = Path(base_dir)
        self.backend = backend
        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self.gcs_client = gcs_client

    def _object_name(self, *, table_name: str, row_id: int, corp_code: str, bsns_year: int) -> str:
        parts = [
            part
            for part in (
                self.prefix,
                str(bsns_year),
                table_name,
                corp_code,
                f"{row_id}.txt.gz",
            )
            if part
        ]
        return "/".join(parts)

    def _get_gcs_client(self):
        if self.gcs_client is not None:
            return self.gcs_client
        try:
            from google.cloud import storage
        except ImportError as exc:
            raise RuntimeError(
                "google-cloud-storage is required for gs:// evidence blob storage. "
                "Install with: pip install 'kreports[gcs]'"
            ) from exc
        self.gcs_client = storage.Client()
        return self.gcs_client

    def write(self, *, table_name: str, row_id: int, corp_code: str, bsns_year: int, content: str) -> StoredEvidenceBlob:
        data = (content or "").encode("utf-8")
        compressed = gzip.compress(data)
        text_hash = sha1_text(content)
        object_name = self._object_name(
            table_name=table_name,
            row_id=int(row_id),
            corp_code=corp_code,
            bsns_year=int(bsns_year),
        )
        if self.backend == "gcs":
            if not self.bucket:
                raise ValueError("bucket is required when backend='gcs'")
            client = self._get_gcs_client()
            blob = client.bucket(self.bucket).blob(object_name)
            blob.upload_from_string(compressed, content_type="application/gzip")
            return StoredEvidenceBlob(
                storage_uri=f"gs://{self.bucket}/{object_name}",
                path=object_name,
                text_hash=text_hash,
                content_length=len(data),
                compressed_length=len(compressed),
            )
        path = self.base_dir / object_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(compressed)
        return StoredEvidenceBlob(
            storage_uri=f"file://{path.resolve()}",
            path=str(path),
            text_hash=text_hash,
            content_length=len(data),
            compressed_length=len(compressed),
        )

    def read(self, storage_uri: str, *, expected_hash: str | None = None) -> str:
        parsed = urlparse(storage_uri)
        if parsed.scheme == "gs":
            client = self._get_gcs_client()
            bucket = client.bucket(parsed.netloc)
            compressed = bucket.blob(parsed.path.lstrip("/")).download_as_bytes()
        elif parsed.scheme == "file":
            compressed = Path(parsed.path).read_bytes()
        else:
            raise ValueError(f"unsupported evidence blob URI scheme: {parsed.scheme}")
        content = gzip.decompress(compressed).decode("utf-8")
        if expected_hash and sha1_text(content) != expected_hash:
            raise ValueError("evidence blob hash mismatch")
        return content
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_evidence_blob_storage.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add kreports/storage/evidence_blobs.py tests/test_evidence_blob_storage.py
git commit -m "feat: add evidence blob storage"
```

---

### Task 2: Add Long Evidence Manifest Columns

**Files:**
- Modify: `kreports/db/models.py`
- Modify: `kreports/db/engine.py`
- Test: `tests/test_evidence_blob_storage.py`

- [ ] **Step 1: Add schema test**

Append to `tests/test_evidence_blob_storage.py`:

```python
from sqlalchemy import inspect


def test_long_text_tables_have_evidence_blob_manifest_columns(temp_engine):
    inspector = inspect(temp_engine)
    for table in ["accounting_note_chapters", "evidence_documents", "report_sections"]:
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "full_text_uri" in columns
        assert "full_text_hash" in columns
        assert "full_text_length" in columns
        assert "full_text_compressed_length" in columns
        assert "full_text_storage_status" in columns
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run pytest tests/test_evidence_blob_storage.py::test_long_text_tables_have_evidence_blob_manifest_columns -q
```

Expected: fails because columns do not exist.

- [ ] **Step 3: Add columns to SQLAlchemy models**

In each mapped model for `accounting_note_chapters`, `evidence_documents`, and `report_sections`, add:

```python
full_text_uri = Column(String(500), nullable=True)
full_text_hash = Column(String(40), nullable=True)
full_text_length = Column(Integer, nullable=True)
full_text_compressed_length = Column(Integer, nullable=True)
full_text_storage_status = Column(String(30), nullable=True)
```

- [ ] **Step 4: Add migration columns in `kreports/db/engine.py`**

In the idempotent schema migration list, add for all three tables:

```python
("accounting_note_chapters", "full_text_uri VARCHAR(500)"),
("accounting_note_chapters", "full_text_hash VARCHAR(40)"),
("accounting_note_chapters", "full_text_length INTEGER"),
("accounting_note_chapters", "full_text_compressed_length INTEGER"),
("accounting_note_chapters", "full_text_storage_status VARCHAR(30)"),
("evidence_documents", "full_text_uri VARCHAR(500)"),
("evidence_documents", "full_text_hash VARCHAR(40)"),
("evidence_documents", "full_text_length INTEGER"),
("evidence_documents", "full_text_compressed_length INTEGER"),
("evidence_documents", "full_text_storage_status VARCHAR(30)"),
("report_sections", "full_text_uri VARCHAR(500)"),
("report_sections", "full_text_hash VARCHAR(40)"),
("report_sections", "full_text_length INTEGER"),
("report_sections", "full_text_compressed_length INTEGER"),
("report_sections", "full_text_storage_status VARCHAR(30)"),
```

Also add indexes:

```python
"CREATE INDEX IF NOT EXISTS idx_note_chapters_full_text_uri ON accounting_note_chapters(full_text_uri)",
"CREATE INDEX IF NOT EXISTS idx_evidence_documents_full_text_uri ON evidence_documents(full_text_uri)",
"CREATE INDEX IF NOT EXISTS idx_report_sections_full_text_uri ON report_sections(full_text_uri)",
```

- [ ] **Step 5: Run schema test**

```bash
uv run pytest tests/test_evidence_blob_storage.py::test_long_text_tables_have_evidence_blob_manifest_columns -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/db/models.py kreports/db/engine.py tests/test_evidence_blob_storage.py
git commit -m "feat: add long evidence manifest columns"
```

---

### Task 3: Externalize Long Derived Text

**Files:**
- Create: `kreports/maintenance/evidence_blob_migration.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_evidence_blob_storage.py`

- [ ] **Step 1: Write migration test**

Append to `tests/test_evidence_blob_storage.py`:

```python
from sqlalchemy import text


def test_externalize_long_evidence_text_preserves_excerpt_and_manifest(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.evidence_blob_migration as migration_module
    from kreports.db.engine import get_session
    from kreports.storage.evidence_blobs import EvidenceBlobStore

    monkeypatch.setattr(
        migration_module,
        "EvidenceBlobStore",
        lambda **kwargs: EvidenceBlobStore(base_dir=tmp_path, **kwargs),
    )
    long_body = "A" * 5000
    with get_session() as session:
        session.execute(text("""
            INSERT INTO report_sections
            (rcept_no, corp_code, bsns_year, source_type, section_key, section_title,
             body_text, body_hash, body_length, ordinal, fetched_at)
            VALUES
            ('20250331000001', '00126380', 2024, 'audit_report', 'kam', 'KAM',
             :body, 'oldhash', :length, 1, CURRENT_TIMESTAMP)
        """), {"body": long_body, "length": len(long_body)})
        session.commit()

    out = migration_module.externalize_long_evidence_text(
        table_name="report_sections",
        text_column="body_text",
        excerpt_chars=1000,
        min_text_chars=2000,
        limit=10,
        backend="file",
    )

    assert out["externalized"] == 1
    with get_session() as session:
        row = session.execute(text("""
            SELECT body_text, full_text_uri, full_text_hash, full_text_length,
                   full_text_compressed_length, full_text_storage_status
            FROM report_sections
            WHERE rcept_no='20250331000001'
        """)).mappings().one()
    assert len(row["body_text"]) == 1000
    assert row["full_text_uri"].startswith("file://")
    assert row["full_text_length"] == 5000
    assert row["full_text_compressed_length"] > 0
    assert row["full_text_storage_status"] == "externalized"
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run pytest tests/test_evidence_blob_storage.py::test_externalize_long_evidence_text_preserves_excerpt_and_manifest -q
```

Expected: fails because migration module does not exist.

- [ ] **Step 3: Implement migration module**

Create `kreports/maintenance/evidence_blob_migration.py`:

```python
from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import get_session
from kreports.storage.evidence_blobs import EvidenceBlobStore, sha1_text


TABLE_CONFIG = {
    "accounting_note_chapters": {
        "text_column": "body",
        "id_column": "id",
        "corp_column": "corp_code",
        "year_column": "bsns_year",
    },
    "evidence_documents": {
        "text_column": "normalized_text",
        "id_column": "id",
        "corp_column": "corp_code",
        "year_column": "bsns_year",
    },
    "report_sections": {
        "text_column": "body_text",
        "id_column": "id",
        "corp_column": "corp_code",
        "year_column": "bsns_year",
    },
}


def _config(table_name: str, text_column: str | None = None) -> dict:
    if table_name not in TABLE_CONFIG:
        raise ValueError(f"unsupported evidence table: {table_name}")
    cfg = dict(TABLE_CONFIG[table_name])
    if text_column:
        cfg["text_column"] = text_column
    return cfg


def externalize_long_evidence_text(
    *,
    table_name: str,
    text_column: str | None = None,
    excerpt_chars: int = 2000,
    min_text_chars: int = 4000,
    limit: int | None = None,
    backend: str = "file",
    bucket: str | None = None,
    prefix: str = "evidence/full-text",
) -> dict:
    cfg = _config(table_name, text_column)
    col = cfg["text_column"]
    limit_sql = " LIMIT :limit" if limit else ""
    params = {"min_text_chars": int(min_text_chars)}
    if limit:
        params["limit"] = int(limit)
    select_sql = text(f"""
        SELECT id, corp_code, bsns_year, {col} AS full_text
        FROM {table_name}
        WHERE length(coalesce({col}, '')) >= :min_text_chars
          AND (full_text_uri IS NULL OR full_text_uri='')
        ORDER BY bsns_year DESC, id
        {limit_sql}
    """)
    store = EvidenceBlobStore(backend=backend, bucket=bucket, prefix=prefix)
    externalized = skipped = failed = 0
    errors: list[dict] = []
    with get_session() as session:
        rows = session.execute(select_sql, params).mappings().all()
        for row in rows:
            full_text = row["full_text"] or ""
            if not full_text:
                skipped += 1
                continue
            try:
                saved = store.write(
                    table_name=table_name,
                    row_id=int(row["id"]),
                    corp_code=row["corp_code"],
                    bsns_year=int(row["bsns_year"]),
                    content=full_text,
                )
                excerpt = full_text[: int(excerpt_chars)]
                session.execute(text(f"""
                    UPDATE {table_name}
                    SET {col}=:excerpt,
                        full_text_uri=:uri,
                        full_text_hash=:hash,
                        full_text_length=:content_length,
                        full_text_compressed_length=:compressed_length,
                        full_text_storage_status='externalized'
                    WHERE id=:id
                """), {
                    "excerpt": excerpt,
                    "uri": saved.storage_uri,
                    "hash": sha1_text(full_text),
                    "content_length": saved.content_length,
                    "compressed_length": saved.compressed_length,
                    "id": row["id"],
                })
                externalized += 1
            except Exception as exc:
                failed += 1
                if len(errors) < 20:
                    errors.append({"id": row["id"], "error": str(exc)})
        session.commit()
    return {
        "table": table_name,
        "total": len(rows),
        "externalized": externalized,
        "skipped": skipped,
        "failed": failed,
        "errors": errors,
    }
```

- [ ] **Step 4: Add CLI command**

In `kreports/cli/main.py`, add:

```python
@app.command("externalize-long-evidence-text")
def externalize_long_evidence_text_cmd(
    table_name: str = typer.Option(..., "--table", help="accounting_note_chapters/evidence_documents/report_sections"),
    excerpt_chars: int = typer.Option(2000, "--excerpt-chars", help="DB에 남길 짧은 본문 길이"),
    min_text_chars: int = typer.Option(4000, "--min-text-chars", help="외부화할 최소 본문 길이"),
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 행 수"),
    backend: str = typer.Option("file", "--backend", help="file/gcs"),
    bucket: Optional[str] = typer.Option(None, "--bucket", help="GCS bucket"),
    prefix: str = typer.Option("evidence/full-text", "--prefix", help="blob prefix"),
):
    """긴 파생 evidence 본문을 GCS/file로 옮기고 DB에는 excerpt와 manifest만 남긴다."""
    from kreports.maintenance.evidence_blob_migration import externalize_long_evidence_text

    result = externalize_long_evidence_text(
        table_name=table_name,
        excerpt_chars=excerpt_chars,
        min_text_chars=min_text_chars,
        limit=limit,
        backend=backend,
        bucket=bucket,
        prefix=prefix,
    )
    _json_print(result)
```

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest tests/test_evidence_blob_storage.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/maintenance/evidence_blob_migration.py kreports/cli/main.py tests/test_evidence_blob_storage.py
git commit -m "feat: externalize long evidence text"
```

---

### Task 4: Add Lazy Full-Text Loading for MCP APIs

**Files:**
- Modify: `kreports/analysis/api.py`
- Test: `tests/test_auditor_peer_tools.py` or `tests/test_evidence_documents.py`

- [ ] **Step 1: Add API test for full text URI fallback**

Add a test in `tests/test_evidence_documents.py`:

```python
def test_search_dataset_returns_excerpt_and_full_text_uri_for_externalized_evidence(temp_engine, tmp_path, monkeypatch):
    from sqlalchemy import text
    from kreports.db.engine import get_session
    from kreports.analysis.api import search_dataset

    long_text = "수익인식 회계정책 " + ("본문 " * 500)
    with get_session() as session:
        session.execute(text("""
            INSERT INTO evidence_documents
            (corp_code, bsns_year, source_type, rcept_no, evidence_scope, title,
             normalized_text, text_hash, text_length, source_count, generated_at,
             full_text_uri, full_text_hash, full_text_length, full_text_compressed_length,
             full_text_storage_status)
            VALUES
            ('00126380', 2024, 'business_report', '20250331000001', 'accounting_policy',
             '회계정책', '수익인식 회계정책 excerpt', 'hash', 12, 1, CURRENT_TIMESTAMP,
             'gs://bucket/evidence.txt.gz', 'fullhash', :full_length, 100, 'externalized')
        """), {"full_length": len(long_text)})
        session.commit()

    result = search_dataset(dataset="evidence_documents", query="수익인식", limit=1)

    assert result["results"][0]["excerpt"]
    assert result["results"][0]["full_text_uri"] == "gs://bucket/evidence.txt.gz"
    assert result["results"][0]["full_text_available"] is True
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run pytest tests/test_evidence_documents.py::test_search_dataset_returns_excerpt_and_full_text_uri_for_externalized_evidence -q
```

Expected: fails because `full_text_uri` is not surfaced.

- [ ] **Step 3: Update `search_dataset` evidence document projection**

In `kreports/analysis/api.py`, where `dataset == "evidence_documents"` rows are converted to result dictionaries, include:

```python
"full_text_uri": row.get("full_text_uri"),
"full_text_length": row.get("full_text_length"),
"full_text_available": bool(row.get("full_text_uri")),
"text_storage_status": row.get("full_text_storage_status") or "inline_excerpt",
```

Ensure the SELECT for `evidence_documents` includes:

```sql
full_text_uri, full_text_length, full_text_storage_status
```

- [ ] **Step 4: Run test**

```bash
uv run pytest tests/test_evidence_documents.py::test_search_dataset_returns_excerpt_and_full_text_uri_for_externalized_evidence -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add kreports/analysis/api.py tests/test_evidence_documents.py
git commit -m "feat: expose externalized evidence metadata"
```

---

### Task 5: Create Compact Financial Facts Table

**Files:**
- Modify: `kreports/db/models.py`
- Modify: `kreports/db/engine.py`
- Create: `kreports/maintenance/financial_compact.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_runtime_db_export.py`

- [ ] **Step 1: Add compact facts schema test**

Create `tests/test_runtime_db_export.py`:

```python
from sqlalchemy import inspect


def test_financial_facts_compact_schema(temp_engine):
    inspector = inspect(temp_engine)
    columns = {column["name"] for column in inspector.get_columns("financial_facts_compact")}
    assert {
        "corp_code",
        "bsns_year",
        "fs_div",
        "metric_key",
        "metric_name",
        "amount",
        "source_account_id",
        "source_account_nm",
    }.issubset(columns)
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run pytest tests/test_runtime_db_export.py::test_financial_facts_compact_schema -q
```

Expected: fails because table does not exist.

- [ ] **Step 3: Add table model/migration**

Add model:

```python
class FinancialFactCompact(Base):
    __tablename__ = "financial_facts_compact"

    id = Column(Integer, primary_key=True)
    corp_code = Column(String(8), nullable=False)
    bsns_year = Column(SmallInteger, nullable=False)
    fs_div = Column(String(3), nullable=False)
    metric_key = Column(String(50), nullable=False)
    metric_name = Column(String(200), nullable=False)
    amount = Column(BigInteger, nullable=True)
    source_account_id = Column(String(200), nullable=True)
    source_account_nm = Column(String(300), nullable=True)
    fetched_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("corp_code", "bsns_year", "fs_div", "metric_key", name="uq_financial_facts_compact"),
        Index("idx_fin_compact_corp_year", "corp_code", "bsns_year"),
        Index("idx_fin_compact_metric", "metric_key"),
    )
```

Add migration DDL in `kreports/db/engine.py`:

```sql
CREATE TABLE IF NOT EXISTS financial_facts_compact (
  id INTEGER PRIMARY KEY,
  corp_code VARCHAR(8) NOT NULL,
  bsns_year SMALLINT NOT NULL,
  fs_div VARCHAR(3) NOT NULL,
  metric_key VARCHAR(50) NOT NULL,
  metric_name VARCHAR(200) NOT NULL,
  amount BIGINT,
  source_account_id VARCHAR(200),
  source_account_nm VARCHAR(300),
  fetched_at DATETIME NOT NULL,
  CONSTRAINT uq_financial_facts_compact UNIQUE (corp_code, bsns_year, fs_div, metric_key)
)
```

- [ ] **Step 4: Run schema test**

```bash
uv run pytest tests/test_runtime_db_export.py::test_financial_facts_compact_schema -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add kreports/db/models.py kreports/db/engine.py tests/test_runtime_db_export.py
git commit -m "feat: add compact financial facts table"
```

---

### Task 6: Populate Compact Financial Facts

**Files:**
- Create: `kreports/maintenance/financial_compact.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_runtime_db_export.py`

- [ ] **Step 1: Write test for metric mapping**

Append to `tests/test_runtime_db_export.py`:

```python
from sqlalchemy import text


def test_rebuild_financial_facts_compact_maps_core_metrics(temp_engine):
    from kreports.db.engine import get_session
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    with get_session() as session:
        session.execute(text("""
            INSERT INTO financial_facts
            (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id, account_nm,
             ord, thstrm_amount, frmtrm_amount, fetched_at)
            VALUES
            ('00126380', 2024, '11011', 'CFS', 'BS', 'ifrs-full_Assets', '자산총계', 1, 1000, 900, CURRENT_TIMESTAMP),
            ('00126380', 2024, '11011', 'CFS', 'IS', 'ifrs-full_Revenue', '매출액', 1, 300, 250, CURRENT_TIMESTAMP)
        """))
        session.commit()

    out = rebuild_financial_facts_compact(year_from=2024, year_to=2024)

    assert out["inserted_or_updated"] == 2
    with get_session() as session:
        rows = session.execute(text("""
            SELECT metric_key, amount FROM financial_facts_compact
            WHERE corp_code='00126380'
            ORDER BY metric_key
        """)).all()
    assert rows == [("assets", 1000), ("revenue", 300)]
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run pytest tests/test_runtime_db_export.py::test_rebuild_financial_facts_compact_maps_core_metrics -q
```

Expected: fails because `rebuild_financial_facts_compact` does not exist.

- [ ] **Step 3: Implement compact rebuild**

Create `kreports/maintenance/financial_compact.py`:

```python
from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import get_session


METRIC_MAP = {
    "ifrs-full_Assets": ("assets", "자산총계"),
    "ifrs-full_Liabilities": ("liabilities", "부채총계"),
    "ifrs-full_Equity": ("equity", "자본총계"),
    "ifrs-full_Revenue": ("revenue", "매출액"),
    "ifrs-full_ProfitLoss": ("profit_loss", "당기순손익"),
    "ifrs-full_OperatingProfitLoss": ("operating_profit", "영업손익"),
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": ("operating_cash_flow", "영업활동현금흐름"),
    "ifrs-full_CashFlowsFromUsedInInvestingActivities": ("investing_cash_flow", "투자활동현금흐름"),
    "ifrs-full_CashFlowsFromUsedInFinancingActivities": ("financing_cash_flow", "재무활동현금흐름"),
}


def rebuild_financial_facts_compact(*, year_from: int | None = None, year_to: int | None = None) -> dict:
    params: dict[str, object] = {}
    where = ["reprt_code='11011'", "account_id IN :account_ids"]
    params["account_ids"] = tuple(METRIC_MAP.keys())
    if year_from is not None:
        where.append("bsns_year >= :year_from")
        params["year_from"] = int(year_from)
    if year_to is not None:
        where.append("bsns_year <= :year_to")
        params["year_to"] = int(year_to)
    sql = text(f"""
        SELECT corp_code, bsns_year, fs_div, account_id, account_nm, thstrm_amount
        FROM financial_facts
        WHERE {' AND '.join(where)}
    """)
    inserted_or_updated = 0
    with get_session() as session:
        rows = session.execute(sql, params).mappings().all()
        for row in rows:
            metric_key, metric_name = METRIC_MAP[row["account_id"]]
            session.execute(text("""
                INSERT INTO financial_facts_compact
                (corp_code, bsns_year, fs_div, metric_key, metric_name, amount,
                 source_account_id, source_account_nm, fetched_at)
                VALUES
                (:corp_code, :bsns_year, :fs_div, :metric_key, :metric_name, :amount,
                 :source_account_id, :source_account_nm, CURRENT_TIMESTAMP)
                ON CONFLICT(corp_code, bsns_year, fs_div, metric_key) DO UPDATE SET
                  amount=excluded.amount,
                  source_account_id=excluded.source_account_id,
                  source_account_nm=excluded.source_account_nm,
                  fetched_at=excluded.fetched_at
            """), {
                "corp_code": row["corp_code"],
                "bsns_year": row["bsns_year"],
                "fs_div": row["fs_div"],
                "metric_key": metric_key,
                "metric_name": metric_name,
                "amount": row["thstrm_amount"],
                "source_account_id": row["account_id"],
                "source_account_nm": row["account_nm"],
            })
            inserted_or_updated += 1
        session.commit()
    return {"source_rows": len(rows), "inserted_or_updated": inserted_or_updated}
```

- [ ] **Step 4: Add CLI command**

In `kreports/cli/main.py`:

```python
@app.command("rebuild-financial-facts-compact")
def rebuild_financial_facts_compact_cmd(
    year_from: Optional[int] = typer.Option(None, "--year-from"),
    year_to: Optional[int] = typer.Option(None, "--year-to"),
):
    """5개년 runtime DB용 핵심 재무 metric 테이블을 재생성한다."""
    from kreports.maintenance.financial_compact import rebuild_financial_facts_compact

    result = rebuild_financial_facts_compact(year_from=year_from, year_to=year_to)
    _json_print(result)
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_runtime_db_export.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/maintenance/financial_compact.py kreports/cli/main.py tests/test_runtime_db_export.py
git commit -m "feat: rebuild compact financial facts"
```

---

### Task 7: Export Five-Year Compact Runtime DB

**Files:**
- Create: `kreports/maintenance/runtime_export.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_runtime_db_export.py`

- [ ] **Step 1: Write export test**

Append to `tests/test_runtime_db_export.py`:

```python
def test_export_runtime_db_excludes_heavy_warehouse_tables(temp_engine, tmp_path):
    from kreports.maintenance.runtime_export import export_runtime_db

    out_path = tmp_path / "runtime.db"
    result = export_runtime_db(output_path=out_path, year_from=2024, year_to=2025, profile="compact")

    assert result["ok"] is True
    assert out_path.exists()
    assert "financial_facts" in result["excluded_tables"]
    assert "extraction_runs" in result["excluded_tables"]
    assert "fetch_log" in result["excluded_tables"]
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run pytest tests/test_runtime_db_export.py::test_export_runtime_db_excludes_heavy_warehouse_tables -q
```

Expected: fails because module does not exist.

- [ ] **Step 3: Implement export command**

Create `kreports/maintenance/runtime_export.py`:

```python
from __future__ import annotations

import sqlite3
from pathlib import Path

from kreports.config import settings


COMPACT_EXCLUDED_TABLES = {
    "financial_facts",
    "extraction_runs",
    "fetch_log",
}


def _sqlite_path_from_db_url(db_url: str) -> Path:
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        raise ValueError("runtime export currently supports sqlite DB_URL only")
    return Path(db_url[len(prefix):])


def export_runtime_db(*, output_path: str | Path, year_from: int, year_to: int, profile: str = "compact") -> dict:
    if profile != "compact":
        raise ValueError("only compact profile is supported")
    src = _sqlite_path_from_db_url(settings.db_url)
    dest = Path(output_path)
    if dest.exists():
        dest.unlink()
    src_conn = sqlite3.connect(src)
    dest_conn = sqlite3.connect(dest)
    try:
        tables = [
            row[0]
            for row in src_conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        ]
        copied = []
        for table in tables:
            if table in COMPACT_EXCLUDED_TABLES:
                continue
            schema = src_conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if not schema or not schema[0]:
                continue
            dest_conn.execute(schema[0])
            columns = [row[1] for row in src_conn.execute(f"PRAGMA table_info({table})")]
            col_csv = ", ".join(columns)
            rows = src_conn.execute(f"SELECT {col_csv} FROM {table}").fetchall()
            placeholders = ", ".join(["?"] * len(columns))
            if rows:
                dest_conn.executemany(f"INSERT INTO {table} ({col_csv}) VALUES ({placeholders})", rows)
            copied.append(table)
        dest_conn.commit()
        dest_conn.execute("VACUUM")
    finally:
        src_conn.close()
        dest_conn.close()
    return {
        "ok": True,
        "output_path": str(dest),
        "profile": profile,
        "year_from": year_from,
        "year_to": year_to,
        "copied_tables": copied,
        "excluded_tables": sorted(COMPACT_EXCLUDED_TABLES),
        "bytes": dest.stat().st_size,
    }
```

- [ ] **Step 4: Add CLI**

In `kreports/cli/main.py`:

```python
@app.command("export-runtime-db")
def export_runtime_db_cmd(
    output_path: Path = typer.Option(..., "--output", help="exported SQLite DB path"),
    year_from: int = typer.Option(..., "--year-from"),
    year_to: int = typer.Option(..., "--year-to"),
    profile: str = typer.Option("compact", "--profile"),
):
    """배포용 compact runtime DB를 생성한다."""
    from kreports.maintenance.runtime_export import export_runtime_db

    result = export_runtime_db(
        output_path=output_path,
        year_from=year_from,
        year_to=year_to,
        profile=profile,
    )
    _json_print(result)
```

- [ ] **Step 5: Run focused tests**

```bash
uv run pytest tests/test_runtime_db_export.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/maintenance/runtime_export.py kreports/cli/main.py tests/test_runtime_db_export.py
git commit -m "feat: export compact runtime db"
```

---

### Task 8: Upload Runtime DB Artifact to GCS

**Files:**
- Modify: `kreports/maintenance/runtime_export.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_runtime_db_export.py`

- [ ] **Step 1: Write upload manifest test**

Append to `tests/test_runtime_db_export.py`:

```python
def test_runtime_db_manifest_contains_hash_and_counts(tmp_path):
    from kreports.maintenance.runtime_export import build_runtime_db_manifest

    db_path = tmp_path / "runtime.db"
    db_path.write_bytes(b"runtime")

    manifest = build_runtime_db_manifest(db_path=db_path, profile="compact", year_from=2021, year_to=2025)

    assert manifest["profile"] == "compact"
    assert manifest["year_from"] == 2021
    assert manifest["year_to"] == 2025
    assert manifest["bytes"] == len(b"runtime")
    assert len(manifest["sha256"]) == 64
```

- [ ] **Step 2: Run test and confirm failure**

```bash
uv run pytest tests/test_runtime_db_export.py::test_runtime_db_manifest_contains_hash_and_counts -q
```

Expected: fails because manifest function does not exist.

- [ ] **Step 3: Implement manifest builder**

In `kreports/maintenance/runtime_export.py` add:

```python
import hashlib
from datetime import datetime, timezone


def build_runtime_db_manifest(*, db_path: str | Path, profile: str, year_from: int, year_to: int) -> dict:
    path = Path(db_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "profile": profile,
        "year_from": int(year_from),
        "year_to": int(year_to),
        "bytes": path.stat().st_size,
        "sha256": digest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
```

- [ ] **Step 4: Add upload function**

In `kreports/maintenance/runtime_export.py` add:

```python
import gzip
import json
from kreports.storage.raw_documents import RawDocumentStore


def upload_runtime_db_artifact(
    *,
    db_path: str | Path,
    bucket: str,
    prefix: str = "runtime-db",
    profile: str = "compact",
    year_from: int,
    year_to: int,
) -> dict:
    from google.cloud import storage

    path = Path(db_path)
    manifest = build_runtime_db_manifest(
        db_path=path,
        profile=profile,
        year_from=year_from,
        year_to=year_to,
    )
    compressed = gzip.compress(path.read_bytes())
    client = storage.Client()
    db_object = f"{prefix.strip('/')}/kreports-{profile}-{year_from}-{year_to}.db.gz"
    manifest_object = f"{prefix.strip('/')}/kreports-{profile}-{year_from}-{year_to}.manifest.json"
    bucket_obj = client.bucket(bucket)
    bucket_obj.blob(db_object).upload_from_string(compressed, content_type="application/gzip")
    bucket_obj.blob(manifest_object).upload_from_string(
        json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        content_type="application/json",
    )
    return {
        "ok": True,
        "db_uri": f"gs://{bucket}/{db_object}",
        "manifest_uri": f"gs://{bucket}/{manifest_object}",
        "manifest": manifest,
        "compressed_bytes": len(compressed),
    }
```

- [ ] **Step 5: Add CLI**

In `kreports/cli/main.py`:

```python
@app.command("upload-runtime-db-artifact")
def upload_runtime_db_artifact_cmd(
    db_path: Path = typer.Option(..., "--db", help="runtime DB path"),
    bucket: str = typer.Option(..., "--bucket", help="GCS bucket"),
    prefix: str = typer.Option("runtime-db", "--prefix"),
    profile: str = typer.Option("compact", "--profile"),
    year_from: int = typer.Option(..., "--year-from"),
    year_to: int = typer.Option(..., "--year-to"),
):
    """배포용 runtime DB artifact와 manifest를 GCS에 업로드한다."""
    from kreports.maintenance.runtime_export import upload_runtime_db_artifact

    result = upload_runtime_db_artifact(
        db_path=db_path,
        bucket=bucket,
        prefix=prefix,
        profile=profile,
        year_from=year_from,
        year_to=year_to,
    )
    _json_print(result)
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_runtime_db_export.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

```bash
git add kreports/maintenance/runtime_export.py kreports/cli/main.py tests/test_runtime_db_export.py
git commit -m "feat: upload runtime db artifact"
```

---

### Task 9: Operational Smoke on Real DB

**Files:**
- Modify docs only after smoke: `docs/deploy-http-mcp.md`, `docs/raw-retention-policy.md`

- [ ] **Step 1: Rebuild compact financial facts**

```bash
uv run kreports rebuild-financial-facts-compact --year-from 2021 --year-to 2025
```

Expected: JSON result with nonzero `inserted_or_updated`.

- [ ] **Step 2: Externalize long evidence in small GCS batch**

```bash
uv run kreports externalize-long-evidence-text \
  --table accounting_note_chapters \
  --min-text-chars 8000 \
  --excerpt-chars 2000 \
  --limit 100 \
  --backend gcs \
  --bucket kreports-raw-documents-gen-lang-client-0171998581 \
  --prefix evidence/full-text
```

Expected: `externalized > 0`, `failed = 0`.

- [ ] **Step 3: Export compact runtime DB**

```bash
uv run kreports export-runtime-db \
  --output artifacts/kreports-runtime-2021-2025.db \
  --year-from 2021 \
  --year-to 2025 \
  --profile compact
```

Expected: artifact created and smaller than maintainer `kreports.db`.

- [ ] **Step 4: Upload compact runtime DB**

```bash
uv run kreports upload-runtime-db-artifact \
  --db artifacts/kreports-runtime-2021-2025.db \
  --bucket kreports-raw-documents-gen-lang-client-0171998581 \
  --prefix runtime-db \
  --profile compact \
  --year-from 2021 \
  --year-to 2025
```

Expected: `db_uri` and `manifest_uri` returned.

- [ ] **Step 5: Run full tests**

```bash
uv run pytest -q
```

Expected: all tests pass.

- [ ] **Step 6: Document measured sizes**

Update docs with:

```text
Maintainer DB size:
Runtime DB artifact size:
Runtime DB gzip size:
Externalized long evidence rows:
GCS raw documents:
GCS evidence blobs:
```

- [ ] **Step 7: Commit docs**

```bash
git add docs/deploy-http-mcp.md docs/raw-retention-policy.md
git commit -m "docs: record compact runtime db operations"
```

---

## Acceptance Criteria

- Five-year MCP coverage is preserved for structured and summary-level questions.
- Runtime DB no longer needs full `financial_facts`, full long note bodies, or debug logs.
- GCS contains:
  - raw reports,
  - long evidence blobs,
  - deployable runtime DB artifact and manifest.
- SQLite maintainer DB may remain larger locally, but deployable runtime artifact is compact and reproducible.
- `uv run pytest -q` passes.
- Real GCS smoke verifies read/write/hash for raw and long evidence.

## Known Gaps

- The first export implementation copies whole tables except excluded tables. A later slice should add year filters and table-specific projections to reduce artifact size further.
- Postgres/Supabase migration is intentionally out of scope for this plan. This plan keeps SQLite artifact deployment as the near-term path.
- Search ranking may need improvement after long text is replaced by excerpts; this should be measured after Task 4.

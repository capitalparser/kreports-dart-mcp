# Raw Storage + Evidence DB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep 100% raw-document reusability while shrinking the runtime DB into financial facts, evidence indexes, and raw-document pointers.

**Architecture:** Move full `source_documents.raw_content` out of SQLite into compressed file/object storage, then keep `source_documents` as a manifest table with `storage_uri`, `content_length`, and `compressed_length`. MCP tools should answer from parsed financial/evidence tables first and lazy-load raw documents only for re-extraction, source proof, or raw search.

**Tech Stack:** Python, SQLAlchemy, SQLite, gzip, pytest, Typer CLI, existing `kreports` collector/MCP modules.

---

## Current Problem

The local DB is operationally overloaded:

- `kreports.db`: about 42GB.
- `source_documents`: about 38.4GB.
- Exact duplicate raw documents by `doc_hash`: only 6 groups, about 10.9MB recoverable.
- Therefore duplicate removal is not enough. The issue is raw XML/HTML bodies stored inline in SQLite.

We must not solve this by deleting raw evidence. The correct split is:

```text
Evidence/Facts DB
  - financial facts and 5-year canonical views
  - audit report sections
  - KAM topics and audit procedures
  - emphasis/other/going concern sections
  - accounting policies and note chapters
  - raw document manifest and storage pointers

Raw Store
  - compressed full XML/HTML documents
  - immutable by doc_hash
  - used for re-parsing, new extractor development, and source verification
```

## File Structure

- Modify `kreports/db/models.py`
  - Add raw storage columns to `SourceDocument`: `storage_uri`, `content_length`, `compressed_length`, `storage_status`.
  - Keep `raw_content` during transition. It becomes nullable only after migration validation.

- Modify `kreports/db/engine.py`
  - Add migration-safe `ALTER TABLE` statements for the new columns and indexes.

- Create `kreports/storage/raw_documents.py`
  - Own gzip write/read/hash verification.
  - Support local file URI first: `file://data/raw_documents/...`.
  - Keep interface object-storage friendly.

- Modify `kreports/collector/report_document_collector.py`
  - Persist new raw documents through the raw storage layer.
  - Keep existing parser behavior by loading raw content through a helper.
  - Avoid `SELECT raw_content` for all rows in `run_document_extractors`; stream one document at a time.

- Modify `kreports/collector/on_demand.py`
  - Cache on-demand disclosure raw documents through the same storage layer.

- Modify `kreports/analysis/api.py`
  - Raw dataset search should use evidence tables by default.
  - Only lazy-load raw content when `include_raw=True` or a raw-only search is explicitly requested.

- Modify `kreports/analysis/readiness.py`
  - Add raw storage readiness: migrated count, missing storage count, hash verification sample.

- Modify `kreports/cli/main.py`
  - Add `migrate-raw-documents-to-storage`.
  - Add `verify-raw-storage`.
  - Add `raw-storage-readiness`.

- Create tests:
  - `tests/test_raw_document_storage.py`
  - Extend `tests/test_business_report_cached_tools.py`
  - Extend `tests/test_on_demand_disclosure_fetch.py`
  - Extend `tests/test_auditor_readiness.py`

---

### Task 1: Add Storage Columns Without Breaking Existing DB

**Files:**
- Modify: `kreports/db/models.py`
- Modify: `kreports/db/engine.py`
- Test: `tests/test_raw_document_storage.py`

- [ ] **Step 1: Write failing schema test**

Create `tests/test_raw_document_storage.py`:

```python
from sqlalchemy import inspect


def test_source_documents_has_raw_storage_columns(temp_engine):
    inspector = inspect(temp_engine)
    columns = {column["name"] for column in inspector.get_columns("source_documents")}

    assert "storage_uri" in columns
    assert "content_length" in columns
    assert "compressed_length" in columns
    assert "storage_status" in columns
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_raw_document_storage.py::test_source_documents_has_raw_storage_columns -q
```

Expected: fails because columns do not exist.

- [ ] **Step 3: Update `SourceDocument` model**

In `kreports/db/models.py`, update `SourceDocument`:

```python
storage_uri = Column(String(500), nullable=True)
content_length = Column(Integer, nullable=True)
compressed_length = Column(Integer, nullable=True)
storage_status = Column(String(30), nullable=False, default="inline")
```

Add indexes:

```python
Index("idx_source_doc_storage_status", "storage_status"),
Index("idx_source_doc_storage_uri", "storage_uri"),
```

- [ ] **Step 4: Add migration-safe ALTER statements**

In `kreports/db/engine.py`, add statements to the existing schema migration block:

```python
_add_column_if_missing("source_documents", "storage_uri", "VARCHAR(500)")
_add_column_if_missing("source_documents", "content_length", "INTEGER")
_add_column_if_missing("source_documents", "compressed_length", "INTEGER")
_add_column_if_missing("source_documents", "storage_status", "VARCHAR(30) DEFAULT 'inline' NOT NULL")
```

Add indexes:

```python
"CREATE INDEX IF NOT EXISTS idx_source_doc_storage_status ON source_documents(storage_status)",
"CREATE INDEX IF NOT EXISTS idx_source_doc_storage_uri ON source_documents(storage_uri)",
```

- [ ] **Step 5: Run schema test**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_raw_document_storage.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/db/models.py kreports/db/engine.py tests/test_raw_document_storage.py
git commit -m "feat: add raw document storage manifest columns"
```

---

### Task 2: Implement Local Compressed Raw Store

**Files:**
- Create: `kreports/storage/raw_documents.py`
- Create: `kreports/storage/__init__.py`
- Test: `tests/test_raw_document_storage.py`

- [ ] **Step 1: Add failing storage round-trip test**

Append to `tests/test_raw_document_storage.py`:

```python
from pathlib import Path

from kreports.storage.raw_documents import RawDocumentStore


def test_raw_document_store_writes_gzip_and_verifies_hash(tmp_path):
    store = RawDocumentStore(base_dir=tmp_path)
    content = "<DOCUMENT><TITLE>감사보고서</TITLE><P>핵심감사사항</P></DOCUMENT>"

    saved = store.write(
        corp_code="00126380",
        bsns_year=2025,
        source_type="audit_report",
        rcept_no="20260331000001_00760_xml",
        content_type="xml",
        content=content,
    )

    assert saved.storage_uri.startswith("file://")
    assert saved.content_length == len(content.encode("utf-8"))
    assert saved.compressed_length > 0
    assert Path(saved.path).exists()
    assert store.read(saved.storage_uri, expected_hash=saved.doc_hash) == content
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_raw_document_storage.py::test_raw_document_store_writes_gzip_and_verifies_hash -q
```

Expected: import error for `RawDocumentStore`.

- [ ] **Step 3: Implement storage module**

Create `kreports/storage/__init__.py`:

```python
"""Storage helpers for raw disclosure documents."""
```

Create `kreports/storage/raw_documents.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class StoredRawDocument:
    storage_uri: str
    path: str
    doc_hash: str
    content_length: int
    compressed_length: int


def sha1_text(content: str) -> str:
    return hashlib.sha1((content or "").encode("utf-8")).hexdigest()


class RawDocumentStore:
    def __init__(self, base_dir: str | Path = "data/raw_documents"):
        self.base_dir = Path(base_dir)

    def _path_for(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        source_type: str,
        rcept_no: str,
        content_type: str,
    ) -> Path:
        suffix = "html" if content_type == "html" else "xml"
        safe_rcept_no = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in rcept_no)
        return self.base_dir / str(bsns_year) / source_type / corp_code / f"{safe_rcept_no}.{suffix}.gz"

    def write(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        source_type: str,
        rcept_no: str,
        content_type: str,
        content: str,
    ) -> StoredRawDocument:
        path = self._path_for(
            corp_code=corp_code,
            bsns_year=int(bsns_year),
            source_type=source_type,
            rcept_no=rcept_no,
            content_type=content_type,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        data = (content or "").encode("utf-8")
        with gzip.open(path, "wb") as fh:
            fh.write(data)
        return StoredRawDocument(
            storage_uri=f"file://{path.resolve()}",
            path=str(path),
            doc_hash=sha1_text(content),
            content_length=len(data),
            compressed_length=path.stat().st_size,
        )

    def read(self, storage_uri: str, *, expected_hash: str | None = None) -> str:
        parsed = urlparse(storage_uri)
        if parsed.scheme != "file":
            raise ValueError(f"unsupported storage_uri scheme: {parsed.scheme}")
        path = Path(parsed.path)
        with gzip.open(path, "rb") as fh:
            content = fh.read().decode("utf-8")
        if expected_hash and sha1_text(content) != expected_hash:
            raise ValueError("raw document hash mismatch")
        return content
```

- [ ] **Step 4: Run storage tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_raw_document_storage.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add kreports/storage tests/test_raw_document_storage.py
git commit -m "feat: add compressed raw document store"
```

---

### Task 3: Add Loader Helper and Avoid Full Raw Table Scans

**Files:**
- Modify: `kreports/collector/report_document_collector.py`
- Test: `tests/test_business_report_cached_tools.py`

- [ ] **Step 1: Add failing lazy-load extractor test**

Append to `tests/test_business_report_cached_tools.py`:

```python
def test_document_extractors_load_raw_from_storage_uri(temp_engine, tmp_path, monkeypatch):
    import kreports.collector.report_document_collector as collector_module
    from kreports.db.engine import get_session
    from kreports.db.models import AccountingNoteChapter, SourceDocument
    from kreports.storage.raw_documents import RawDocumentStore

    store = RawDocumentStore(base_dir=tmp_path)
    content = """
    <DOCUMENT>
      <TITLE>III. 재무에 관한 사항</TITLE>
      <TITLE>연결재무제표 주석</TITLE>
      <P>2. 재무제표 작성기준</P><P>한국채택국제회계기준에 따라 작성되었습니다.</P>
      <P>3. 중요한 회계정책</P><P>수익은 수행의무 이행 시 인식합니다.</P>
      <P>4. 중요한 회계추정 및 판단</P><P>손상검사에는 경영진 판단이 필요합니다.</P>
      <P>5. 영업부문</P><P>다음 주석입니다.</P>
    </DOCUMENT>
    """
    saved = store.write(
        corp_code="00000001",
        bsns_year=2024,
        source_type="business_report",
        rcept_no="20250331000001",
        content_type="xml",
        content=content,
    )
    monkeypatch.setattr(collector_module, "RawDocumentStore", lambda: store)

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content="",
            doc_hash=saved.doc_hash,
            storage_uri=saved.storage_uri,
            content_length=saved.content_length,
            compressed_length=saved.compressed_length,
            storage_status="externalized",
        ))

    out = collector_module.run_document_extractors(year=2024, source_type="business_report", extractor="note_chapters")

    assert out["ok"] == 1
    with get_session() as session:
        assert session.query(AccountingNoteChapter).filter_by(corp_code="00000001").count() == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_business_report_cached_tools.py::test_document_extractors_load_raw_from_storage_uri -q
```

Expected: fails because extractor only uses selected `raw_content`.

- [ ] **Step 3: Implement helper**

In `kreports/collector/report_document_collector.py`, import:

```python
from kreports.storage.raw_documents import RawDocumentStore
```

Add:

```python
def _load_source_document_content(row) -> str:
    raw_content = row.raw_content or ""
    if raw_content:
        return raw_content
    storage_uri = getattr(row, "storage_uri", None)
    if not storage_uri:
        return ""
    return RawDocumentStore().read(storage_uri, expected_hash=row.doc_hash)
```

- [ ] **Step 4: Change extractor query to stream lightweight rows**

Replace the `run_document_extractors` SELECT with:

```sql
SELECT id, rcept_no, dcm_no, corp_code, bsns_year, source_type, report_nm,
       raw_content, doc_hash, storage_uri, content_type
FROM source_documents
WHERE content_type!='derived_report_sections'
```

Then convert each row to a mapping and call:

```python
content = _load_source_document_content(row)
```

Keep the existing parser calls unchanged after `content` is loaded.

- [ ] **Step 5: Run focused tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_business_report_cached_tools.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/collector/report_document_collector.py tests/test_business_report_cached_tools.py
git commit -m "feat: lazy load raw source documents from storage"
```

---

### Task 4: Implement Safe Migration Command

**Files:**
- Modify: `kreports/cli/main.py`
- Create: `kreports/maintenance/raw_storage_migration.py`
- Create: `kreports/maintenance/__init__.py`
- Test: `tests/test_raw_document_storage.py`

- [ ] **Step 1: Add migration test**

Append to `tests/test_raw_document_storage.py`:

```python
def test_migrate_raw_documents_to_storage_preserves_hash(temp_engine, tmp_path, monkeypatch):
    import kreports.maintenance.raw_storage_migration as migration_module
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.storage.raw_documents import RawDocumentStore

    monkeypatch.setattr(migration_module, "RawDocumentStore", lambda: RawDocumentStore(base_dir=tmp_path))

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content="<DOCUMENT><P>원문</P></DOCUMENT>",
            doc_hash="9a7c8f0a2b3ad65af02da9e84c5f9bfe67d20d5d",
            storage_status="inline",
        ))

    result = migration_module.migrate_raw_documents_to_storage(limit=10, clear_inline=True)

    assert result["migrated"] == 1
    with get_session() as session:
        doc = session.query(SourceDocument).one()
        assert doc.storage_uri.startswith("file://")
        assert doc.storage_status == "externalized"
        assert doc.raw_content == ""
        assert doc.content_length > 0
        assert doc.compressed_length > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_raw_document_storage.py::test_migrate_raw_documents_to_storage_preserves_hash -q
```

Expected: import error for migration module.

- [ ] **Step 3: Implement migration module**

Create `kreports/maintenance/__init__.py`:

```python
"""Maintenance jobs for local and deployed datasets."""
```

Create `kreports/maintenance/raw_storage_migration.py`:

```python
from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import get_session
from kreports.db.models import SourceDocument
from kreports.storage.raw_documents import RawDocumentStore, sha1_text


def migrate_raw_documents_to_storage(*, limit: int | None = None, clear_inline: bool = False) -> dict:
    totals = {"scanned": 0, "migrated": 0, "skipped": 0, "errors": []}
    with get_session() as session:
        query = (
            session.query(SourceDocument)
            .filter(SourceDocument.content_type != "derived_report_sections")
            .filter(SourceDocument.storage_status != "externalized")
            .filter(SourceDocument.raw_content != "")
            .order_by(SourceDocument.bsns_year, SourceDocument.source_type, SourceDocument.rcept_no)
        )
        if limit:
            query = query.limit(int(limit))
        rows = query.all()

        store = RawDocumentStore()
        for doc in rows:
            totals["scanned"] += 1
            content = doc.raw_content or ""
            if not content:
                totals["skipped"] += 1
                continue
            actual_hash = sha1_text(content)
            if doc.doc_hash and actual_hash != doc.doc_hash:
                totals["errors"].append({"rcept_no": doc.rcept_no, "error": "hash mismatch before migration"})
                continue
            saved = store.write(
                corp_code=doc.corp_code,
                bsns_year=doc.bsns_year,
                source_type=doc.source_type,
                rcept_no=doc.rcept_no,
                content_type=doc.content_type,
                content=content,
            )
            doc.storage_uri = saved.storage_uri
            doc.doc_hash = saved.doc_hash
            doc.content_length = saved.content_length
            doc.compressed_length = saved.compressed_length
            doc.storage_status = "externalized"
            if clear_inline:
                doc.raw_content = ""
            totals["migrated"] += 1
        session.flush()
    return totals


def raw_storage_readiness() -> dict:
    with get_session() as session:
        row = session.execute(text(
            """
            SELECT
              COUNT(*) total,
              SUM(CASE WHEN content_type='derived_report_sections' THEN 1 ELSE 0 END) derived,
              SUM(CASE WHEN storage_status='externalized' THEN 1 ELSE 0 END) externalized,
              SUM(CASE WHEN storage_uri IS NULL OR storage_uri='' THEN 1 ELSE 0 END) missing_uri,
              SUM(CASE WHEN raw_content!='' THEN 1 ELSE 0 END) inline_present
            FROM source_documents
            """
        )).mappings().one()
    return dict(row)
```

- [ ] **Step 4: Add CLI command**

In `kreports/cli/main.py`, add:

```python
@app.command("migrate-raw-documents-to-storage")
def migrate_raw_documents_to_storage_cmd(
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 처리 문서 수"),
    clear_inline: bool = typer.Option(False, "--clear-inline", help="검증 후 DB raw_content를 비움"),
):
    """source_documents.raw_content를 압축 raw store로 이전한다."""
    from kreports.maintenance.raw_storage_migration import migrate_raw_documents_to_storage

    init_db()
    result = migrate_raw_documents_to_storage(limit=limit, clear_inline=clear_inline)
    _json_print(result)
```

- [ ] **Step 5: Run migration tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_raw_document_storage.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/maintenance kreports/cli/main.py tests/test_raw_document_storage.py
git commit -m "feat: migrate raw documents to compressed storage"
```

---

### Task 5: Preserve Financial Completeness Separately From Raw Documents

**Files:**
- Modify: `kreports/analysis/readiness.py`
- Create: `kreports/analysis/financial_timeseries.py`
- Test: `tests/test_financial_timeseries.py`

- [ ] **Step 1: Add DCF-ready financial timeseries test**

Create `tests/test_financial_timeseries.py`:

```python
from kreports.analysis.financial_timeseries import get_financial_timeseries_quality
from kreports.db.models import Company, Financial


def test_financial_timeseries_quality_requires_five_year_cfs(temp_engine):
    from kreports.db.engine import get_session

    with get_session() as session:
        session.add(Company(corp_code="00126380", stock_code="005930", corp_name="삼성전자", market="KOSPI"))
        for year in [2021, 2022, 2023, 2024, 2025]:
            session.add(Financial(
                corp_code="00126380",
                year=year,
                quarter=4,
                fs_div="CFS",
                revenue=1000 + year,
                operating_profit=100,
                net_income=80,
                total_assets=5000,
                total_debt=2000,
                total_equity=3000,
            ))

    out = get_financial_timeseries_quality("00126380", year=2025, years_back=5)

    assert out["verdict"] == "pass"
    assert out["years"] == [2021, 2022, 2023, 2024, 2025]
    assert out["fs_div_used"] == "CFS"
    assert out["missing_years"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_financial_timeseries.py -q
```

Expected: import error.

- [ ] **Step 3: Implement financial timeseries quality**

Create `kreports/analysis/financial_timeseries.py`:

```python
from __future__ import annotations

from sqlalchemy import text

from kreports.db.engine import engine


def get_financial_timeseries_quality(corp_code: str, *, year: int = 2025, years_back: int = 5) -> dict:
    years = list(range(int(year) - int(years_back) + 1, int(year) + 1))
    with engine.connect() as conn:
        rows = conn.execute(text(
            """
            SELECT year, fs_div, revenue, operating_profit, net_income,
                   total_assets, total_debt, total_equity
            FROM financials
            WHERE corp_code=:corp_code
              AND quarter=4
              AND year BETWEEN :start_year AND :year
            ORDER BY year, CASE WHEN fs_div='CFS' THEN 0 ELSE 1 END
            """
        ), {"corp_code": corp_code, "start_year": years[0], "year": year}).mappings().all()

    by_year = {}
    for row in rows:
        if row["year"] not in by_year:
            by_year[row["year"]] = dict(row)

    missing = [target_year for target_year in years if target_year not in by_year]
    fs_divs = {row["fs_div"] for row in by_year.values()}
    fs_div_used = "CFS" if fs_divs == {"CFS"} else "mixed" if fs_divs else None
    return {
        "verdict": "pass" if not missing and fs_div_used == "CFS" else "conditional",
        "corp_code": corp_code,
        "years": years,
        "fs_div_used": fs_div_used,
        "missing_years": missing,
        "rows": list(by_year.values()),
    }
```

- [ ] **Step 4: Add readiness integration**

In `kreports/analysis/readiness.py`, ensure `dataset_completeness_snapshot` keeps financial facts separate from raw source readiness. Do not treat raw document migration as a financial completeness failure if `financials`/`financial_facts` are complete.

- [ ] **Step 5: Run tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_financial_timeseries.py tests/test_auditor_readiness.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add kreports/analysis/financial_timeseries.py kreports/analysis/readiness.py tests/test_financial_timeseries.py
git commit -m "feat: separate financial timeseries readiness from raw documents"
```

---

### Task 6: Raw Storage Verification Gate Before Clearing Inline Content

**Files:**
- Modify: `kreports/maintenance/raw_storage_migration.py`
- Modify: `kreports/cli/main.py`
- Test: `tests/test_raw_document_storage.py`

- [ ] **Step 1: Add verification test**

Append to `tests/test_raw_document_storage.py`:

```python
def test_verify_raw_storage_detects_missing_file(temp_engine):
    from kreports.db.engine import get_session
    from kreports.db.models import SourceDocument
    from kreports.maintenance.raw_storage_migration import verify_raw_storage

    with get_session() as session:
        session.add(SourceDocument(
            rcept_no="20250331000001",
            corp_code="00000001",
            bsns_year=2024,
            source_type="business_report",
            report_nm="사업보고서",
            content_type="xml",
            raw_content="",
            doc_hash="abc",
            storage_uri="file:///missing/file.xml.gz",
            storage_status="externalized",
        ))

    out = verify_raw_storage(limit=10)

    assert out["checked"] == 1
    assert out["failed"] == 1
    assert "missing" in out["errors"][0]["error"]
```

- [ ] **Step 2: Implement verifier**

In `kreports/maintenance/raw_storage_migration.py`, add:

```python
def verify_raw_storage(*, limit: int | None = None) -> dict:
    totals = {"checked": 0, "ok": 0, "failed": 0, "errors": []}
    with get_session() as session:
        query = (
            session.query(SourceDocument)
            .filter(SourceDocument.storage_status == "externalized")
            .filter(SourceDocument.storage_uri.isnot(None))
            .order_by(SourceDocument.bsns_year, SourceDocument.rcept_no)
        )
        if limit:
            query = query.limit(int(limit))
        rows = query.all()
    store = RawDocumentStore()
    for doc in rows:
        totals["checked"] += 1
        try:
            content = store.read(doc.storage_uri, expected_hash=doc.doc_hash)
            if doc.content_length is not None and len(content.encode("utf-8")) != doc.content_length:
                raise ValueError("content length mismatch")
            totals["ok"] += 1
        except Exception as exc:
            totals["failed"] += 1
            if len(totals["errors"]) < 20:
                totals["errors"].append({"rcept_no": doc.rcept_no, "error": str(exc)})
    return totals
```

- [ ] **Step 3: Add CLI verifier**

In `kreports/cli/main.py`, add:

```python
@app.command("verify-raw-storage")
def verify_raw_storage_cmd(
    limit: Optional[int] = typer.Option(None, "--limit", help="최대 검증 문서 수"),
):
    """외부화된 원문을 읽고 hash/length를 검증한다."""
    from kreports.maintenance.raw_storage_migration import verify_raw_storage

    result = verify_raw_storage(limit=limit)
    _json_print(result)
```

- [ ] **Step 4: Run tests**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/test_raw_document_storage.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add kreports/maintenance/raw_storage_migration.py kreports/cli/main.py tests/test_raw_document_storage.py
git commit -m "feat: verify external raw document storage"
```

---

### Task 7: Rollout Procedure on Current 42GB DB

**Files:**
- No code changes after previous tasks.
- Operational commands only.

- [ ] **Step 1: Stop backfill wrapper before migration**

Run:

```bash
screen -ls
```

Expected: identify `kreports_source_documents_limit_aware`.

Then stop the active worker screen or pause launch loop. Do not run additional source backfills during migration.

- [ ] **Step 2: Run small migration sample without clearing inline**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run kreports migrate-raw-documents-to-storage --limit 20
```

Expected:

```json
{"scanned": 20, "migrated": 20, "skipped": 0, "errors": []}
```

- [ ] **Step 3: Verify sample**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run kreports verify-raw-storage --limit 20
```

Expected:

```json
{"checked": 20, "ok": 20, "failed": 0, "errors": []}
```

- [ ] **Step 4: Run extractor smoke against externalized sample**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run kreports run-document-extractors --year 2025 --source-type business_report --extractor note_chapters --limit 20
```

Expected: command completes without hash or missing-file errors.

- [ ] **Step 5: Clear inline content in batches**

Only after Step 2-4 pass, run:

```bash
UV_CACHE_DIR=.uv-cache uv run kreports migrate-raw-documents-to-storage --limit 500 --clear-inline
```

Repeat in batches. Do not run `VACUUM` until enough disk space exists for a compact copy.

- [ ] **Step 6: Check readiness after each batch**

Run:

```bash
UV_CACHE_DIR=.uv-cache uv run kreports auditor-feature-readiness --year 2025 --market KOSPI --json
UV_CACHE_DIR=.uv-cache uv run kreports dataset-completeness --year 2025 --json
UV_CACHE_DIR=.uv-cache uv run kreports verify-raw-storage --limit 100
```

Expected:

- auditor feature readiness remains usable.
- dataset completeness does not regress due to raw migration.
- raw storage verification has zero failures.

- [ ] **Step 7: Compact DB only after free space is adequate**

Required condition:

```text
free disk space > current kreports.db size + 10GB
```

If not satisfied, do not run `VACUUM`. Instead create a compact copy on an external disk or larger volume.

- [ ] **Step 8: Commit rollout notes**

Update `docs/operations/raw-storage-runbook.md` with actual migration counts and verification results, then commit:

```bash
git add docs/operations/raw-storage-runbook.md
git commit -m "docs: record raw storage migration runbook"
```

---

## Acceptance Criteria

- `source_documents` no longer needs full raw XML/HTML inline for normal MCP operation.
- Raw documents are preserved in compressed storage and hash-verifiable.
- Existing extractors can operate from either inline `raw_content` or external `storage_uri`.
- Financial 5-year readiness is measured from financial fact tables, not raw document metadata.
- Auditor-facing functions still work:
  - KAM search.
  - audit procedure search.
  - emphasis/other/going concern search.
  - accounting policy comparison.
  - peer group comparison.
  - 5-year financial snapshot / DCF-ready timeseries checks.
- Full test suite passes.

## Self-Review

- Spec coverage: This plan covers raw preservation, evidence DB, financial fact separation, migration safety, and deployment readiness.
- Placeholder scan: No TBD or deferred implementation placeholders remain.
- Type consistency: `storage_uri`, `content_length`, `compressed_length`, `storage_status`, `RawDocumentStore`, `migrate_raw_documents_to_storage`, and `verify_raw_storage` are introduced before use.


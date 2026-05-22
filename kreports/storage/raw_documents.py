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
        safe_rcept_no = "".join(
            ch if ch.isalnum() or ch in ("_", "-") else "_"
            for ch in rcept_no
        )
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
        if not path.exists():
            raise FileNotFoundError(f"missing raw document: {path}")
        with gzip.open(path, "rb") as fh:
            content = fh.read().decode("utf-8")
        if expected_hash and sha1_text(content) != expected_hash:
            raise ValueError("raw document hash mismatch")
        return content

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


def _suffix_for_content_type(content_type: str) -> str:
    if content_type == "html":
        return "html"
    if content_type == "pdf_text":
        return "txt"
    return "xml"


class RawDocumentStore:
    def __init__(
        self,
        base_dir: str | Path = "data/raw_documents",
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

    def _path_for(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        source_type: str,
        rcept_no: str,
        content_type: str,
    ) -> Path:
        suffix = _suffix_for_content_type(content_type)
        safe_rcept_no = "".join(
            ch if ch.isalnum() or ch in ("_", "-") else "_"
            for ch in rcept_no
        )
        return self.base_dir / str(bsns_year) / source_type / corp_code / f"{safe_rcept_no}.{suffix}.gz"

    def _object_name_for(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        source_type: str,
        rcept_no: str,
        content_type: str,
    ) -> str:
        suffix = _suffix_for_content_type(content_type)
        safe_rcept_no = "".join(
            ch if ch.isalnum() or ch in ("_", "-") else "_"
            for ch in rcept_no
        )
        parts = [
            part
            for part in (
                self.prefix,
                str(bsns_year),
                source_type,
                corp_code,
                f"{safe_rcept_no}.{suffix}.gz",
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
                "google-cloud-storage is required for gs:// raw storage. "
                "Install with: pip install 'kreports[gcs]'"
            ) from exc
        self.gcs_client = storage.Client()
        return self.gcs_client

    def _write_file(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        source_type: str,
        rcept_no: str,
        content_type: str,
        data: bytes,
        doc_hash: str,
    ) -> StoredRawDocument:
        path = self._path_for(
            corp_code=corp_code,
            bsns_year=int(bsns_year),
            source_type=source_type,
            rcept_no=rcept_no,
            content_type=content_type,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wb") as fh:
            fh.write(data)
        return StoredRawDocument(
            storage_uri=f"file://{path.resolve()}",
            path=str(path),
            doc_hash=doc_hash,
            content_length=len(data),
            compressed_length=path.stat().st_size,
        )

    def _write_gcs(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        source_type: str,
        rcept_no: str,
        content_type: str,
        data: bytes,
        doc_hash: str,
    ) -> StoredRawDocument:
        if not self.bucket:
            raise ValueError("bucket is required when backend='gcs'")
        object_name = self._object_name_for(
            corp_code=corp_code,
            bsns_year=int(bsns_year),
            source_type=source_type,
            rcept_no=rcept_no,
            content_type=content_type,
        )
        compressed = gzip.compress(data)
        client = self._get_gcs_client()
        blob = client.bucket(self.bucket).blob(object_name)
        blob.upload_from_string(compressed, content_type="application/gzip")
        return StoredRawDocument(
            storage_uri=f"gs://{self.bucket}/{object_name}",
            path=object_name,
            doc_hash=doc_hash,
            content_length=len(data),
            compressed_length=len(compressed),
        )

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
        from kreports.runtime import raw_persistence_allowed

        if not raw_persistence_allowed(backend=self.backend, bucket=self.bucket):
            raise RuntimeError(
                "raw persistence requires collector mode, explicit raw opt-in, "
                "external non-inline storage, and a GCS bucket when applicable."
            )
        data = (content or "").encode("utf-8")
        doc_hash = sha1_text(content)
        if self.backend == "file":
            return self._write_file(
                corp_code=corp_code,
                bsns_year=int(bsns_year),
                source_type=source_type,
                rcept_no=rcept_no,
                content_type=content_type,
                data=data,
                doc_hash=doc_hash,
            )
        if self.backend == "gcs":
            return self._write_gcs(
                corp_code=corp_code,
                bsns_year=int(bsns_year),
                source_type=source_type,
                rcept_no=rcept_no,
                content_type=content_type,
                data=data,
                doc_hash=doc_hash,
            )
        raise ValueError(f"unsupported raw storage backend: {self.backend}")

    def _read_file(self, storage_uri: str) -> str:
        parsed = urlparse(storage_uri)
        path = Path(parsed.path)
        if not path.exists():
            raise FileNotFoundError(f"missing raw document: {path}")
        with gzip.open(path, "rb") as fh:
            return fh.read().decode("utf-8")

    def _read_gcs(self, storage_uri: str) -> str:
        parsed = urlparse(storage_uri)
        bucket_name = parsed.netloc
        object_name = parsed.path.lstrip("/")
        if not bucket_name or not object_name:
            raise ValueError(f"invalid gs storage_uri: {storage_uri}")
        client = self._get_gcs_client()
        blob = client.bucket(bucket_name).blob(object_name)
        compressed = blob.download_as_bytes()
        return gzip.decompress(compressed).decode("utf-8")

    def read(self, storage_uri: str, *, expected_hash: str | None = None) -> str:
        parsed = urlparse(storage_uri)
        if parsed.scheme == "file":
            content = self._read_file(storage_uri)
        elif parsed.scheme == "gs":
            content = self._read_gcs(storage_uri)
        else:
            raise ValueError(f"unsupported storage_uri scheme: {parsed.scheme}")
        if expected_hash and sha1_text(content) != expected_hash:
            raise ValueError("raw document hash mismatch")
        return content

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

    def _object_name(
        self,
        *,
        table_name: str,
        row_id: int,
        corp_code: str,
        bsns_year: int,
    ) -> str:
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

    def write(
        self,
        *,
        table_name: str,
        row_id: int,
        corp_code: str,
        bsns_year: int,
        content: str,
    ) -> StoredEvidenceBlob:
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

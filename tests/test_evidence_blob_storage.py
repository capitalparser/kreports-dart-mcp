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

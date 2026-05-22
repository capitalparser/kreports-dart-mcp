from sqlalchemy import inspect


def test_source_documents_has_raw_storage_columns(temp_engine):
    inspector = inspect(temp_engine)
    columns = {column["name"] for column in inspector.get_columns("source_documents")}

    assert "storage_uri" in columns
    assert "content_length" in columns
    assert "compressed_length" in columns
    assert "storage_status" in columns

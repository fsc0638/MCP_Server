from pathlib import Path
from datetime import datetime, timedelta

from server.services.user_document_service import ExpiredDocumentError, UserDocumentService


def test_user_document_service_lifecycle(tmp_path: Path):
    service = UserDocumentService(root_dir=tmp_path)

    created = service.create_document(
        user_key="tester",
        raw_user_id="tester",
        filename="notes.txt",
        content="hello document center".encode("utf-8"),
    )

    assert created["extension"] == ".txt"
    assert created["text_extract_status"] == "pending"
    assert created["stored_filename"] == "notes.txt"
    assert (tmp_path / "tester" / "notes.txt").exists()

    docs = service.list_documents("tester")
    assert len(docs) == 1
    assert docs[0]["display_name"] == "notes.txt"

    updated = service.ensure_text_cache("tester", created["doc_id"])
    assert updated["text_extract_status"] == "ready"

    document, text = service.get_text_content("tester", created["doc_id"])
    assert document["doc_id"] == created["doc_id"]
    assert text == "hello document center"

    renamed = service.rename_document("tester", created["doc_id"], "meeting-notes")
    assert renamed["display_name"] == "meeting-notes.txt"

    service.delete_document("tester", created["doc_id"])
    assert service.list_documents("tester") == []


def test_user_document_service_preserves_original_filename_with_suffix_for_duplicates(tmp_path: Path):
    service = UserDocumentService(root_dir=tmp_path)

    first = service.create_document(
        user_key="tester",
        raw_user_id="tester",
        filename="互動牆.pdf",
        content=b"first version",
    )
    second = service.create_document(
        user_key="tester",
        raw_user_id="tester",
        filename="互動牆.pdf",
        content=b"second version",
    )

    assert first["stored_filename"] == "互動牆.pdf"
    assert second["stored_filename"] == "互動牆_2.pdf"
    assert (tmp_path / "tester" / "互動牆.pdf").read_bytes() == b"first version"
    assert (tmp_path / "tester" / "互動牆_2.pdf").read_bytes() == b"second version"


def test_user_document_service_expires_and_cleans_up(tmp_path: Path):
    service = UserDocumentService(root_dir=tmp_path, ttl_days=1)

    created = service.create_document(
        user_key="tester",
        raw_user_id="tester",
        filename="notes.txt",
        content="hello document center".encode("utf-8"),
    )

    manifest = service._load_manifest("tester")
    manifest["documents"][0]["expires_at"] = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    service._save_manifest("tester", manifest)

    assert service.list_documents("tester") == []

    try:
        service.get_document("tester", created["doc_id"])
        assert False, "Expected document to be expired"
    except ExpiredDocumentError:
        pass

    summary = service.cleanup_expired_documents()
    assert summary["removed_documents"] == 1

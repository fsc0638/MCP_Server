from pathlib import Path

from server.services.user_document_service import UserDocumentService


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

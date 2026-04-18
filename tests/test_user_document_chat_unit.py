from server.services.user_document_chat import resolve_document_turn


DOCS = [
    {"doc_id": "doc_a", "display_name": "meeting-notes.docx", "original_filename": "meeting-notes.docx"},
    {"doc_id": "doc_b", "display_name": "proposal.pdf", "original_filename": "proposal.pdf"},
]


def test_resolve_document_turn_lists_documents():
    result = resolve_document_turn("目前有哪些文件可以看", DOCS)
    assert result is not None
    assert result["action"] == "list"
    assert "meeting-notes.docx" in result["message"]


def test_resolve_document_turn_prompts_for_display_choice():
    result = resolve_document_turn("我想看 proposal.pdf", DOCS)
    assert result is not None
    assert result["action"] == "prompt_choice"
    assert result["document"]["doc_id"] == "doc_b"


def test_resolve_document_turn_handles_pending_display_choice():
    result = resolve_document_turn("文字", DOCS, pending_document=DOCS[1])
    assert result is not None
    assert result["action"] == "show_text"
    assert result["document"]["doc_id"] == "doc_b"

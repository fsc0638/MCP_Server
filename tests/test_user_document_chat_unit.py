from server.services.user_document_chat import (
    detect_requested_processing_action,
    resolve_document_task_request,
    resolve_document_turn,
)


DOCS = [
    {"doc_id": "doc_a", "display_name": "meeting-notes.docx", "original_filename": "meeting-notes.docx"},
    {"doc_id": "doc_b", "display_name": "proposal.pdf", "original_filename": "proposal.pdf"},
]

ZH_DOCS = [
    {"doc_id": "doc_c", "display_name": "保險費送金單.pdf", "original_filename": "保險費送金單.pdf"},
    {"doc_id": "doc_d", "display_name": "高階執行長特助職務說明.docx", "original_filename": "高階執行長特助職務說明.docx"},
]


def test_resolve_document_turn_lists_documents():
    result = resolve_document_turn("目前有哪些文件可以看", DOCS)
    assert result is not None
    assert result["action"] == "list"
    assert "meeting-notes.docx" in result["message"]
    assert result["set_pending_candidates"] == DOCS[:8]


def test_resolve_document_turn_lists_document_center_files():
    result = resolve_document_turn("幫我列出文件中心的檔案", DOCS)
    assert result is not None
    assert result["action"] == "list"
    assert "proposal.pdf" in result["message"]


def test_resolve_document_turn_prompts_for_display_choice():
    result = resolve_document_turn("我想看 proposal.pdf", DOCS)
    assert result is not None
    assert result["action"] == "prompt_choice"
    assert result["document"]["doc_id"] == "doc_b"


def test_resolve_document_turn_opens_preview_for_explicit_preview_request():
    result = resolve_document_turn("我要預覽 proposal.pdf", DOCS)
    assert result is not None
    assert result["action"] == "show_preview"
    assert result["document"]["doc_id"] == "doc_b"


def test_resolve_document_turn_opens_preview_for_explicit_open_request():
    result = resolve_document_turn("幫我開啟 proposal.pdf", DOCS)
    assert result is not None
    assert result["action"] == "show_preview"
    assert result["document"]["doc_id"] == "doc_b"


def test_resolve_document_turn_handles_pending_display_choice():
    result = resolve_document_turn("文字", DOCS, pending_document=DOCS[1])
    assert result is not None
    assert result["action"] == "show_text"
    assert result["document"]["doc_id"] == "doc_b"


def test_resolve_document_turn_matches_chinese_open_request_without_extension():
    result = resolve_document_turn("幫我開啟保險費送金單", ZH_DOCS)
    assert result is not None
    assert result["action"] == "show_preview"
    assert result["document"]["doc_id"] == "doc_c"


def test_resolve_document_turn_treats_bare_filename_as_document_reference():
    result = resolve_document_turn("保險費送金單", ZH_DOCS)
    assert result is not None
    assert result["action"] == "prompt_choice"
    assert result["document"]["doc_id"] == "doc_c"


def test_resolve_document_turn_allows_numeric_reply_after_list():
    result = resolve_document_turn("1.", ZH_DOCS, pending_candidates=ZH_DOCS)
    assert result is not None
    assert result["action"] == "prompt_choice"
    assert result["document"]["doc_id"] == "doc_c"


def test_detect_requested_processing_action_for_todo_request():
    result = detect_requested_processing_action(
        "幫我將【01 【2026第578次經營管理會議紀錄】_Max_0420.docx】轉換成todo list，但是先不要上傳notion"
    )
    assert result == "todo"


def test_resolve_document_task_request_binds_named_doc_for_todo_conversion():
    docs = [
        {
            "doc_id": "doc_minutes",
            "display_name": "01 【2026第578次經營管理會議紀錄】_Max_0420.docx",
            "original_filename": "01 【2026第578次經營管理會議紀錄】_Max_0420.docx",
        }
    ]
    result = resolve_document_task_request(
        "幫我將【01 【2026第578次經營管理會議紀錄】_Max_0420.docx】轉換成todo list，但是先不要上傳notion",
        docs,
    )
    assert result is not None
    assert result["action"] == "todo"
    assert result["document"]["doc_id"] == "doc_minutes"

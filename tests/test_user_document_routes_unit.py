from server.routes.user_documents import _build_content_disposition


def test_content_disposition_supports_unicode_filename():
    header = _build_content_disposition("inline", "互動牆.pdf")

    assert header.startswith("inline; ")
    assert 'filename="document.pdf"' in header
    assert "filename*=UTF-8''%E4%BA%92%E5%8B%95%E7%89%86.pdf" in header
    assert header.encode("latin-1")


def test_content_disposition_preserves_ascii_filename():
    header = _build_content_disposition("attachment", "report.pdf")

    assert header.startswith("attachment; ")
    assert 'filename="report.pdf"' in header
    assert "filename*=UTF-8''report.pdf" in header

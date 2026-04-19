"""DOCX-to-HTML preview helpers for the user document center."""

from __future__ import annotations

import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Iterator


def iter_block_items(parent: Any) -> Iterator[Any]:
    """Yield paragraphs and tables in document order."""
    from docx.document import Document as DocumentType
    from docx.oxml.table import CT_Tbl
    from docx.oxml.text.paragraph import CT_P
    from docx.table import Table, _Cell
    from docx.text.paragraph import Paragraph

    if isinstance(parent, DocumentType):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise TypeError(f"Unsupported parent type: {type(parent)!r}")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def render_docx_to_html(docx_path: str) -> str:
    """Convert a DOCX file into a safe HTML fragment for preview."""
    from docx import Document
    from docx.text.paragraph import Paragraph

    doc = Document(docx_path)
    parts: list[str] = []
    current_list_type: str | None = None

    def close_list() -> None:
        nonlocal current_list_type
        if current_list_type:
            parts.append(f"</{current_list_type}>")
            current_list_type = None

    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            text_html = _render_paragraph_runs(block).strip()
            list_type = _get_list_type(block)

            if list_type:
                if current_list_type != list_type:
                    close_list()
                    current_list_type = list_type
                    parts.append(f"<{list_type}>")
                if text_html:
                    parts.append(f"<li>{text_html}</li>")
                continue

            close_list()

            if not text_html:
                parts.append('<div class="docx-preview-spacer" aria-hidden="true"></div>')
                continue

            tag, css_class = _get_block_tag(block)
            if css_class:
                parts.append(f'<{tag} class="{css_class}">{text_html}</{tag}>')
            else:
                parts.append(f"<{tag}>{text_html}</{tag}>")
            continue

        close_list()
        table_html = _render_table(block)
        if table_html:
            parts.append(table_html)

    close_list()

    if not parts:
        parts.append("<p>這份文件目前沒有可顯示的內容。</p>")

    return '<article class="docx-preview-fragment">' + "".join(parts) + "</article>"


def convert_docx_to_pdf(docx_path: str, pdf_path: str) -> None:
    """Convert a DOCX file to PDF for inline preview.

    Conversion order:
    1. `docx2pdf` (best on Windows with Word installed)
    2. `soffice --headless` if LibreOffice is available
    """
    source = Path(docx_path).resolve()
    target = Path(pdf_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    try:
        _convert_via_docx2pdf(source, target)
        return
    except Exception as exc:
        errors.append(str(exc))

    try:
        _convert_via_soffice(source, target)
        return
    except Exception as exc:
        errors.append(str(exc))

    joined = "；".join(err for err in errors if err) or "沒有可用的 DOCX 轉 PDF 轉換器。"
    raise RuntimeError(joined)


def _get_block_tag(paragraph: Any) -> tuple[str, str | None]:
    style_name = ((paragraph.style.name if paragraph.style else "") or "").strip()
    normalized = style_name.lower()

    if normalized == "title":
        return "h1", "docx-preview-title"
    if normalized == "subtitle":
        return "p", "docx-preview-subtitle"
    if normalized.startswith("heading"):
        match = re.search(r"(\d+)", normalized)
        level = 1
        if match:
            level = min(max(int(match.group(1)), 1), 6)
        return f"h{level}", None

    return "p", None


def _get_list_type(paragraph: Any) -> str | None:
    style_name = ((paragraph.style.name if paragraph.style else "") or "").strip().lower()
    p_pr = getattr(paragraph._p, "pPr", None)
    has_numbering = bool(p_pr is not None and getattr(p_pr, "numPr", None) is not None)

    if "list bullet" in style_name or "bullet" in style_name:
        return "ul"
    if "list number" in style_name or "number" in style_name:
        return "ol"
    if has_numbering:
        return "ol" if "number" in style_name else "ul"
    return None


def _render_paragraph_runs(paragraph: Any) -> str:
    chunks: list[str] = []
    for run in paragraph.runs:
        text = _escape_run_text(run.text)
        if not text:
            continue
        if run.bold:
            text = f"<strong>{text}</strong>"
        if run.italic:
            text = f"<em>{text}</em>"
        if run.underline:
            text = f"<u>{text}</u>"
        chunks.append(text)

    if chunks:
        return "".join(chunks)
    return _escape_run_text(paragraph.text)


def _escape_run_text(value: str) -> str:
    if not value:
        return ""
    escaped = html.escape(value)
    escaped = escaped.replace("\t", "&emsp;")
    return escaped.replace("\n", "<br>")


def _convert_via_docx2pdf(source: Path, target: Path) -> None:
    try:
        from docx2pdf import convert as convert_docx2pdf
    except ModuleNotFoundError as exc:
        raise RuntimeError("docx2pdf 未安裝，無法使用 Word 轉檔。") from exc

    temp_target = target.with_name(target.stem + ".build.pdf")
    temp_target.unlink(missing_ok=True)

    try:
        convert_docx2pdf(str(source), str(temp_target))
    except Exception as exc:
        temp_target.unlink(missing_ok=True)
        raise RuntimeError(f"docx2pdf 轉換失敗：{exc}") from exc

    if not temp_target.exists():
        raise RuntimeError("docx2pdf 沒有產生 PDF 檔案。")

    temp_target.replace(target)


def _convert_via_soffice(source: Path, target: Path) -> None:
    soffice = shutil.which("soffice")
    if not soffice:
        raise RuntimeError("找不到 LibreOffice soffice，無法使用 headless 轉檔。")

    with tempfile.TemporaryDirectory(prefix="docx-pdf-preview-") as temp_dir:
        temp_path = Path(temp_dir)
        cmd = [
            soffice,
            "--headless",
            "--convert-to",
            "pdf:writer_pdf_Export",
            "--outdir",
            str(temp_path),
            str(source),
        ]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=180)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("LibreOffice 轉檔逾時。") from exc
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise RuntimeError(f"LibreOffice 轉檔失敗：{stderr or exc}") from exc

        produced = temp_path / (source.stem + ".pdf")
        if not produced.exists():
            raise RuntimeError("LibreOffice 沒有產生 PDF 檔案。")

        shutil.move(str(produced), str(target))


def _render_table(table: Any) -> str:
    rows = list(table.rows)
    if not rows:
        return ""

    rendered_rows = [[_render_table_cell(cell) for cell in row.cells] for row in rows]
    if not any(any(cell.strip() for cell in row) for row in rendered_rows):
        return ""

    head_cells = "".join(f"<th>{cell or '&nbsp;'}</th>" for cell in rendered_rows[0])
    body_rows = rendered_rows[1:] if len(rendered_rows) > 1 else []
    body_html = "".join(
        "<tr>" + "".join(f"<td>{cell or '&nbsp;'}</td>" for cell in row) + "</tr>"
        for row in body_rows
    )

    table_parts = [
        '<div class="docx-preview-table-wrap"><table class="docx-preview-table">',
        f"<thead><tr>{head_cells}</tr></thead>",
    ]
    if body_html:
        table_parts.append(f"<tbody>{body_html}</tbody>")
    table_parts.append("</table></div>")
    return "".join(table_parts)


def _render_table_cell(cell: Any) -> str:
    chunks: list[str] = []
    for block in iter_block_items(cell):
        if isinstance(block, Paragraph):
            html_text = _render_paragraph_runs(block).strip()
            if html_text:
                chunks.append(html_text)
        else:
            nested = _render_table(block)
            if nested:
                chunks.append(nested)
    return "<br>".join(chunks)

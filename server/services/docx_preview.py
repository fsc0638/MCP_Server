"""DOCX-to-HTML preview helpers for the user document center."""

from __future__ import annotations

import html
import re
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

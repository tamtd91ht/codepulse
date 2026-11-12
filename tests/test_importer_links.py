"""Links survive import (docs/specs/29 §16): .docx hyperlinks, .xlsx linked cells, <a href> of a
page, Google Docs / Sheets read through their export."""

import io
import zipfile

from openpyxl import Workbook

from app.assistant import importers

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def _docx() -> bytes:
    doc = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document {W} {R}><w:body>'
        '<w:p><w:r><w:t xml:space="preserve">Xem </w:t></w:r>'
        '<w:hyperlink r:id="rId9"><w:r><w:t>hướng dẫn [API]</w:t></w:r></w:hyperlink></w:p>'
        "<w:p><w:fldSimple w:instr=' HYPERLINK \"https://b.example/x\" '>"
        "<w:r><w:t>B</w:t></w:r></w:fldSimple></w:p>"
        '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
        '<w:r><w:instrText> HYPERLINK "https://c.example/" </w:instrText></w:r>'
        '<w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>C</w:t></w:r>'
        '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>'
        "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Tên</w:t></w:r></w:p></w:tc></w:tr>"
        '<w:tr><w:tc><w:p><w:hyperlink r:id="rId9"><w:r><w:t>Ô</w:t></w:r></w:hyperlink>'
        "</w:p></w:tc></w:tr></w:tbl></w:body></w:document>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships '
        'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId9" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
        'relationships/hyperlink" Target="https://www.google.com/url?q=https://a.example/api'
        '&amp;sa=D" TargetMode="External"/></Relationships>'
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", doc)
        z.writestr("word/_rels/document.xml.rels", rels)
    return out.getvalue()


def test_docx_hyperlinks_become_markdown_links() -> None:
    text = importers.from_file("a.docx", _docx())
    assert "Xem [hướng dẫn \\[API\\]](https://a.example/api)" in text  # Google wrapper removed
    assert "[B](https://b.example/x)" in text
    assert "[C](https://c.example/)" in text
    assert "| [Ô](https://a.example/api) |" in text


def test_xlsx_linked_cells_become_links() -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Repo"
    sheet.append(["Tên", "Link"])
    sheet.append(["payment-api", "Mở"])
    sheet["B2"].hyperlink = "https://gitlab.example/pay"
    sheet["A3"] = "x | y"
    buf = io.BytesIO()
    book.save(buf)
    text = importers.from_file("a.xlsx", buf.getvalue())
    assert "## Repo" in text and "[Mở](https://gitlab.example/pay)" in text
    assert "x \\| y" in text


def test_html_links_and_google_export() -> None:
    page = importers._Html("https://wiki.example/docs/a")
    page.feed(
        '<p>Xem <a href="../b">trang B</a> và <a href="javascript:x()">bỏ</a></p>'
        '<table><tr><th>Tên</th></tr><tr><td><a href="https://c.example">C</a></td></tr></table>'
    )
    md = page.markdown()
    assert "[trang B](https://wiki.example/b)" in md and "javascript" not in md
    assert "| [C](https://c.example) |" in md
    assert importers.google_export(
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUv/edit#gid=0"
    ) == (
        "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUv/export?format=xlsx",
        ".xlsx",
    )
    assert (
        importers.google_export("https://docs.google.com/document/d/1AbCdEfGhIjKlMnOpQrStUv/edit")[
            1
        ]
        == ".docx"
    )  # type: ignore[index]
    assert importers.google_export("https://example.com/document/d/x") is None

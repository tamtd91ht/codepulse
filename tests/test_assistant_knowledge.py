"""Stetho's knowledge base (docs/specs/28): converting files and links, splitting, finding."""

import io
import re
import zipfile
from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.assistant import howto, importers, knowledge
from app.assistant.chat import build_prompt, command
from app.models import AssistantDoc

GUIDE = Path(__file__).parents[1] / "dashboards/web/app/guide/jira/page.tsx"


def docx(*paragraphs: tuple[str | None, str], table: list[list[str]] | None = None) -> bytes:
    """A minimal .docx: (style, text) paragraphs, and a table."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    parts = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        parts.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    if table:
        rows = "".join(
            "<w:tr>"
            + "".join(f"<w:tc><w:p><w:r><w:t>{c}</w:t></w:r></w:p></w:tc>" for c in r)
            + "</w:tr>"
            for r in table
        )
        parts.append(f"<w:tbl>{rows}</w:tbl>")
    body = "".join(parts)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{w}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


def test_files_become_markdown() -> None:
    word = docx(
        ("Heading1", "Quy trình release"),
        (None, "Mỗi release cần tag."),
        ("ListParagraph", "Chạy test"),
        table=[["Bước", "Ai"], ["Duyệt", "Lead"]],
    )
    text = importers.from_file("quy-trinh.docx", word)
    assert text.startswith("# Quy trình release")
    assert "- Chạy test" in text and "| Bước | Ai |" in text and "| Duyệt | Lead |" in text

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Checklist"
    sheet.append(["Mục", "Bắt buộc"])
    sheet.append(["Có test", "Có"])
    buf = io.BytesIO()
    book.save(buf)
    sheet_text = importers.from_file("checklist.xlsx", buf.getvalue())
    assert sheet_text.startswith("## Checklist") and "| Có test | Có |" in sheet_text

    assert importers.from_file("a.md", "# Tiêu đề\n\nnội dung".encode()) == "# Tiêu đề\n\nnội dung"
    assert importers.from_file("a.txt", "café crème".encode("cp1252")) == "café crème"
    with pytest.raises(importers.ImportError_):
        importers.from_file("a.pdf", b"%PDF")
    with pytest.raises(importers.ImportError_):
        importers.from_file("a.docx", b"not a zip")


def test_a_word_file_with_a_dtd_is_refused() -> None:
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]><w:document/>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", evil)
    with pytest.raises(importers.ImportError_):
        importers.from_file("x.docx", buf.getvalue())


def test_html_becomes_markdown_without_navigation() -> None:
    parser = importers._Html()
    parser.feed(
        "<html><head><title>Wiki</title><script>x()</script></head><body><nav>Menu</nav>"
        "<h2>Cài đặt</h2><p>Chạy   lệnh sau:</p><pre>make up\nmake test</pre>"
        "<ul><li>Một</li><li>Hai</li></ul><table><tr><th>A</th><th>B</th></tr>"
        "<tr><td>1</td><td>2</td></tr></table></body></html>"
    )
    md = parser.markdown()
    assert parser.title == "Wiki" and "Menu" not in md and "x()" not in md
    assert "## Cài đặt" in md and "Chạy lệnh sau:" in md and "make up\nmake test" in md
    assert "- Một" in md and "| A | B |" in md


@pytest.mark.parametrize(
    ("address", "host", "allowed", "ok"),
    [
        ("93.184.216.34", "example.com", (), True),
        ("127.0.0.1", "x", (), False),
        ("::ffff:127.0.0.1", "x", (), False),
        ("169.254.169.254", "x", (), False),  # cloud metadata
        ("100.100.100.200", "x", (), False),  # carrier-grade NAT (Alibaba metadata)
        ("fd00:ec2::254", "x", (), False),  # AWS IPv6 metadata
        ("172.17.0.1", "wiki.congty.vn", ("congty.vn",), False),  # Docker bridge, even listed
        ("10.0.0.5", "wiki.congty.vn", (), False),  # intranet: only when listed
        ("10.0.0.5", "wiki.congty.vn", ("congty.vn",), True),
        ("10.0.0.5", "evilcongty.vn", ("congty.vn",), False),
    ],
)
def test_which_addresses_a_link_may_reach(
    address: str, host: str, allowed: tuple[str, ...], ok: bool
) -> None:
    if ok:
        importers.check_ip(address, host, allowed)
    else:
        with pytest.raises(importers.ImportError_):
            importers.check_ip(address, host, allowed)


@pytest.mark.parametrize(
    "url",
    ["ftp://a/b", "http://h:99999/", "http://h:22/", "http://user:pw@h/", "file:///etc/passwd"],
)
def test_links_must_be_plain_web_links(url: str) -> None:
    with pytest.raises(importers.ImportError_):
        importers._reachable(url)


def test_the_check_is_made_on_the_address_connected_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding: whatever the name resolved to before, the connection checks its own
    answer (and no proxy from the environment is used)."""
    import socket

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, 0, 0, "", ("127.0.0.1", 80))]
    )
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    with pytest.raises(importers.ImportError_):
        importers.from_url("http://looks-public.example/page")


def test_the_link_shown_has_no_query_string() -> None:
    url = "https://wiki.example/page?token=abc&x=1"
    assert importers.safe_source(url) == "https://wiki.example/page"


def test_zip_bombs_and_non_utf8_word_files_are_refused() -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/sharedStrings.xml", "a" * (20 * 1024 * 1024))  # ~20 KB compressed
    with pytest.raises(importers.ImportError_):
        importers.from_file("bomb.xlsx", buf.getvalue())
    utf16 = '<?xml version="1.0" encoding="UTF-16"?><w:document/>'.encode("utf-16")
    word = io.BytesIO()
    with zipfile.ZipFile(word, "w") as z:
        z.writestr("word/document.xml", utf16)
    with pytest.raises(importers.ImportError_):
        importers.from_file("x.docx", word.getvalue())


def test_a_big_table_is_split_by_rows_with_its_header() -> None:
    rows = "\n".join(f"| {i} | dòng {i} |" for i in range(2000))
    pieces = knowledge.split(f"# Bảng\n\n| Số | Tên |\n|---|---|\n{rows}", size=1000)
    assert len(pieces) > 10
    assert all(p.body.startswith("| Số | Tên |\n|---|---|") for p in pieces)
    assert all(len(p.body) < 2500 for p in pieces)


def test_split_keeps_headings_and_code_blocks() -> None:
    body = (
        "# A\n\nmở đầu\n\n## B\n\n"
        + ("đoạn dài. " * 200)
        + "\n\n```sh\nmột\n\nhai\n```\n\n### C\n\ncuối"
    )
    pieces = knowledge.split(body, size=300)
    assert pieces[0].heading == "A" and pieces[0].body == "mở đầu"
    assert any(p.heading == "A > B" and p.body.startswith("```sh") for p in pieces)
    assert pieces[-1].heading == "A > B > C" and pieces[-1].body == "cuối"


def test_terms_fold_accents_and_drop_filler() -> None:
    assert knowledge.terms("Làm sao để commit sửa bug Jira?") == ["commit", "sua", "bug", "jira"]
    assert knowledge.fold("Đồng bộ") == "dong bo"


def test_the_hook_and_the_ci_job_match_the_guide_page() -> None:
    page = GUIDE.read_text(encoding="utf-8")
    hook = re.search(r"const HOOK = String\.raw`(.*?)`;", page, re.S)
    ci = re.search(r"const CI = String\.raw`(.*?)`;", page, re.S)
    assert hook and ci
    assert hook.group(1) == howto.HOOK and ci.group(1) == howto.CI


def test_the_prompt_carries_the_documents_and_web_is_opt_in() -> None:
    prompt = build_prompt("ctx", [], "hỏi", "[1] Tài liệu\nnội dung")
    assert prompt.startswith("<tai_lieu>\n[1] Tài liệu") and "<ngu_canh>\nctx" in prompt
    plain = command("claude", "sonnet")
    assert plain[plain.index("--tools") + 1] == "" and "WebSearch" not in " ".join(plain)
    web = command("claude", "sonnet", web=True)
    # WebSearch only: WebFetch would fetch from this server (intranet, metadata) on request.
    assert web[web.index("--tools") + 1] == "WebSearch"
    assert web[web.index("--allowedTools") + 1] == "WebSearch" and "WebFetch" not in " ".join(web)
    assert "TUYỆT ĐỐI không đưa dữ liệu nội bộ" in web[web.index("--system-prompt") + 1]


@pytest.mark.integration
def test_search_finds_the_right_pieces(db_session: Session) -> None:
    assert knowledge.sync_builtin(db_session) == len(howto.TOPICS)
    assert knowledge.sync_builtin(db_session) == 0  # unchanged: nothing rewritten
    hits = knowledge.search(db_session, "làm sao để commit sửa bug liên kết với issue jira")
    assert any(h.title.startswith("Chuẩn liên kết Jira") for h in hits[:2])
    assert any("Fixes: CRM-415" in h.body for h in hits)

    secret = AssistantDoc(
        title="Quy định lương thưởng",
        body="Thưởng dự án tính theo quý.",
        source="manual",
        audience="trainers",
        enabled=True,
        paths=[],
        keywords=[],
    )
    off = AssistantDoc(
        title="Quy trình cũ release",
        body="Release thứ sáu.",
        source="manual",
        audience="all",
        enabled=False,
        paths=[],
        keywords=[],
    )
    knowledge.save(db_session, secret)
    knowledge.save(db_session, off)
    titles = [h.title for h in knowledge.search(db_session, "thưởng dự án theo quý")]
    assert "Quy định lương thưởng" not in titles  # trainers only
    trained = knowledge.search(db_session, "thưởng dự án theo quý", trainer=True)
    assert trained[0].title == "Quy định lương thưởng"
    off_titles = [h.title for h in knowledge.search(db_session, "release thứ sáu", trainer=True)]
    assert "Quy trình cũ release" not in off_titles  # switched off
    # The screen on view breaks ties toward its documents.
    on_screen = knowledge.search(db_session, "tài khoản claude dự phòng", path="/admin")
    assert on_screen and on_screen[0].title.startswith("Nhiều tài khoản Claude")

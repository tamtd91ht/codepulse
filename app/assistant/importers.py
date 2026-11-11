"""Turning what a trainer gives Stetho into Markdown text (docs/specs/28 §2).

Files: .md / .markdown / .txt (UTF-8, else Windows-1258/1252 tried), .docx (paragraphs with their
heading levels, lists and tables, read from word/document.xml with the standard library: no
macro, no embedded object is ever opened), .doc (Word 97-2003: the main text through the piece
table, app/assistant/worddoc.py), .xlsx (each sheet as a Markdown table, values only, never
formulas run), .pdf (the text layer of each page with pypdf, at most MAX_PDF_PAGES pages; a
scanned PDF without text is refused: there is no OCR). Zip-based files are checked for size and
compression ratio before they are opened (zip bombs), and Word XML must be UTF-8 without a DTD
(entity expansion).

A web link (`from_url`): http/https on a web port, 2 MB, 30 s in all, redirects followed by
hand. The address is checked when the connection is made, on the very IP connected to (a name
that resolves to something else the second time — DNS rebinding — gains nothing), with no proxy
from the environment. Never: this machine, link-local / cloud metadata, carrier-grade NAT,
Docker's bridge; private (RFC 1918 / ULA) addresses only for the intranet hosts an admin listed
in ASSISTANT_URL_HOSTS. Its HTML is reduced to headings, paragraphs, lists, tables and code.

Only the text is kept; the file itself is not stored.
"""

import io
import ipaddress
import re
import socket
import struct
import time
import zipfile
from collections.abc import Iterable
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpcore
import httpx

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_URL_BYTES = 2 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
URL_TIMEOUT_S = 15.0  # each connect / read
URL_DEADLINE_S = 30.0  # the whole fetch, redirects included
URL_PORTS = frozenset({80, 443, 8080, 8443})
# Uncompressed size of all members of an uploaded .docx / .xlsx, and the worst ratio allowed.
MAX_UNZIPPED_BYTES = 100 * 1024 * 1024
MAX_ZIP_RATIO = 100
MAX_WORD_XML_BYTES = 20 * 1024 * 1024
MAX_SHEET_ROWS = 2_000
MAX_SHEET_COLS = 30
MAX_PDF_PAGES = 500
# What Stetho takes (docs/specs/28 §2).
FILE_KINDS = (".md", ".markdown", ".txt", ".doc", ".docx", ".xlsx")
# Review rules imported as files (docs/specs/17 §Tệp quy tắc, user 2026-11-11).
RULE_FILE_KINDS = (".md", ".markdown", ".txt", ".doc", ".docx")
# Project documents (docs/specs/29): the original is kept, its text indexed and rendered.
DOCUMENT_KINDS = (".md", ".markdown", ".txt", ".doc", ".docx", ".xlsx", ".pdf")


class ImportError_(ValueError):
    """What the trainer is told when a file or link cannot be read."""


def _clip(text: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not text:
        raise ImportError_("không đọc được nội dung chữ nào")
    return text[:MAX_TEXT_CHARS]


# -- files ----------------------------------------------------------------------------------------


def from_file(name: str, data: bytes) -> str:
    """Markdown of an uploaded file, by its extension."""
    if len(data) > MAX_FILE_BYTES:
        raise ImportError_(f"file lớn hơn {MAX_FILE_BYTES // (1024 * 1024)} MB")
    lower = name.lower()
    if lower.endswith((".md", ".markdown", ".txt")):
        return _clip(_text(data))
    if lower.endswith(".docx"):
        return _clip(_docx(data))
    if lower.endswith(".doc"):
        return _clip(_doc(data))
    if lower.endswith(".xlsx"):
        return _clip(_xlsx(data))
    if lower.endswith(".pdf"):
        return _clip(_pdf(data))
    raise ImportError_("chỉ nhận .md, .txt, .doc, .docx, .xlsx, .pdf")


def _doc(data: bytes) -> str:
    from app.assistant.worddoc import DocError, doc_text

    try:
        return doc_text(data)
    except DocError as exc:
        raise ImportError_(str(exc)) from exc
    except (IndexError, ValueError, KeyError, UnicodeError, struct.error) as exc:
        # A corrupt file the reader did not foresee: still the uploader's file, not a 500.
        raise ImportError_("tệp .doc bị hỏng") from exc


# Stop reading a PDF once its text is this long (MAX_TEXT_CHARS is kept anyway): a crafted
# file with endless pages of text does not keep the API busy past what is stored.
PDF_STOP_CHARS = MAX_TEXT_CHARS + 10_000


def _pdf(data: bytes) -> str:
    """The text layer, page by page ("--- Trang N ---" between pages, for citing), at most
    MAX_PDF_PAGES pages and PDF_STOP_CHARS characters. pypdf (>= 6.1.3, past its known
    decompression-bomb and loop fixes) runs with strict=False on an untrusted file: any failure
    of the parser is the file's fault and becomes ImportError_."""
    from pypdf import PdfReader

    pages: list[str] = []
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ImportError_("PDF có mật khẩu: bỏ mật khẩu rồi tải lại")
        total = 0
        for number, page in enumerate(reader.pages, start=1):
            if number > MAX_PDF_PAGES or total > PDF_STOP_CHARS:
                break
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"--- Trang {number} ---\n\n{text}")
                total += len(text)
    except ImportError_:
        raise
    except MemoryError as exc:
        raise ImportError_("PDF quá phức tạp để đọc chữ") from exc
    except Exception as exc:  # pypdf raises many kinds on a corrupt file
        raise ImportError_("không phải file PDF đọc được") from exc
    if not pages:
        raise ImportError_("PDF không có lớp chữ (bản scan): chưa hỗ trợ nhận dạng chữ")
    return "\n\n".join(pages)


def _text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1258", "cp1252"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _zip_ok(data: bytes, what: str) -> None:
    """A zip that would unpack to far more than it weighs is refused before anything reads it."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            members = z.infolist()
    except zipfile.BadZipFile as exc:
        raise ImportError_(f"không phải file {what} hợp lệ") from exc
    total = sum(m.file_size for m in members)
    if total > MAX_UNZIPPED_BYTES:
        raise ImportError_(f"file {what} giải nén quá lớn")
    for m in members:
        if m.file_size > MAX_ZIP_RATIO * max(m.compress_size, 1) and m.file_size > 1024 * 1024:
            raise ImportError_(f"file {what} có phần nén bất thường")


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(data: bytes) -> str:
    _zip_ok(data, ".docx")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_WORD_XML_BYTES:
                raise ImportError_("tài liệu Word quá lớn")
            xml = z.read(info)
            # Word writes UTF-8 without a DTD. Anything else (UTF-16, which a byte search for
            # "<!DOCTYPE" would miss, or a DTD: entity expansion) is refused.
            head = xml[:200].lstrip(b"\xef\xbb\xbf")
            declared = re.match(rb"<\?xml[^>]*encoding=[\"']([^\"']+)", head)
            if not head.startswith(b"<") or (declared and declared.group(1).lower() != b"utf-8"):
                raise ImportError_("tài liệu Word không hợp lệ")
            if b"<!DOCTYPE" in xml or b"<!ENTITY" in xml:
                raise ImportError_("tài liệu Word không hợp lệ")
            root = ElementTree.fromstring(xml)  # noqa: S314 - UTF-8, no DTD (checked above)
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as exc:
        raise ImportError_("không phải file .docx hợp lệ") from exc
    body = root.find(f"{_W}body")
    if body is None:
        return ""
    out: list[str] = []
    for el in body:
        if el.tag == f"{_W}p":
            out.append(_docx_paragraph(el))
        elif el.tag == f"{_W}tbl":
            out.append(_docx_table(el))
    return "\n\n".join(x for x in out if x.strip())


def _runs(el: ElementTree.Element) -> str:
    parts: list[str] = []
    for node in el.iter():
        if node.tag == f"{_W}t" and node.text:
            parts.append(node.text)
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag in (f"{_W}br", f"{_W}cr"):
            parts.append("\n")
    return "".join(parts).strip()


def _docx_paragraph(p: ElementTree.Element) -> str:
    text = _runs(p)
    if not text:
        return ""
    style = p.find(f"{_W}pPr/{_W}pStyle")
    name = (style.get(f"{_W}val") or "") if style is not None else ""
    if m := re.match(r"(?i)^(?:heading|tieude|title)\s*(\d)?$", name.replace(" ", "")):
        level = int(m.group(1) or 1)
        return f"{'#' * min(level, 4)} {text}"
    if p.find(f"{_W}pPr/{_W}numPr") is not None or name.lower().startswith("list"):
        return f"- {text}"
    return text


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _table(rows: list[list[str]]) -> str:
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(_cell(c) for c in rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _docx_table(tbl: ElementTree.Element) -> str:
    rows = [[_runs(tc) for tc in tr.findall(f"{_W}tc")] for tr in tbl.findall(f"{_W}tr")]
    return _table(rows)


def _xlsx(data: bytes) -> str:
    from openpyxl import load_workbook

    _zip_ok(data, ".xlsx")
    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises several kinds for a bad file
        raise ImportError_("không phải file .xlsx hợp lệ") from exc
    out: list[str] = []
    try:
        for sheet in book.worksheets:
            rows: list[list[str]] = []
            for row in sheet.iter_rows(
                max_row=MAX_SHEET_ROWS, max_col=MAX_SHEET_COLS, values_only=True
            ):
                rows.append(["" if v is None else str(v) for v in row])
            table = _table(rows)
            if table:
                out.append(f"## {sheet.title}\n\n{table}")
    finally:
        book.close()
    return "\n\n".join(out)


# -- web links ------------------------------------------------------------------------------------


_NEVER = tuple(
    ipaddress.ip_network(n)
    for n in (
        "100.64.0.0/10",  # carrier-grade NAT (Alibaba metadata 100.100.100.200)
        "172.17.0.0/16",  # Docker's default bridge (the Docker API, other containers)
        "fd00:ec2::/32",  # AWS IPv6 metadata
    )
)


def hosts_of(raw: str | Iterable[str]) -> tuple[str, ...]:
    """ASSISTANT_URL_HOSTS ("wiki.congty.vn, docs.noibo") as lower-case names."""
    items = raw.split(",") if isinstance(raw, str) else raw
    return tuple(h.strip().lower().strip(".") for h in items if h.strip())


def _listed(host: str, allowed: tuple[str, ...]) -> bool:
    name = host.lower().rstrip(".")
    return any(name == h or name.endswith("." + h) for h in allowed)


def check_ip(address: str, host: str, allowed: tuple[str, ...]) -> None:
    """Refuse an address the server must not fetch from (see the module docstring)."""
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
        or any(ip in net for net in _NEVER if net.version == ip.version)
    ):
        raise ImportError_("không được lấy từ địa chỉ nội bộ của máy chủ CodePulse")
    if ip.is_private and not _listed(host, allowed):
        raise ImportError_(
            "địa chỉ nội bộ: admin cần thêm tên máy này vào ASSISTANT_URL_HOSTS để lấy được"
        )


def _reachable(url: str) -> None:
    """The URL itself: http/https on a web port (the address is checked when connecting)."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError as exc:
        raise ImportError_("cổng (port) không hợp lệ") from exc
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ImportError_("chỉ nhận link http:// hoặc https://")
    if parts.username or parts.password:
        raise ImportError_("link không được chứa tài khoản/mật khẩu")
    if port is not None and port not in URL_PORTS:
        raise ImportError_("chỉ nhận cổng web 80, 443, 8080, 8443")


class _Guarded(httpcore.SyncBackend):
    """Connects only to an address `check_ip` allows, resolved here and connected to as is: the
    check and the connection see the same IP (TLS still verifies the host name)."""

    def __init__(self, allowed: tuple[str, ...]) -> None:
        self._allowed = allowed

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise httpcore.ConnectError(f"cannot resolve {host}") from exc
        addresses = [str(info[4][0]) for info in infos]
        for address in addresses:  # every answer must pass, then the first is used
            check_ip(address, host, self._allowed)
        return super().connect_tcp(addresses[0], port, timeout, local_address, socket_options)


def _client(allowed: tuple[str, ...]) -> httpx.Client:
    transport = httpx.HTTPTransport(retries=0)
    transport._pool._network_backend = _Guarded(allowed)
    return httpx.Client(
        transport=transport,
        timeout=URL_TIMEOUT_S,
        follow_redirects=False,
        trust_env=False,  # an HTTP(S)_PROXY would connect for us, past the address check
    )


def safe_source(url: str) -> str:
    """The URL as shown to readers (and put in Stetho's prompt): without its query string,
    where a token may sit."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def from_url(
    url: str, allowed_hosts: tuple[str, ...] = (), client: httpx.Client | None = None
) -> tuple[str, str]:
    """(title, Markdown) of a web page or a text/Markdown file at `url`."""
    own = client is None
    http = client or _client(allowed_hosts)
    deadline = time.monotonic() + URL_DEADLINE_S
    try:
        current = url
        for _ in range(5):  # redirects followed by hand, each hop checked
            _reachable(current)
            with http.stream(
                "GET", current, headers={"User-Agent": "CodePulse-Stetho/1.0"}
            ) as resp:
                if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                    current = urljoin(current, resp.headers["location"])
                    continue
                if resp.status_code >= 400:
                    raise ImportError_(f"máy chủ trả mã {resp.status_code}")
                kind = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                body = b""
                for piece in resp.iter_bytes():
                    body += piece
                    if len(body) > MAX_URL_BYTES:
                        raise ImportError_("trang lớn hơn 2 MB")
                    if time.monotonic() > deadline:
                        raise ImportError_(f"tải quá {int(URL_DEADLINE_S)} giây")
                break
        else:
            raise ImportError_("chuyển hướng quá nhiều lần")
    except httpx.HTTPError as exc:
        cause = exc.__cause__ or exc.__context__
        if isinstance(cause, ImportError_):
            raise cause from None
        raise ImportError_(f"không tải được ({exc.__class__.__name__})") from exc
    finally:
        if own:
            http.close()
    text = _text(body)
    if kind in ("text/html", "application/xhtml+xml") or text.lstrip().lower().startswith(
        ("<!doctype", "<html")
    ):
        parser = _Html()
        parser.feed(text)
        title = parser.title.strip() or urlsplit(url).path.rsplit("/", 1)[-1] or url
        return title[:200], _clip(parser.markdown())
    if kind.startswith("text/") or kind == "":
        return urlsplit(url).path.rsplit("/", 1)[-1] or url, _clip(text)
    raise ImportError_(f"không đọc được loại nội dung {kind}")


class _Html(HTMLParser):
    """HTML -> Markdown, enough for documentation pages: headings, paragraphs, list items,
    preformatted code, tables; navigation, scripts and styles dropped."""

    SKIP = frozenset(
        {"script", "style", "nav", "footer", "header", "noscript", "svg", "form", "aside"}
    )
    BLOCK = frozenset({"p", "div", "section", "article", "main", "br", "blockquote"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self._in_title = False
        self._skip = 0
        self._pre = 0
        self._out: list[str] = []
        self._line: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._rows: list[list[str]] | None = None

    def _flush(self, prefix: str = "") -> None:
        text = "".join(self._line)
        self._line = []
        text = text if self._pre else " ".join(text.split())
        if text.strip():
            self._out.append(prefix + text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip += 1
        if self._skip:
            return
        if tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") or tag in self.BLOCK or tag == "li":
            self._flush()
        if tag == "pre":
            self._flush()
            self._pre += 1
            self._line.append("```\n")
        elif tag == "table":
            self._rows = []
        elif tag == "tr" and self._rows is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if self._skip:
            return
        if tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._flush("#" * min(int(tag[1]), 4) + " ")
        elif tag == "li":
            self._flush("- ")
        elif tag in self.BLOCK:
            self._flush()
        elif tag == "pre":
            self._line.append("\n```")
            self._flush()
            self._pre = max(0, self._pre - 1)
        elif tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._rows is not None and self._row is not None:
            self._rows.append(self._row)
            self._row = None
        elif tag == "table" and self._rows is not None:
            self._flush()
            if table := _table(self._rows):
                self._out.append(table)
            self._rows = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        if self._cell is not None:
            self._cell.append(data)
        else:
            self._line.append(data)

    def markdown(self) -> str:
        self._flush()
        return "\n\n".join(self._out)

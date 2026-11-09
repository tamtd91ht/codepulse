"""Stetho's knowledge base (docs/specs/28, user 2026-11-09): store, index and find documents.

A document is split into pieces of about CHUNK_CHARS under its headings (a fenced code block is
never cut); each piece is indexed with PostgreSQL full-text search over its accent-folded text
("simple" configuration: Vietnamese has no stemmer, and folding lets "ma jira" find "mã Jira").
A question is folded the same way, its words OR-ed, and the best pieces (ts_rank_cd, a boost
for documents about the screen on view) go into the prompt, at most MAX_PIECES and
MAX_KNOWLEDGE_CHARS.

The built-in how-tos (app/assistant/howto.py) are documents too, written from code: refreshed
when their text changes (`sync_builtin`), never edited on the screen (only switched off).
"""

import hashlib
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, case, delete, func, literal, select, true
from sqlalchemy.orm import Session

from app.assistant.howto import TOPICS
from app.models import AppSetting, AssistantDoc, AssistantDocChunk

log = logging.getLogger(__name__)

CHUNK_CHARS = 1_400
MAX_PIECES = 6
CANDIDATES = 24
MAX_KNOWLEDGE_CHARS = 14_000
MAX_TERMS = 30
SCREEN_BOOST = 0.5  # half a matched word: the screen on view breaks near-ties
WEB_KEY = "assistant_web"  # app_settings: Stetho may look things up on the web

# Folded Vietnamese (and English) words that say nothing about the topic.
STOPWORDS = frozenset(
    [
        "la",
        "cua",
        "va",
        "cac",
        "nhung",
        "cho",
        "toi",
        "ban",
        "minh",
        "lam",
        "sao",
        "the",
        "nao",
        "gi",
        "co",
        "khong",
        "duoc",
        "de",
        "voi",
        "trong",
        "mot",
        "nay",
        "khi",
        "thi",
        "o",
        "se",
        "da",
        "dang",
        "nhu",
        "ve",
        "tu",
        "den",
        "hay",
        "hoac",
        "neu",
        "vi",
        "ma",
        "cung",
        "roi",
        "nhe",
        "a",
        "oi",
        "giup",
        "xin",
        "hoi",
        "biet",
        "can",
        "muon",
        "phai",
        "nen",
        "bao",
        "nhieu",
        "dau",
        "ai",
        "cai",
        "nay",
        "kia",
        "do",
        "day",
        "vay",
        "chu",
        "duoc",
        "nua",
        "them",
        "moi",
        "tat",
        "ca",
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "be",
        "how",
        "what",
        "why",
        "which",
        "with",
        "it",
        "this",
        "that",
        "do",
        "does",
    ]
)


def fold(text: str) -> str:
    """Lower case, without Vietnamese accents (đ -> d): what is indexed and searched."""
    decomposed = unicodedata.normalize("NFD", text.casefold().replace("đ", "d"))
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def terms(question: str) -> list[str]:
    """The words of a question worth searching for, first seen first."""
    out: list[str] = []
    for word in re.findall(r"[a-z0-9]+", fold(question)):
        if len(word) < 2 or word in STOPWORDS or word in out:
            continue
        out.append(word)
        if len(out) >= MAX_TERMS:
            break
    return out


def exact_terms(question: str) -> list[str]:
    """The question's words with their accents, where that differs from the folded form:
    "thưởng" must find "thưởng" before "thường" (both fold to "thuong")."""
    out: list[str] = []
    for word in re.findall(r"\w+", unicodedata.normalize("NFC", question.casefold())):
        plain = fold(word)
        if len(plain) < 2 or plain in STOPWORDS or word == plain or word in out:
            continue
        out.append(word)
        if len(out) >= MAX_TERMS:
            break
    return out


def content_hash(*parts: Any) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


# -- splitting ------------------------------------------------------------------------------------


@dataclass
class Piece:
    heading: str | None
    body: str


_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


def _cut(text: str, size: int) -> list[str]:
    """A block too big for one piece, cut where it can be read alone: a Markdown table by rows
    (its header repeated), a fenced code block by lines (the fence re-opened), anything else by
    lines."""
    lines = text.split("\n")
    head: list[str] = []
    tail: list[str] = []
    if len(lines) > 2 and lines[0].startswith("|") and set(lines[1]) <= set("|-: "):
        head, lines = lines[:2], lines[2:]
    elif lines[0].startswith("```") and lines[-1].strip() == "```":
        head, tail, lines = lines[:1], lines[-1:], lines[1:-1]
    out: list[str] = []
    buf: list[str] = []
    used = sum(len(h) + 1 for h in head + tail)
    for line in lines:
        if buf and used + len(line) + 1 > size:
            out.append("\n".join(head + buf + tail))
            buf, used = [], sum(len(h) + 1 for h in head + tail)
        buf.append(line[: size * 2])  # one absurd line cannot make a piece unbounded
        used += len(line) + 1
    if buf:
        out.append("\n".join(head + buf + tail))
    return out


def split(body: str, size: int = CHUNK_CHARS) -> list[Piece]:
    """Pieces of about `size` characters, each under its heading path ("A > B")."""
    pieces: list[Piece] = []
    trail: list[tuple[int, str]] = []  # (level, heading) from the top
    block: list[str] = []
    buf: list[str] = []
    fenced = False

    def heading() -> str | None:
        return " > ".join(t for _, t in trail) or None

    def emit() -> None:
        text = "\n".join(buf).strip()
        buf.clear()
        if text:
            pieces.append(Piece(heading(), text))

    def add_block() -> None:
        text = "\n".join(block).strip("\n")
        block.clear()
        if not text.strip():
            return
        if len(text) > size * 2:
            emit()
            for part in _cut(text, size):  # a big table / code block / paragraph, in pieces
                buf.append(part)
                emit()
            return
        if buf and sum(len(b) for b in buf) + len(text) > size:
            emit()
        buf.append(text)

    for line in body.replace("\r\n", "\n").split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
            block.append(line)
            if not fenced:
                add_block()
            continue
        if fenced:
            block.append(line)
            continue
        if m := _HEADING.match(line):
            add_block()
            emit()
            level = len(m.group(1))
            while trail and trail[-1][0] >= level:
                trail.pop()
            trail.append((level, m.group(2).strip()))
            continue
        if not line.strip():
            add_block()
            continue
        block.append(line)
    add_block()
    emit()
    return pieces


# -- writing --------------------------------------------------------------------------------------


def index(session: Session, doc: AssistantDoc) -> int:
    """Replace the pieces of `doc` from its body; returns how many."""
    session.execute(delete(AssistantDocChunk).where(AssistantDocChunk.doc_id == doc.id))
    extra = " ".join([doc.title, *(doc.keywords or [])])
    pieces = split(doc.body)
    for position, piece in enumerate(pieces):
        text = " ".join(filter(None, [extra, piece.heading, piece.body]))
        # Both forms: folded (a question typed without accents finds it) and as written (an
        # accented question finds the exact word first).
        searchable = fold(text) + " " + unicodedata.normalize("NFC", text.casefold())
        session.add(
            AssistantDocChunk(
                doc_id=doc.id,
                position=position,
                heading=piece.heading,
                body=piece.body,
                search=func.to_tsvector("simple", searchable),
            )
        )
    session.flush()
    return len(pieces)


def save(session: Session, doc: AssistantDoc) -> int:
    """Store `doc` (new or changed) and index it."""
    doc.content_hash = content_hash(doc.title, doc.body, doc.keywords, doc.paths)
    session.add(doc)
    session.flush()
    return index(session, doc)


def sync_builtin(session: Session) -> int:
    """The built-in how-tos as documents: added, refreshed when their text changed, removed
    when code dropped them. An admin's on/off choice is kept. Returns how many changed."""
    have = {
        d.key: d
        for d in session.scalars(select(AssistantDoc).where(AssistantDoc.source == "builtin"))
    }
    changed = 0
    wanted = set()
    for topic in TOPICS:
        key = f"builtin:{topic.id}"
        wanted.add(key)
        body = topic.body.strip()
        digest = content_hash(topic.title, body, list(topic.keywords), list(topic.paths))
        doc = have.get(key)
        if doc is not None and doc.content_hash == digest:
            continue
        if doc is None:
            doc = AssistantDoc(key=key, source="builtin", audience="all", enabled=True)
        doc.title, doc.body = topic.title, body
        doc.keywords, doc.paths = list(topic.keywords), list(topic.paths)
        doc.source_name = "CodePulse"
        save(session, doc)
        changed += 1
    for old_key, old_doc in have.items():
        if old_key not in wanted:
            session.delete(old_doc)
            changed += 1
    session.flush()
    return changed


_synced = False


def ensure_builtin() -> None:
    """`sync_builtin` once per process (the built-ins only change with a deploy), in a session
    of its own: the caller's request session is never committed or rolled back here. A failure
    is logged and tried again on the next call."""
    global _synced
    if _synced:
        return
    from app.db import get_sessionmaker

    try:
        with get_sessionmaker()() as own:
            sync_builtin(own)
            own.commit()
        _synced = True
    except Exception:
        log.exception("stetho built-in documents not synced")


# -- finding --------------------------------------------------------------------------------------


@dataclass
class Hit:
    doc_id: int
    title: str
    heading: str | None
    body: str
    source: str
    source_name: str | None
    score: float


def _audience(trainer: bool) -> ColumnElement[bool]:
    return true() if trainer else AssistantDoc.audience == "all"


def search(
    session: Session,
    question: str,
    *,
    path: str | None = None,
    trainer: bool = False,
    limit: int = MAX_PIECES,
    budget: int = MAX_KNOWLEDGE_CHARS,
) -> list[Hit]:
    """The pieces that best answer `question`, documents about `path` first among equals."""
    words = terms(question)
    if not words:
        return []
    # Accented forms only for words searched anyway, and at most MAX_TERMS checks in all.
    exact = [w for w in exact_terms(question) if fold(w) in words][: max(0, MAX_TERMS - len(words))]
    query = func.to_tsquery("simple", literal(" | ".join(words)))
    # How many of the question's words a piece has comes first (a short piece with all of them
    # beats a long one repeating two common ones); ts_rank_cd, normalised by length (32: r/(r+1)),
    # orders pieces that have as many.
    covered = sum(
        (
            case(
                (AssistantDocChunk.search.op("@@")(func.to_tsquery("simple", literal(w))), 1),
                else_=0,
            )
            for w in [*words, *exact]
        ),
        start=literal(0),
    )
    rank = func.ts_rank_cd(AssistantDocChunk.search, query, 32)
    rows = session.execute(
        select(
            AssistantDocChunk.doc_id,
            AssistantDocChunk.heading,
            AssistantDocChunk.body,
            AssistantDoc.title,
            AssistantDoc.source,
            AssistantDoc.source_name,
            AssistantDoc.paths,
            rank.label("rank"),
            covered.label("covered"),
        )
        .join(AssistantDoc, AssistantDoc.id == AssistantDocChunk.doc_id)
        .where(
            AssistantDoc.enabled.is_(True),
            _audience(trainer),
            AssistantDocChunk.search.op("@@")(query),
        )
        .order_by(covered.desc(), rank.desc(), AssistantDocChunk.id)
        .limit(CANDIDATES)
    ).all()
    screen = (path or "").rstrip("/") or None
    hits = [
        Hit(
            doc_id=r.doc_id,
            title=r.title,
            heading=r.heading,
            body=r.body,
            source=r.source,
            source_name=r.source_name,
            score=int(r.covered)
            + float(r.rank)
            + (SCREEN_BOOST if screen and any(screen.startswith(p) for p in r.paths or []) else 0),
        )
        for r in rows
    ]
    hits.sort(key=lambda h: h.score, reverse=True)
    out: list[Hit] = []
    used = 0
    for hit in hits:
        if len(out) >= limit or used + len(hit.body) > budget:
            continue
        out.append(hit)
        used += len(hit.body)
    return out


def render(hits: list[Hit]) -> str:
    """The <tai_lieu> text: numbered pieces with where they come from."""
    if not hits:
        return "(Không có đoạn tài liệu nào khớp câu hỏi.)"
    blocks = []
    for n, h in enumerate(hits, start=1):
        where = f"{h.title}" + (f" > {h.heading}" if h.heading else "")
        origin = {"url": f" (nguồn: {h.source_name})", "file": f" (file: {h.source_name})"}.get(
            h.source, ""
        )
        blocks.append(f"[{n}] {where}{origin}\n{h.body}")
    return "\n\n---\n\n".join(blocks)


def web_enabled(session: Session) -> bool:
    row = session.get(AppSetting, WEB_KEY)
    return bool(row is not None and row.value is True)

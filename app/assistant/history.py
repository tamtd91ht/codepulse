"""Lịch sử Stetho (docs/specs/28 §9, user 2026-11-09): what people asked Stetho, kept
HISTORY_KEEP_DAYS days, seen by admins only (ADMIN_EMAILS — not by holders of admin.* rights).

- A turn keeps the question as asked and only the gist of the answer: up to three key points
  taken from its own text (headings, list items, first sentence; code dropped), with no LLM call.
- Turns group into conversations (the client's id; "Cuộc trò chuyện mới" starts another).
- Each conversation gets one tag from a fixed list, so reports can count by it. Cheap on
  purpose: a batch job tags conversations quiet for TAG_AFTER, up to TAG_BATCH in ONE call to
  the smallest model, sending only the questions, cut short. Accuracy is not the point; when
  the call fails, keyword rules tag them. An admin can change a tag by hand.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.assistant.knowledge import fold
from app.models import AssistantConversation, AssistantTurn

log = logging.getLogger(__name__)

HISTORY_KEEP_DAYS = 30
TAG_AFTER = timedelta(minutes=15)  # a conversation quiet this long gets its tag
TAG_BATCH = 40  # conversations per tagging call
TAG_MODEL = "haiku"
QUESTION_CHARS = 4_000
POINT_CHARS = 160
MAX_POINTS = 3
TITLE_CHARS = 120
TAG_TEXT_CHARS = 220  # of a conversation's questions, sent to the tagger


@dataclass(frozen=True)
class Tag:
    key: str
    label: str
    hint: str  # what the tagger reads
    words: tuple[str, ...]  # folded keywords for the rule fallback


TAGS: tuple[Tag, ...] = (
    Tag(
        "codepulse_howto",
        "Cách dùng CodePulse",
        "how to use a CodePulse screen or feature",
        ("lam sao", "cach", "o dau", "man", "menu", "nut", "huong dan", "xem o"),
    ),
    Tag(
        "jira_commit",
        "Jira & chuẩn commit",
        "Jira issues, branch/commit/MR conventions, linking",
        ("jira", "commit", "nhanh", "branch", "fixes", "hook", "issue", "merge request"),
    ),
    Tag(
        "quality_analysis",
        "Phân tích chất lượng",
        "review scores, quality trends, project/repo quality",
        ("diem", "chat luong", "xu huong", "review", "verdict", "ty le", "quality"),
    ),
    Tag(
        "findings_fix",
        "Finding & sửa lỗi",
        "specific findings, bugs, how to fix code, close or dispute findings",
        ("finding", "loi", "bug", "sua", "critical", "dong finding", "khieu nai", "security"),
    ),
    Tag(
        "people_team",
        "Hiệu suất người & team",
        "a person's or a team's performance, coaching",
        ("thanh vien", "team", "nguoi", "coaching", "hieu suat", "lead"),
    ),
    Tag(
        "dpi",
        "DPI & đánh giá",
        "DPI scorecards, monthly evaluation, evidence",
        ("dpi", "danh gia", "scorecard", "minh chung", "thang"),
    ),
    Tag(
        "admin_config",
        "Cấu hình & quản trị",
        "settings, permissions, accounts, integrations setup",
        ("cau hinh", "quyen", "tai khoan", "admin", "token", "ket noi", "cai dat"),
    ),
    Tag(
        "tech_general",
        "Kỹ thuật chung",
        "general programming or technology questions",
        ("python", "java", "react", "sql", "docker", "api", "thu vien", "framework"),
    ),
    Tag("other", "Khác", "anything else", ()),
)
TAG_KEYS = tuple(t.key for t in TAGS)
TAG_LABEL = {t.key: t.label for t in TAGS}


# -- the gist of an answer ------------------------------------------------------------------------


def _clean(text: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*|__|\[(\d+)\]", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return " ".join(text.split()).strip(" -*•:")


def key_points(answer: str) -> str | None:
    """Up to MAX_POINTS short points of an answer (headings, list items, else first sentences),
    one per line; code blocks and tables left out. None for an empty answer."""
    body = re.sub(r"```.*?(```|$)", " ", answer or "", flags=re.S)
    lines = [ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("|")]
    points: list[str] = []
    for ln in lines:
        stripped = ln.strip()
        if re.match(r"^(#{1,6}\s|[-*•]\s|\d+[.)]\s)", stripped) and not ln.startswith(
            ("   ", "\t")
        ):
            text = _clean(re.sub(r"^(#{1,6}|[-*•]|\d+[.)])\s+", "", stripped))
            if len(text) >= 8 and text not in points:
                points.append(text)
        if len(points) >= MAX_POINTS:
            break
    if not points:
        prose = _clean(" ".join(lines))
        points = [s for s in re.split(r"(?<=[.!?])\s+", prose) if len(s) >= 8][:MAX_POINTS]
    if not points:
        return None
    return "\n".join(p[:POINT_CHARS] + ("…" if len(p) > POINT_CHARS else "") for p in points)


# -- recording a turn -----------------------------------------------------------------------------


def record_turn(
    session: Session,
    *,
    conversation_id: str,
    user_id: int,
    question: str,
    answer: str,
    status: str,
    screen: str | None,
    model: str | None,
    cost_usd: float | None,
    duration_ms: int | None,
    docs: int,
    web: bool,
    now: datetime | None = None,
) -> str:
    """Add a turn to the conversation (created on its first turn). A conversation id that is
    someone else's starts a new one for this person (the id is the client's). Returns the id
    used."""
    when = now or datetime.now(UTC)
    cost = Decimal(str(round(cost_usd or 0.0, 4)))
    row = session.get(AssistantConversation, conversation_id, with_for_update=True)
    if row is not None and row.user_id != user_id:
        # Someone else's id: this person's own conversation under an id derived from it, so
        # their later turns on the same client id land in it too.
        conversation_id = f"{conversation_id[:40]}-u{user_id}"
        row = session.get(AssistantConversation, conversation_id, with_for_update=True)
    if row is None:
        try:
            with session.begin_nested():  # two first turns at once: the second one appends
                session.add(
                    AssistantConversation(
                        id=conversation_id,
                        user_id=user_id,
                        title=" ".join(question.split())[:TITLE_CHARS],
                        started_at=when,
                        last_at=when,
                        turns=1,
                        cost_usd=cost,
                    )
                )
        except IntegrityError:
            row = session.get(
                AssistantConversation, conversation_id, with_for_update=True, populate_existing=True
            )
            if row is None or row.user_id != user_id:
                raise
    if row is not None:
        row.last_at = when
        row.turns += 1
        row.cost_usd = (row.cost_usd or Decimal(0)) + cost
        if row.tag_source != "manual":  # a new turn: looked at again (an admin's tag stays)
            row.tag = row.tag_source = row.tagged_at = None
    session.add(
        AssistantTurn(
            conversation_id=conversation_id,
            asked_at=when,
            question=question[:QUESTION_CHARS],
            answer_points=key_points(answer),
            answer_chars=len(answer or ""),
            status=status,
            screen=(screen or None) and screen[:300],
            model=model,
            cost_usd=Decimal(str(round(cost_usd, 4))) if cost_usd is not None else None,
            duration_ms=duration_ms,
            docs=docs,
            web=web,
        )
    )
    session.flush()
    return conversation_id


# -- tagging --------------------------------------------------------------------------------------

TAG_SYSTEM = (
    "Classify each conversation of an internal code-review assistant into exactly one topic. "
    "Topics: "
    + "; ".join(f"{t.key} = {t.hint}" for t in TAGS)
    + ". Reply with the JSON only. Guessing is fine; never leave one out."
)
TAG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tags": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "tag": {"type": "string", "enum": list(TAG_KEYS)},
                },
                "required": ["n", "tag"],
            },
        }
    },
    "required": ["tags"],
}


def rule_tag(text: str) -> str:
    """The tag whose keywords the text has most of; "other" when none."""
    plain = f" {fold(text)} "
    best, hits = "other", 0
    for tag in TAGS:
        n = sum(1 for w in tag.words if f" {w} " in plain or (len(w) > 4 and w in plain))
        if n > hits:
            best, hits = tag.key, n
    return best


def pending(session: Session, now: datetime, limit: int = TAG_BATCH) -> list[tuple[str, str]]:
    """(id, the questions cut short) of conversations quiet for TAG_AFTER and not tagged."""
    ids = list(
        session.scalars(
            select(AssistantConversation.id)
            .where(
                AssistantConversation.tag.is_(None), AssistantConversation.last_at < now - TAG_AFTER
            )
            .order_by(AssistantConversation.last_at)
            .limit(limit)
        )
    )
    if not ids:
        return []
    questions: dict[str, list[str]] = {i: [] for i in ids}
    for cid, q in session.execute(
        select(AssistantTurn.conversation_id, AssistantTurn.question)
        .where(AssistantTurn.conversation_id.in_(ids))
        .order_by(AssistantTurn.asked_at)
    ):
        questions[str(cid)].append(" ".join(str(q).split()))
    return [(i, " / ".join(questions[i])[:TAG_TEXT_CHARS]) for i in ids]


def tag_pending(session: Session, classify: Any, now: datetime | None = None) -> dict[str, Any]:
    """Tag one batch: `classify(prompt)` is the LLM (a provider's `review`); on any failure the
    keyword rules tag the batch. Returns what was done (and the call's cost)."""
    from app.review.providers.base import ReviewPrompt

    when = now or datetime.now(UTC)
    batch = pending(session, when)
    if not batch:
        return {"tagged": 0}
    lines = "\n".join(f"{n}. {text}" for n, (_, text) in enumerate(batch, start=1))
    chosen: dict[int, str] = {}
    source, cost = "ai", 0.0
    try:
        result = classify(ReviewPrompt(system=TAG_SYSTEM, user=lines, json_schema=TAG_SCHEMA))
        cost = float(result.cost_usd or 0.0)
        out = result.output if isinstance(result.output, dict) else json.loads(str(result.output))
        for item in out.get("tags") or []:
            if isinstance(item, dict) and item.get("tag") in TAG_KEYS:
                chosen[int(item.get("n") or 0)] = str(item["tag"])
    except Exception as exc:  # the LLM is down, over budget, or answered nonsense
        log.warning("stetho tagging fell back to rules", extra={"error": exc.__class__.__name__})
        source = "rule"
    tagged = 0
    for n, (cid, text) in enumerate(batch, start=1):
        tag = chosen.get(n) or rule_tag(text)
        # Read again: a turn added during the LLM call clears the tag and must be tagged anew.
        row = session.get(AssistantConversation, cid, with_for_update=True, populate_existing=True)
        if row is None or row.tag is not None or row.last_at > when - TAG_AFTER:
            continue
        row.tag = tag
        row.tag_source = "ai" if n in chosen else "rule"
        row.tagged_at = when
        tagged += 1
    session.flush()
    return {"tagged": tagged, "source": source, "cost_usd": cost}


def tag_job() -> dict[str, Any]:
    """Worker entry point (scheduler, every few minutes): one batch with the smallest model,
    never over the month's LLM budget (rules then), its cost audited into the month's spend."""
    from app.admin_settings import monthly_budget_usd, with_overrides
    from app.api.audit import audit
    from app.config import get_settings
    from app.db import get_sessionmaker
    from app.review.providers import build_provider
    from app.workers.knowledge_jobs import month_spend_usd

    settings = get_settings()
    with get_sessionmaker()() as session:
        tuned = with_overrides(session, settings)
        budget = monthly_budget_usd(session, settings)
        over = budget > 0 and month_spend_usd(session) >= budget

        def classify(prompt: Any) -> Any:
            if over:
                raise RuntimeError("monthly LLM budget reached")
            cheap = tuned.model_copy(update={"llm_model": TAG_MODEL})
            return build_provider(cheap, timeout_s=90.0, max_retries=1).review(prompt)

        result = tag_pending(session, classify)
        if result.get("cost_usd"):
            audit(
                session,
                None,
                "assistant.tag.cost",
                "assistant",
                cost_usd=result["cost_usd"],
                tagged=result["tagged"],
            )
        session.commit()
    log.info("stetho conversations tagged", extra=result)
    return result


def enqueue_tag_job() -> None:
    """Scheduler: queue the tagging batch on the default worker (cheap, fixed id)."""
    from rq import Queue

    from app.db import get_redis

    Queue("default", connection=get_redis()).enqueue(
        "app.assistant.history.tag_job", job_id="stetho-tag", job_timeout=300
    )


# -- retention ------------------------------------------------------------------------------------


def purge(session: Session, now: datetime | None = None) -> int:
    """Conversations whose last turn is older than HISTORY_KEEP_DAYS go, with their turns."""
    cutoff = (now or datetime.now(UTC)) - timedelta(days=HISTORY_KEEP_DAYS)
    result = session.execute(
        delete(AssistantConversation).where(AssistantConversation.last_at < cutoff)
    )
    return int(getattr(result, "rowcount", 0) or 0)

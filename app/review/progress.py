"""Live progress of running reviews (docs/specs/17 §Tiến trình).

A review job runs in one database transaction, so its "running" row is invisible to the API
until it finishes. The worker therefore also writes a small JSON record per running review to
Redis: what is reviewed, by whom, which stage. The web shows these. Records expire on their own
(a crashed worker leaves nothing behind for long) and Redis trouble never breaks a review.
When a review ends, a short record with its outcome goes to a capped "recent" list.

`end()` runs just before the job's transaction commits: for a moment a review is in neither the
running list nor the database. The web treats that as "ended", which is harmless.

No code, diff or finding text is stored here: only the target, people and stage names.
"""

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, Protocol

from redis import Redis
from redis.exceptions import RedisError

log = logging.getLogger(__name__)

INDEX_KEY = "cp:reviews:active"
ITEM_KEY = "cp:review:active:{}"
RECENT_KEY = "cp:reviews:recent"
# Longer than REVIEW_JOB_TIMEOUT (45 min): a live review always refreshes before expiry.
TTL_S = 50 * 60
MAX_ACTIVE = 50
RECENT_MAX = 30
RECENT_TTL_S = 60 * 60
# The worker must not stall on a hung Redis: progress is a bonus.
SOCKET_TIMEOUT_S = 2.0

# Stage ids the worker reports, in order (the web maps them to labels).
STAGES = ("start", "diff", "llm", "deep", "profile", "save")
# Fields of a running record kept in the "recent" list (people and target, no stage details).
RECENT_FIELDS = (
    "review_id",
    "kind",
    "project_id",
    "project",
    "ref",
    "sha",
    "title",
    "author_user_id",
    "author",
    "requested_by",
    "trigger",
    "round",
    "started_at",
)


class ProgressSink(Protocol):
    def start(self, review_id: int, info: dict[str, Any]) -> None: ...
    def stage(self, review_id: int, stage: str, detail: str = "", **extra: Any) -> None: ...
    def end(self, review_id: int, status: str | None = None) -> None: ...


class ProgressReader(Protocol):
    def active(self) -> list[dict[str, Any]]: ...
    def recent(self) -> list[dict[str, Any]]: ...


class NullProgress:
    def start(self, review_id: int, info: dict[str, Any]) -> None:
        return None

    def stage(self, review_id: int, stage: str, detail: str = "", **extra: Any) -> None:
        return None

    def end(self, review_id: int, status: str | None = None) -> None:
        return None


def _ended(record: dict[str, Any], status: str | None) -> dict[str, Any]:
    """`status` None: the job crashed (its transaction rolled back; RQ may retry it)."""
    return {
        **{k: record.get(k) for k in RECENT_FIELDS},
        "status": status,
        "ended_at": _now_iso(),
    }


class MemoryProgress:
    """For tests and single-process use."""

    def __init__(self) -> None:
        self.items: dict[int, dict[str, Any]] = {}
        self.ended: list[dict[str, Any]] = []

    def start(self, review_id: int, info: dict[str, Any]) -> None:
        self.items[review_id] = {**info, "review_id": review_id, "stage": "start", "detail": ""}

    def stage(self, review_id: int, stage: str, detail: str = "", **extra: Any) -> None:
        if review_id in self.items:
            self.items[review_id].update(stage=stage, detail=detail, **extra)

    def end(self, review_id: int, status: str | None = None) -> None:
        record = self.items.pop(review_id, None)
        if record is not None:
            self.ended.insert(0, _ended(record, status))

    def active(self) -> list[dict[str, Any]]:
        return sorted(self.items.values(), key=lambda i: i.get("started_at") or "")

    def recent(self) -> list[dict[str, Any]]:
        return self.ended[:RECENT_MAX]


class RedisProgress:
    def __init__(self, redis: Redis, ttl_s: int = TTL_S) -> None:
        self._redis, self._ttl = redis, ttl_s

    # -- worker side: never raises -------------------------------------------------------------

    def start(self, review_id: int, info: dict[str, Any]) -> None:
        record = {**info, "review_id": review_id, "stage": "start", "detail": ""}
        record["stage_at"] = record.get("started_at")
        try:
            pipe = self._redis.pipeline()
            pipe.set(ITEM_KEY.format(review_id), json.dumps(record, default=str), ex=self._ttl)
            pipe.zadd(INDEX_KEY, {str(review_id): time.time()})
            pipe.expire(INDEX_KEY, self._ttl)
            pipe.execute()
        except RedisError as exc:
            log.warning("review progress not recorded", extra={"error": exc.__class__.__name__})

    def stage(self, review_id: int, stage: str, detail: str = "", **extra: Any) -> None:
        key = ITEM_KEY.format(review_id)
        try:
            raw = self._redis.get(key)
            if not raw:
                return
            record = json.loads(raw)
            record.update(stage=stage, detail=detail[:120], stage_at=_now_iso(), **extra)
            self._redis.set(key, json.dumps(record, default=str), ex=self._ttl)
            self._redis.expire(INDEX_KEY, self._ttl)
        except (RedisError, ValueError) as exc:
            log.warning("review progress not updated", extra={"error": exc.__class__.__name__})

    def end(self, review_id: int, status: str | None = None) -> None:
        key = ITEM_KEY.format(review_id)
        try:
            raw = self._redis.get(key)
            pipe = self._redis.pipeline()
            pipe.delete(key)
            pipe.zrem(INDEX_KEY, str(review_id))
            if raw:
                pipe.lpush(RECENT_KEY, json.dumps(_ended(json.loads(raw), status), default=str))
                pipe.ltrim(RECENT_KEY, 0, RECENT_MAX - 1)
                pipe.expire(RECENT_KEY, RECENT_TTL_S)
            pipe.execute()
        except (RedisError, ValueError) as exc:
            log.warning("review progress not cleared", extra={"error": exc.__class__.__name__})

    # -- API side: Redis trouble means "nothing to show", never an error page ------------------

    def active(self) -> list[dict[str, Any]]:
        """Running reviews, oldest first; ids whose record expired are dropped from the index."""
        try:
            # Entries older than any live review (crashed worker) would otherwise stay forever.
            self._redis.zremrangebyscore(INDEX_KEY, 0, time.time() - self._ttl)
            ids = [
                i.decode() if isinstance(i, bytes) else str(i)
                for i in self._redis.zrange(INDEX_KEY, 0, MAX_ACTIVE - 1)
            ]
            if not ids:
                return []
            raws = self._redis.mget([ITEM_KEY.format(i) for i in ids])
            out: list[dict[str, Any]] = []
            gone: list[str] = []
            for review_id, raw in zip(ids, raws, strict=True):
                try:
                    record = json.loads(raw) if raw else None
                except ValueError:
                    record = None
                if record is None:
                    gone.append(review_id)
                else:
                    out.append(record)
            if gone:
                self._redis.zrem(INDEX_KEY, *gone)
            return out
        except RedisError as exc:
            log.warning("review progress not readable", extra={"error": exc.__class__.__name__})
            return []

    def recent(self) -> list[dict[str, Any]]:
        try:
            raws = self._redis.lrange(RECENT_KEY, 0, RECENT_MAX - 1)
        except RedisError as exc:
            log.warning("recent reviews not readable", extra={"error": exc.__class__.__name__})
            return []
        out: list[dict[str, Any]] = []
        for raw in raws:
            try:
                out.append(json.loads(raw))
            except ValueError:
                continue
        return out


def progress_redis(url: str) -> Redis:
    """A client with short timeouts, for the worker's progress writes."""
    return Redis.from_url(
        url, socket_timeout=SOCKET_TIMEOUT_S, socket_connect_timeout=SOCKET_TIMEOUT_S
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# -- the queue -------------------------------------------------------------------------------------

# Every review enqueuer names its job after the target (webhook, dashboard, Telegram, rerun,
# backfill), so the queue can be read from the job ids alone: no job payload is unpickled.
MR_JOB = re.compile(r"review-mr-(\d+)-(\d+)-([0-9a-f]{7,40})(?:-(rerun|again)\b.*)?")
COMMIT_JOB = re.compile(r"review-commit-([0-9a-f]{7,40})(?:-(rerun|again)\b.*)?")
TRIGGER_OF_SUFFIX = {"rerun": "rerun", "again": "telegram"}


def queued_reviews(queue: Any, scan: int = 200) -> list[dict[str, Any]]:
    """Review jobs waiting in the RQ queue (oldest first), parsed from their ids (one LRANGE).
    A commit job id has no project: `project_id` is None and the caller looks the sha up."""
    try:
        ids = [i.decode() if isinstance(i, bytes) else str(i) for i in queue.get_job_ids(0, scan)]
    except RedisError as exc:
        log.warning("review queue not readable", extra={"error": exc.__class__.__name__})
        return []
    out: list[dict[str, Any]] = []
    for job_id in ids:
        if m := MR_JOB.fullmatch(job_id):
            out.append(
                {
                    "kind": "mr",
                    "project_id": int(m[1]),
                    "ref": m[2],
                    "sha": m[3],
                    "trigger": TRIGGER_OF_SUFFIX.get(m[4] or "", ""),
                }
            )
        elif m := COMMIT_JOB.fullmatch(job_id):
            out.append(
                {
                    "kind": "commit",
                    "project_id": None,
                    "ref": m[1],
                    "sha": m[1],
                    "trigger": TRIGGER_OF_SUFFIX.get(m[2] or "", ""),
                }
            )
    return out

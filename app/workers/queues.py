"""Which RQ queue a job goes to (docs/specs/13 "Hàng đợi review").

Reviews have queues of their own, served by the review workers (`review-worker`,
`REVIEW_WORKERS` of them, each running one review at a time), in two tiers:

- asked (`reviews_asked:*`): what one member asked for and waits on (user 2026-11-09): `/review`
  from Telegram, "Chạy lại" of a review, the AI check of a close request (web or Claude Code
  through MCP) and of a dispute; a worker always takes these first;
- the rest (`reviews:*`): GitLab webhooks, backfills, criteria re-scoring, and the batches the
  "Yêu cầu review" page sends after scanning commit lists (not a member's own request).

Each tier is split into lanes by dự án (user 2026-11-13: a dự án reviewing in bulk must not
make another dự án's single review wait for the whole batch). The lane of dự án `x` is
`1 + (x - 1) % REVIEW_LANES`; lane 0 holds work with no dự án. With REVIEW_LANES at least the
number of dự án, each dự án has a lane of its own. Workers take the lanes in turn
(round-robin within a tier, `rotate`): after a job of lane A they look at every other lane
before A again, so a dự án's review waits at most for a worker to finish the one it runs, not
for another dự án's backlog. All workers serve all lanes: a lane no one uses costs nothing, and
one busy dự án still gets every worker when nobody else waits.

Within a lane it is first come first served. The unsplit names (`reviews_asked`, `reviews`)
are still served, last in their tier: jobs queued before the split, or by an older process
during a deploy. Syncs, knowledge builds, notifications and other short jobs stay on
`default`, whose worker also takes reviews when it has nothing else to do, so a review never
waits behind a sync.
"""

from typing import TYPE_CHECKING, Any

from rq import Queue, Worker

from app.collector.sync import Enqueuer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

DEFAULT_QUEUE = "default"
REVIEW_QUEUE = "reviews"
ASKED_REVIEW_QUEUE = "reviews_asked"
# Review triggers of a member's own request: their reviews go to the asked tier ("dashboard" is
# the scanning "Yêu cầu review" page: not one of them).
ASKED_TRIGGERS = frozenset({"telegram", "rerun", "mcp"})
NO_PROJECT_LANE = 0


def lane_count() -> int:
    from app.config import get_settings  # lazy: the worker imports this module early

    return get_settings().review_lanes


def lane_of(bp_id: int | None, lanes: int | None = None) -> int:
    """The lane of a dự án (0: none). Dự án ids 1..REVIEW_LANES each get a lane of their own."""
    if bp_id is None or bp_id <= 0:
        return NO_PROJECT_LANE
    return 1 + (bp_id - 1) % (lanes or lane_count())


def lane_names(base: str, lanes: int | None = None, connection: Any = None) -> list[str]:
    """Every queue of a tier in serving order: lanes 0..M, then any other lane RQ knows in
    Redis (`rq:queues`: queued under another REVIEW_LANES, before a restart or by a process
    with another value, so never stranded), then the unsplit legacy name."""
    names = [f"{base}:{lane}" for lane in range(1 + (lanes or lane_count()))]
    if connection is not None:
        names += [n for n in known_lanes(connection, base) if n not in names]
    return [*names, base]


def known_lanes(connection: Any, base: str) -> list[str]:
    """Lane queues of a tier RQ has registered in Redis, by lane number; [] when Redis cannot
    say (the configured lanes are still served)."""
    try:
        queues = Queue.all(connection=connection)
    except Exception:
        return []
    found: list[tuple[int, str]] = []
    for q in queues:
        head, _, lane = q.name.partition(":")
        if head == base and lane.isdigit():
            found.append((int(lane), q.name))
    return [name for _, name in sorted(found)]


def review_queue(queue: Enqueuer, *, asked: bool = False, bp_id: int | None = None) -> Enqueuer:
    """The review queue of the dự án `bp_id` (None: the no-dự án lane) in its tier (`asked`:
    the tier served first), on the same Redis as `queue` (tests pass a fake: used as is)."""
    if isinstance(queue, Queue):
        base = ASKED_REVIEW_QUEUE if asked else REVIEW_QUEUE
        return Queue(f"{base}:{lane_of(bp_id)}", connection=queue.connection)
    return queue


def review_queues(queue: Enqueuer, *, asked: bool) -> list[Enqueuer]:
    """Every queue of a tier (each lane and the legacy name), for readers: the activity panel,
    a job looked up by id. A fake queue stands for all of them."""
    if isinstance(queue, Queue):
        base = ASKED_REVIEW_QUEUE if asked else REVIEW_QUEUE
        names = lane_names(base, connection=queue.connection)
        return [Queue(name, connection=queue.connection) for name in names]
    return [queue]


def bp_of_repo(session: "Session", project_id: int | None) -> int | None:
    """The dự án of a repo (`projects.id`), the lane key of its reviews."""
    if project_id is None:
        return None
    from app.models import Project  # lazy: keeps this module light for the worker

    project = session.get(Project, project_id)
    return project.business_project_id if project is not None else None


def tier(name: str) -> str:
    """The tier a queue name belongs to: lanes of one tier rotate together."""
    return name.split(":", 1)[0]


def worker_queues(names: list[str], connection: Any = None) -> list[str]:
    """The queues a worker listens on, in the order it serves them: whoever serves `reviews`
    serves every lane of the asked tier first, then every lane of `reviews` (with `connection`:
    also the lanes already in Redis beyond REVIEW_LANES)."""
    out: list[str] = []
    for name in names:
        expanded = (
            lane_names(ASKED_REVIEW_QUEUE, connection=connection)
            + lane_names(REVIEW_QUEUE, connection=connection)
            if name == REVIEW_QUEUE
            else [name]
        )
        out.extend(n for n in expanded if n not in out)
    return out


def rotate(ordered: list[Any], taken: Any, name: Any = lambda q: q.name) -> list[Any]:
    """Round-robin within the tier of `taken` (the queue a job just came from): the lanes after
    it come first, it goes last; other tiers keep their place, so the asked tier still comes
    before the rest and `default` keeps its rank."""
    group = tier(name(taken))
    lanes = [q for q in ordered if tier(name(q)) == group]
    if taken not in lanes:
        return ordered
    pos = lanes.index(taken)
    turned = iter(lanes[pos + 1 :] + lanes[: pos + 1])
    return [next(turned) if tier(name(q)) == group else q for q in ordered]


def review_backlog(
    queue: Enqueuer, *, asked: bool = False, bp_id: int | None = None
) -> tuple[int, int] | None:
    """(reviews waiting ahead of a new one in its dự án's lane, workers that take reviews), or
    None when it cannot be told (a fake queue, Redis trouble): a caller then says nothing rather
    than something wrong. Other dự án's lanes are taken in turn, never all before this one; an
    asked-for review only waits behind the asked-for ones of its lane."""
    target = review_queue(queue, bp_id=bp_id)
    if not isinstance(target, Queue):
        return None
    try:
        workers = Worker.count(queue=target)
        first = review_queue(queue, asked=True, bp_id=bp_id)
        waiting = len(first) + (0 if asked else len(target))  # type: ignore[arg-type]
        return waiting, workers
    except Exception:
        return None


def backlog_note(backlog: tuple[int, int] | None, queued: int) -> str:
    """One sentence for the person who just asked: how many reviews are ahead and how many run
    at once. `queued`: how many of the waiting ones are theirs (at the end of the line)."""
    if backlog is None:
        return ""
    waiting, workers = backlog
    ahead = max(waiting - queued, 0)
    if workers <= 0:
        return "Chưa có worker review nào đang chạy; review sẽ bắt đầu khi worker lên lại."
    if ahead == 0:
        return (
            f"Không có review nào của dự án này chờ trước; {workers} worker review chạy song "
            "song, xen kẽ giữa các dự án."
        )
    return (
        f"Có {ahead} review của dự án này đang chờ trước; {workers} worker review chạy song "
        "song, xen kẽ giữa các dự án."
    )


__all__: list[Any] = [
    "ASKED_REVIEW_QUEUE",
    "ASKED_TRIGGERS",
    "DEFAULT_QUEUE",
    "NO_PROJECT_LANE",
    "REVIEW_QUEUE",
    "backlog_note",
    "bp_of_repo",
    "known_lanes",
    "lane_names",
    "lane_of",
    "review_backlog",
    "review_queue",
    "review_queues",
    "rotate",
    "worker_queues",
]

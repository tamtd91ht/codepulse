"""The activity panel reads the review lanes (app/api/routers/activity.py, user 2026-11-13)."""

from typing import Any

from app.api.routers.activity import interleave, nonempty


def test_interleave_shows_each_du_an_in_turn() -> None:
    a = [{"id": f"A{i}"} for i in range(30)]
    b = [{"id": "B0"}]
    shown = [x["id"] for x in interleave([a, b])][:20]
    assert shown[:3] == ["A0", "B0", "A1"]  # B is not hidden behind A's 30
    assert interleave([]) == []


class _Pipe:
    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts
        self.keys: list[str] = []

    def llen(self, key: str) -> None:
        self.keys.append(key)

    zcard = llen

    def execute(self) -> list[int]:
        return [self.counts.get(k, 0) for k in self.keys]


class _Reg:
    def __init__(self, key: str) -> None:
        self.key = key


class _Q:
    def __init__(self, name: str, conn: Any) -> None:
        self.key, self.connection = f"rq:queue:{name}", conn
        self.scheduled_job_registry = _Reg(f"rq:scheduled:{name}")
        self.started_job_registry = _Reg(f"rq:wip:{name}")


def test_nonempty_skips_empty_lanes_in_one_round_trip() -> None:
    counts = {"rq:queue:reviews:1": 3, "rq:wip:reviews:2": 1}

    class Conn:
        calls = 0

        def pipeline(self, transaction: bool = False) -> _Pipe:
            Conn.calls += 1
            return _Pipe(counts)

    conn = Conn()
    lanes = [_Q(n, conn) for n in ("reviews:0", "reviews:1", "reviews:2", "reviews")]
    assert [q.key for q in nonempty(lanes)] == ["rq:queue:reviews:1", "rq:queue:reviews:2"]
    assert Conn.calls == 1


def test_nonempty_keeps_every_lane_when_redis_cannot_say() -> None:
    class Broken:
        def pipeline(self, transaction: bool = False) -> Any:
            raise ConnectionError("down")

    lanes = [_Q("reviews:0", Broken())]
    assert nonempty(lanes) == lanes

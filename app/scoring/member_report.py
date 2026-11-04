"""The member report (docs/specs/23): how good one person's code is over a period, against the
period before and against everyone the viewer sees under the same filters. Pure functions over
`ReviewFacts`; the router loads, this module judges and shapes."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.scoring.criteria import CATEGORY_LABELS
from app.scoring.evaluation import ATTENTION_SCORE, Bucket, ReviewFacts, week_starts

GOOD_SCORE = 80.0  # the dashboard's green (Evaluation.tsx scoreTone)
GOOD_PASS_RATE = 60.0  # at least 3 in 5 judged changes approved
FEW_REVIEWS = 3  # fewer: the level is shown, flagged as thin evidence

LEVELS = {
    "good": "Tốt",
    "fair": "Cần cải thiện",
    "support": "Cần hỗ trợ",
    "none": "Chưa có dữ liệu",
}


@dataclass(frozen=True)
class Assessment:
    level: str  # good | fair | support | none
    label: str
    reasons: list[str]
    thin: bool  # few reviews: read as a hint, not a judgement

    def as_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "label": self.label,
            "reasons": self.reasons,
            "thin": self.thin,
        }


def _delta(a: float | None, b: float | None) -> float | None:
    return round(a - b, 1) if a is not None and b is not None else None


def _signed(value: float, digits: int = 1) -> str:
    text = f"{abs(value):.{digits}f}".rstrip("0").rstrip(".")
    return f"+{text}" if value > 0 else f"-{text}" if value < 0 else "0"


def assess(me: dict[str, Any], previous: dict[str, Any], peers: dict[str, Any]) -> Assessment:
    """The level and the reasons a reader needs to agree with it. `me`, `previous`, `peers` are
    `Bucket.as_dict()` of the person, the person in the period before, everyone in scope."""
    if not me["reviews"]:
        return Assessment("none", LEVELS["none"], ["Không có review nào trong khoảng này."], True)
    avg, rate = me["avg_score"], me["pass_rate"]
    if me["critical"] or (avg is not None and avg < ATTENTION_SCORE):
        level = "support"
    elif avg is None or avg < GOOD_SCORE or (rate is not None and rate < GOOD_PASS_RATE):
        level = "fair"
    else:
        level = "good"
    reasons: list[str] = []
    if avg is not None:
        line = f"Điểm TB {avg:.1f}/100 (theo LOC)"
        vs_peers = _delta(avg, peers.get("avg_score"))
        if vs_peers is not None and peers.get("members", 0) > 1:
            line += f", {_signed(vs_peers)} so với mặt bằng {peers['avg_score']:.1f}"
        vs_prev = _delta(avg, previous.get("avg_score"))
        if vs_prev is not None:
            line += f", {_signed(vs_prev)} so với kỳ trước"
        reasons.append(line + ".")
    if rate is not None:
        reasons.append(
            f"{rate:.0f}% thay đổi được kết luận APPROVE"
            + (
                f" (mặt bằng {peers['pass_rate']:.0f}%)."
                if peers.get("pass_rate") is not None and peers.get("members", 0) > 1
                else "."
            )
        )
    if me["critical"] or me["high"]:
        reasons.append(
            f"{me['critical']} critical, {me['high']} high được tính điểm"
            + (" — critical là lỗi phải sửa trước khi merge." if me["critical"] else ".")
        )
    else:
        reasons.append("Không có finding critical hoặc high.")
    density, peer_density = me["findings_per_100_loc"], peers.get("findings_per_100_loc")
    if density is not None:
        line = f"{density:.2f} lỗi / 100 dòng code"
        if peer_density is not None and peers.get("members", 0) > 1:
            more = density > peer_density * 1.2
            fewer = density < peer_density * 0.8
            line += (
                f" ({'cao hơn' if more else 'thấp hơn' if fewer else 'ngang'} mặt bằng"
                f" {peer_density:.2f})"
            )
        reasons.append(line + ".")
    thin = me["reviews"] < FEW_REVIEWS
    if thin:
        reasons.append(f"Chỉ có {me['reviews']} review: nên xem như gợi ý, chưa đủ để kết luận.")
    return Assessment(level, LEVELS[level], reasons, thin)


def weekly(
    facts: list[ReviewFacts], weeks: list[date], tz: ZoneInfo
) -> tuple[list[float | None], list[int]]:
    """Average score and review count per week (Mondays in `weeks`)."""
    buckets: dict[date, Bucket] = defaultdict(Bucket)
    for f in facts:
        day = (f.counted_at or f.finished_at).astimezone(tz).date()
        buckets[day - timedelta(days=day.weekday())].add(f)
    return (
        [buckets[w].avg_score if w in buckets else None for w in weeks],
        [buckets[w].reviews if w in buckets else 0 for w in weeks],
    )


def summarize(facts: list[ReviewFacts]) -> dict[str, Any]:
    total = Bucket()
    people: set[int] = set()
    for f in facts:
        total.add(f)
        people.add(f.user_id)
    return {**total.as_dict(), "members": len(people)}


def by_repo(facts: list[ReviewFacts]) -> list[dict[str, Any]]:
    repos: dict[int | None, Bucket] = defaultdict(Bucket)
    paths: dict[int | None, str | None] = {}
    for f in facts:
        repos[f.project_id].add(f)
        paths.setdefault(f.project_id, f.project_path)
    rows = [{"project_id": k, "path": paths[k], **b.as_dict()} for k, b in repos.items()]
    rows.sort(key=lambda r: (-r["reviews"], r["path"] or ""))
    return rows


def categories(facts: list[ReviewFacts], peer_facts: list[ReviewFacts]) -> list[dict[str, Any]]:
    """Kinds of counted findings, most first, with each kind's share of the person's findings
    and of everyone's (what the person does more often than the others)."""
    mine, all_ = Bucket(), Bucket()
    for f in facts:
        mine.add(f)
    for f in peer_facts:
        all_.add(f)
    total = sum(mine.categories.values()) or 1
    peer_total = sum(all_.categories.values()) or 1
    return [
        {
            "category": c,
            "label": CATEGORY_LABELS.get(c, c),
            "count": n,
            "share": round(100.0 * n / total, 1),
            "peer_share": round(100.0 * all_.categories.get(c, 0) / peer_total, 1),
        }
        for c, n in mine.categories.most_common()
    ]


def review_row(f: ReviewFacts) -> dict[str, Any]:
    """One review as the report lists it (counts are the scored findings, like the verdict)."""
    one = Bucket()
    one.add(f)
    return {
        "id": f.id,
        "target_type": f.target_type,
        "project_id": f.project_id,
        "repo": f.project_path,
        "finished_at": f.finished_at,
        "counted_at": f.counted_at or f.finished_at,
        "score": f.score,
        "verdict": f.verdict,
        "loc": f.loc,
        **{s: one.severity[s] for s in ("critical", "high", "medium", "low")},
    }


def weeks_of(start: date, end: date) -> list[date]:
    return week_starts(start, end)

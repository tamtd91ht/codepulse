"""Criteria scores in the database (docs/specs/22 §4): which sets a dự án applies, the score of
a review under a set version, stored in `review_criteria_scores`. The scoring itself is the
pure `app.scoring.criteria.score`."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import (
    CriteriaSet,
    CriteriaSetVersion,
    ProjectCriteriaSet,
    Review,
    ReviewCriteriaScore,
    ReviewFinding,
)
from app.scoring.criteria import Component, Components, CriteriaFinding, CriteriaScore, score
from app.scoring.formula import MIN_CONFIDENCE_UNVERIFIED, ScoredFinding, weights_for


@dataclass(frozen=True)
class SetVersion:
    """One version of a set, ready to score with."""

    set_id: int
    name: str
    version_id: int
    version: int
    components: list[Component]
    apply_caps: bool
    is_primary: bool = False
    position: int = 0

    @property
    def keys(self) -> set[str]:
        return {c.key for c in self.components}


def parse_components(raw: list[dict[str, Any]], apply_caps: bool) -> Components | None:
    try:
        return Components.model_validate({"components": raw, "apply_caps": apply_caps})
    except ValidationError:
        return None  # never written by the API; a hand-edited row is not scored


def as_set_version(
    criteria_set: CriteriaSet,
    version: CriteriaSetVersion,
    is_primary: bool = False,
    position: int = 0,
) -> SetVersion | None:
    parsed = parse_components(version.components, version.apply_caps)
    if parsed is None:
        return None
    return SetVersion(
        criteria_set.id,
        criteria_set.name,
        version.id,
        version.version,
        parsed.components,
        version.apply_caps,
        is_primary,
        position,
    )


def project_sets(session: Session, bp_id: int | None) -> list[SetVersion]:
    """The sets of dự án `bp_id` at their current version: primary first, then by position."""
    if bp_id is None:
        return []
    rows = session.execute(
        select(ProjectCriteriaSet, CriteriaSet, CriteriaSetVersion)
        .join(CriteriaSet, CriteriaSet.id == ProjectCriteriaSet.set_id)
        .join(CriteriaSetVersion, CriteriaSetVersion.id == CriteriaSet.current_version_id)
        .where(ProjectCriteriaSet.business_project_id == bp_id)
        .order_by(ProjectCriteriaSet.is_primary.desc(), ProjectCriteriaSet.position)
    ).tuples()
    out = [as_set_version(s, v, link.is_primary, link.position) for link, s, v in rows]
    return [s for s in out if s is not None]


def clean_criteria(
    placed: Mapping[str, str] | None, sets: Iterable[SetVersion]
) -> dict[str, str] | None:
    """The model's placements that name a set of the dự án and a key of that set; the rest is
    dropped (docs/specs/22 §4 "Kiểm tra đầu ra")."""
    if not placed:
        return None
    keys = {str(s.set_id): s.keys for s in sets}
    out = {k: v for k, v in placed.items() if k in keys and v in keys[k]}
    return out or None


def criteria_findings(findings: Iterable[ReviewFinding], set_id: int) -> list[CriteriaFinding]:
    return [
        CriteriaFinding(
            id=f.id,
            category=f.category,
            scored=ScoredFinding(
                f.severity, float(f.confidence or 1), f.status or "open", f.verified or "skipped"
            ),
            placed=(f.criteria or {}).get(str(set_id)),
        )
        for f in findings
    ]


def score_review(
    review: Review,
    findings: Iterable[ReviewFinding],
    version: SetVersion,
    weights_override: Mapping[str, float] | None = None,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
) -> CriteriaScore:
    return score(
        criteria_findings(findings, version.set_id),
        version.components,
        loc_reviewed=review.loc_reviewed or 0,
        weights=weights_for(review.rubric_version, weights_override),
        rubric_version=review.rubric_version,
        apply_caps=version.apply_caps,
        min_confidence_unverified=min_confidence_unverified,
    )


def _findings(session: Session, review_id: int) -> list[ReviewFinding]:
    return list(
        session.scalars(
            select(ReviewFinding)
            .where(ReviewFinding.review_id == review_id)
            .order_by(ReviewFinding.id)
        )
    )


def store_scores(
    session: Session,
    review: Review,
    versions: Iterable[SetVersion],
    weights_override: Mapping[str, float] | None = None,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
    findings: list[ReviewFinding] | None = None,
) -> int:
    """Score `review` under each version and upsert its row per set."""
    wanted = list(versions)
    if not wanted:
        return 0
    rows = findings if findings is not None else _findings(session, review.id)
    now = datetime.now(UTC)
    for version in wanted:
        result = score_review(review, rows, version, weights_override, min_confidence_unverified)
        values = {
            "review_id": review.id,
            "set_id": version.set_id,
            "version_id": version.version_id,
            "score": Decimal(str(result.score)),
            "breakdown": result.breakdown(),
            "computed_at": now,
        }
        session.execute(
            insert(ReviewCriteriaScore)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["review_id", "set_id"],
                set_={k: v for k, v in values.items() if k not in ("review_id", "set_id")},
            )
        )
    session.flush()
    return len(wanted)


def review_scores(session: Session, review_id: int, bp_id: int | None) -> list[dict[str, Any]]:
    """`criteria_scores` of GET /reviews/{id}: the stored scores, the dự án's primary set first,
    then its other sets by position, then sets it no longer has."""
    links = {
        link.set_id: link
        for link in session.scalars(
            select(ProjectCriteriaSet).where(ProjectCriteriaSet.business_project_id == bp_id)
        )
    }
    rows = session.execute(
        select(ReviewCriteriaScore, CriteriaSet, CriteriaSetVersion)
        .join(CriteriaSet, CriteriaSet.id == ReviewCriteriaScore.set_id)
        .join(CriteriaSetVersion, CriteriaSetVersion.id == ReviewCriteriaScore.version_id)
        .where(ReviewCriteriaScore.review_id == review_id)
    ).tuples()
    out = [
        {
            "set_id": s.id,
            "set_name": s.name,
            "version": version.version,
            "is_primary": bool(links.get(s.id) and links[s.id].is_primary),
            "score": row.score,
            "breakdown": row.breakdown,
        }
        for row, s, version in rows
    ]

    def order(item: dict[str, Any]) -> tuple[int, int, int]:
        link = links.get(item["set_id"])
        if link is None:
            return (2, 0, item["set_id"])
        return (0 if link.is_primary else 1, link.position, item["set_id"])

    return sorted(out, key=order)


def rescore_stored(
    session: Session,
    review: Review,
    weights_override: Mapping[str, float] | None = None,
    min_confidence_unverified: float = MIN_CONFIDENCE_UNVERIFIED,
) -> int:
    """Recompute the criteria scores a review has, each at the version it was stored with
    (a dispute, a closing, a verification changed a finding; docs/specs/22 §4)."""
    rows = session.execute(
        select(CriteriaSet, CriteriaSetVersion)
        .join(ReviewCriteriaScore, ReviewCriteriaScore.set_id == CriteriaSet.id)
        .join(CriteriaSetVersion, CriteriaSetVersion.id == ReviewCriteriaScore.version_id)
        .where(ReviewCriteriaScore.review_id == review.id)
    ).tuples()
    versions = [v for v in (as_set_version(s, ver) for s, ver in rows) if v is not None]
    return store_scores(session, review, versions, weights_override, min_confidence_unverified)

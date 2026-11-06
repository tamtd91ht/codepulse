"""Which CodePulse dự án a Jira issue counts for (docs/specs/27 §15).

A Jira project has scopes (`jira_project_scopes`): one dự án each, with component / label rules
or none. An issue goes to every scope whose rules it matches (a component or a label, case-
insensitive); matching none, to the scopes without rules ("every other issue"); with no such
scope either, to every dự án of the project, so no issue is lost. `issues.business_project_ids`
stores the result, NULL meaning "every dự án of its project": a project without rules then
needs nothing per issue, and its scopes can change without touching its issues.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import BusinessProject, Issue, JiraProject, JiraProjectScope

MAX_RULE_VALUES = 50  # components or labels of one scope
MAX_SCOPES = 20  # dự án of one Jira project


@dataclass(frozen=True)
class Rule:
    """One scope as routing reads it."""

    business_project_id: int
    components: tuple[str, ...] = ()
    labels: tuple[str, ...] = ()

    @property
    def catch_all(self) -> bool:
        return not self.components and not self.labels

    def matches(self, components: Iterable[str], labels: Iterable[str]) -> bool:
        mine = {c.strip().lower() for c in self.components}
        tags = {t.strip().lower() for t in self.labels}
        return bool(mine & {c.strip().lower() for c in components}) or bool(
            tags & {t.strip().lower() for t in labels}
        )


def route(
    rules: Sequence[Rule], components: Iterable[str] | None, labels: Iterable[str] | None
) -> list[int] | None:
    """The dự án ids of one issue, sorted; None: every dự án of its project (§15)."""
    ruled = [r for r in rules if not r.catch_all]
    if not ruled:
        return None
    comps, tags = list(components or []), list(labels or [])
    matched = {r.business_project_id for r in ruled if r.matches(comps, tags)}
    if not matched:
        matched = {r.business_project_id for r in rules if r.catch_all}
    return sorted(matched) if matched else None


def rules_of(session: Session, jira_project_id: int) -> list[Rule]:
    return [
        Rule(s.business_project_id, tuple(s.components or ()), tuple(s.labels or ()))
        for s in session.scalars(
            select(JiraProjectScope)
            .where(JiraProjectScope.jira_project_id == jira_project_id)
            .order_by(JiraProjectScope.position, JiraProjectScope.id)
        )
    ]


def jira_issue_in(bps: Iterable[int]) -> ColumnElement[bool]:
    """A Jira issue counts for one of `bps` (§15): routed there, or routed nowhere in particular
    (NULL) and its Jira project has a scope there."""
    wanted = sorted(set(bps)) or [0]
    return or_(
        Issue.business_project_ids.overlap(wanted),
        and_(
            Issue.business_project_ids.is_(None),
            exists().where(
                JiraProjectScope.jira_project_id == Issue.jira_project_id,
                JiraProjectScope.business_project_id.in_(wanted),
            ),
        ),
    )


def projects_in(bps: Iterable[int]) -> ColumnElement[bool]:
    """A Jira project has a scope in one of `bps`."""
    wanted = sorted(set(bps)) or [0]
    return exists().where(
        JiraProjectScope.jira_project_id == JiraProject.id,
        JiraProjectScope.business_project_id.in_(wanted),
    )


def recompute(session: Session, jira_project_id: int) -> int:
    """Route the project's stored issues again after its scopes changed; the caller commits.
    Returns the issues whose dự án changed."""
    rules = rules_of(session, jira_project_id)
    changed = 0
    rows = session.execute(
        select(Issue.id, Issue.components, Issue.labels, Issue.business_project_ids).where(
            Issue.jira_project_id == jira_project_id
        )
    ).all()
    targets: dict[tuple[int, ...] | None, list[int]] = {}
    for issue_id, components, labels, current in rows:
        routed = route(rules, components, labels)
        if (list(current) if current is not None else None) == routed:
            continue
        targets.setdefault(tuple(routed) if routed is not None else None, []).append(issue_id)
        changed += 1
    for routed_key, ids in targets.items():
        value = list(routed_key) if routed_key is not None else None
        for start in range(0, len(ids), 1000):
            session.execute(
                update(Issue)
                .where(Issue.id.in_(ids[start : start + 1000]))
                .values(business_project_ids=value)
                .execution_options(synchronize_session=False)
            )
    return changed


def components_missing(session: Session, jira_project_id: int) -> bool:
    """Issues stored before components were kept (§15): routing by component needs them
    fetched again."""
    return bool(
        session.scalar(
            select(
                exists().where(
                    Issue.jira_project_id == jira_project_id,
                    # SQL NULL, or a JSON null (a None written through the ORM).
                    or_(
                        Issue.components.is_(None),
                        func.jsonb_typeof(Issue.components) == "null",
                    ),
                )
            )
        )
    )


def preview(session: Session, jira_project_id: int, rules: Sequence[Rule]) -> dict[str, Any]:
    """How the stored issues of a project would split with `rules` (§15), without saving:
    issues per dự án, and the ones no rule matched (sent to the catch-all, else to all)."""
    groups = Counter[tuple[tuple[str, ...], tuple[str, ...]]]()
    for stored_components, stored_labels in session.execute(
        select(Issue.components, Issue.labels).where(Issue.jira_project_id == jira_project_id)
    ):
        groups[(tuple(stored_components or ()), tuple(stored_labels or ()))] += 1
    per: Counter[int] = Counter()
    unmatched = 0
    every = sorted({r.business_project_id for r in rules})
    ruled = any(not r.catch_all for r in rules)
    for (components, labels), n in groups.items():
        routed = route(rules, components, labels)
        for bp in routed if routed is not None else every:
            per[bp] += n
        if ruled and not any(r.matches(components, labels) for r in rules if not r.catch_all):
            unmatched += n
    return {
        "total": sum(groups.values()),
        "per_business_project": {str(k): v for k, v in sorted(per.items())},
        "unmatched": unmatched,
        "components_missing": components_missing(session, jira_project_id),
    }


def counts(session: Session, project_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    """For the projects list: issues per dự án as stored, and the ones that fell back to every
    dự án although the project has rules ("không khớp quy tắc")."""
    out: dict[int, dict[str, Any]] = {
        pid: {"per": Counter[int](), "fallback": 0} for pid in project_ids
    }
    if not project_ids:
        return out
    scopes: dict[int, list[JiraProjectScope]] = {pid: [] for pid in project_ids}
    for s in session.scalars(
        select(JiraProjectScope).where(JiraProjectScope.jira_project_id.in_(project_ids))
    ):
        scopes[s.jira_project_id].append(s)
    for pid, routed, n in session.execute(
        select(Issue.jira_project_id, Issue.business_project_ids, func.count())
        .where(Issue.jira_project_id.in_(project_ids))
        .group_by(Issue.jira_project_id, Issue.business_project_ids)
    ):
        if pid is None:
            continue
        entry = out[int(pid)]
        mine = scopes.get(int(pid), [])
        targets = list(routed) if routed is not None else [s.business_project_id for s in mine]
        for bp in targets:
            entry["per"][int(bp)] += int(n)
        if routed is None and any(s.components or s.labels for s in mine):
            entry["fallback"] += int(n)
    return out


def scopes_json(session: Session, project_ids: Sequence[int]) -> dict[int, list[dict[str, Any]]]:
    """The scopes of each project, in screen order, with their dự án's name."""
    out: dict[int, list[dict[str, Any]]] = {pid: [] for pid in project_ids}
    if not project_ids:
        return out
    for s, bp in session.execute(
        select(JiraProjectScope, BusinessProject)
        .join(BusinessProject, BusinessProject.id == JiraProjectScope.business_project_id)
        .where(JiraProjectScope.jira_project_id.in_(project_ids))
        .order_by(JiraProjectScope.position, JiraProjectScope.id)
    ):
        out[s.jira_project_id].append(
            {
                "business_project": {"id": bp.id, "name": bp.name},
                "components": list(s.components or []),
                "labels": list(s.labels or []),
            }
        )
    return out


def clean_values(values: Iterable[str], what: str) -> list[str]:
    """Trimmed, without repeats (case-insensitive), 1-200 characters each, at most 50."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw).strip()
        if not value:
            continue
        if len(value) > 200:
            raise ValueError(f"{what}: tối đa 200 ký tự mỗi giá trị")
        if value.lower() not in seen:
            seen.add(value.lower())
            out.append(value)
    if len(out) > MAX_RULE_VALUES:
        raise ValueError(f"{what}: tối đa {MAX_RULE_VALUES} giá trị")
    return out


def replace_scopes(session: Session, project: JiraProject, wanted: Sequence[Rule]) -> bool:
    """Make the project's scopes `wanted` (in that order). True when they changed."""
    before = rules_of(session, project.id)
    if list(before) == list(wanted):
        return False
    for old in session.scalars(
        select(JiraProjectScope).where(JiraProjectScope.jira_project_id == project.id)
    ):
        session.delete(old)
    session.flush()
    for position, rule in enumerate(wanted):
        session.add(
            JiraProjectScope(
                jira_project_id=project.id,
                business_project_id=rule.business_project_id,
                components=list(rule.components),
                labels=list(rule.labels),
                position=position,
            )
        )
    session.flush()
    return True

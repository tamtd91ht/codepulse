"""Links of test cases to Jira and code (docs/specs/30 §15.2 G2): the repos a code path may name,
the documents naming a Jira key (the reverse "Sinh testcase" from an issue) and the
traceability matrix with its CSV export.

Who: `tests.view` reads all of it (app/testcases/service.py).
"""

import csv
import io
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_session
from app.api.security import CurrentPrincipal, Principal
from app.docs import service as docs
from app.models import ProjectDocument
from app.testcases import links as tl
from app.testcases import service as tc
from app.testcases import sheets

router = APIRouter(tags=["testcases"])
SessionDep = Annotated[Session, Depends(get_session)]


class RepoOut(BaseModel):
    id: int
    path: str  # path_with_namespace: "{path}:{glob}"
    name: str  # its last part: "{name}:{glob}" works too
    folders: list[str]  # the main folders changed, "dir/ (n)"


class DocOut(BaseModel):
    id: int
    title: str
    keys: list[str]


class TraceCaseOut(BaseModel):
    id: int
    key: str
    title: str
    status: str
    suite: str
    run_id: int | None
    run_key: str | None
    run_name: str | None
    test_status: str | None
    defects: list[str]


class TraceRowOut(BaseModel):
    key: str
    title: str
    type: str | None
    state: str | None
    sprint: str | None
    labels: list[str]
    url: str | None
    cases: list[TraceCaseOut]


class TraceOut(BaseModel):
    rows: list[TraceRowOut]
    issues: int
    covered: int
    without_requirement: int  # live cases without a Jira key
    sprints: list[str]
    labels: list[str]
    total: int  # issues the pages go through (`uncovered` applied)
    limit: int
    offset: int


@router.get("/testcases/repos", response_model=list[RepoOut])
def repos(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    folders: bool = False,
) -> list[RepoOut]:
    """The dự án's repos, for the code path suggestions."""
    bp = tc.project_for(session, principal, business_project_id)
    return [
        RepoOut(id=r.id, path=r.path, name=r.name, folders=r.folders)
        for r in tl.repos(session, [bp.id], folders=folders)
    ]


@router.get("/testcases/issue-docs", response_model=list[DocOut])
def issue_docs(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    key: Annotated[str, Query(min_length=3, max_length=40)],
) -> list[DocOut]:
    """The dự án's documents naming a Jira key (picked with it to write its cases)."""

    bp = tc.project_for(session, principal, business_project_id)
    keys = tc.jira_keys(key)
    out = []
    for hit in tl.documents_mentioning(session, bp.id, keys):
        doc = session.get(ProjectDocument, hit.id)
        if doc is not None and docs.may_view_doc(session, principal, doc):
            out.append(DocOut(id=hit.id, title=hit.title, keys=hit.keys))
    return out


def _trace(
    session: Session,
    principal: Principal,
    business_project_id: int,
    milestone_id: int | None,
    sprint: str | None,
    label: str | None,
    q: str | None,
    days: int | None,
    uncovered: bool,
    limit: int = tl.TRACE_PAGE,
    offset: int = 0,
) -> tl.Trace:
    bp = tc.project_for(session, principal, business_project_id)
    return tl.traceability(
        session,
        bp.id,
        milestone_id=milestone_id,
        sprint=sprint or None,
        label=label or None,
        q=(q or "").strip() or None,
        days=days,
        uncovered=uncovered,
        limit=limit,
        offset=offset,
    )


Text = Annotated[str | None, Query(max_length=200)]
Days = Annotated[int | None, Query(ge=1, le=3650)]
Limit = Annotated[int, Query(ge=1, le=tl.TRACE_ISSUES)]
Offset = Annotated[int, Query(ge=0, le=1_000_000)]


@router.get("/testcases/traceability", response_model=TraceOut)
def traceability(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    milestone_id: int | None = None,
    sprint: Text = None,
    label: Text = None,
    q: Text = None,
    days: Days = None,
    uncovered: bool = False,
    limit: Limit = tl.TRACE_PAGE,
    offset: Offset = 0,
) -> TraceOut:
    """Jira issue -> linked cases -> their latest test (in the milestone's runs when given) and
    defects. `sprint` is the issue's sprint or version, `label` one of its labels (an epic is
    a label here: the Jira sync keeps no epic link). One page of issues (`limit`, `offset`);
    `total` is the number of issues the pages go through."""
    trace = _trace(
        session,
        principal,
        business_project_id,
        milestone_id,
        sprint,
        label,
        q,
        days,
        uncovered,
        limit,
        offset,
    )
    return TraceOut(
        rows=[
            TraceRowOut(
                key=r.key,
                title=r.title,
                type=r.type,
                state=r.state,
                sprint=r.sprint,
                labels=r.labels,
                url=r.url,
                cases=[TraceCaseOut(**c.__dict__) for c in r.cases],
            )
            for r in trace.rows
        ],
        issues=trace.issues,
        covered=trace.covered,
        without_requirement=trace.without_requirement,
        sprints=trace.sprints,
        labels=trace.labels,
        total=trace.total,
        limit=trace.limit,
        offset=trace.offset,
    )


@router.get("/testcases/traceability/export")
def traceability_export(
    principal: CurrentPrincipal,
    session: SessionDep,
    business_project_id: int,
    milestone_id: int | None = None,
    sprint: Text = None,
    label: Text = None,
    q: Text = None,
    days: Days = None,
    uncovered: bool = False,
) -> Response:
    """Every issue matching the filters (up to TRACE_EXPORT), read page by page."""
    trace = _trace(
        session,
        principal,
        business_project_id,
        milestone_id,
        sprint,
        label,
        q,
        days,
        uncovered,
        tl.TRACE_ISSUES,
        0,
    )
    while len(trace.rows) < min(trace.total, tl.TRACE_EXPORT):
        more = _trace(
            session,
            principal,
            business_project_id,
            milestone_id,
            sprint,
            label,
            q,
            days,
            uncovered,
            tl.TRACE_ISSUES,
            len(trace.rows),
        )
        if not more.rows:
            break
        trace.rows += more.rows
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in tl.csv_rows(trace):
        # A cell starting like a formula (or a tab / CR) is quoted so a spreadsheet does not run it.
        writer.writerow([sheets._safe(v) for v in row])
    return Response(
        content=("﻿" + buf.getvalue()).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="ma-tran-truy-vet.csv"'},
    )

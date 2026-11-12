"""Links of test cases to Jira and code (docs/specs/30 §15.2 G2): "{repo}:{glob}" code paths on
suites and sections inherited by their cases (with "!" removal), repo validation, AI link
suggestions accepted in one go with a revision each, the reverse generation from a Jira issue,
documents naming Jira keys, and the traceability matrix."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.docs import service as docs
from app.jira.settings import JIRA_ID_OFFSET
from app.models import (
    Commit,
    CommitFile,
    Issue,
    JiraProject,
    JiraProjectScope,
    MrCommit,
    Project,
    ProjectDocument,
    ProjectGrant,
    TestAiRun,
    TestCaseRevision,
)
from app.review.providers.fake import FakeLLMProvider
from app.testcases import ai
from app.testcases import impact as imp
from tests.api.conftest import FakeQueue, World

API = "/api/v1"


def _suite(client: TestClient, h: dict[str, str], bp: int, name: str = "CSKH") -> Any:
    got = client.post(
        f"{API}/testcases/suites", json={"business_project_id": bp, "name": name}, headers=h
    )
    assert got.status_code == 201, got.text
    return got.json()


def _section(client: TestClient, h: dict[str, str], suite: int, name: str, **kw: Any) -> Any:
    got = client.post(
        f"{API}/testcases/sections", json={"suite_id": suite, "name": name, **kw}, headers=h
    )
    assert got.status_code == 201, got.text
    return got.json()


def _case(client: TestClient, h: dict[str, str], suite: int, title: str, **kw: Any) -> Any:
    got = client.post(f"{API}/testcases", json={"suite_id": suite, "title": title, **kw}, headers=h)
    assert got.status_code == 201, got.text
    return got.json()


def _mr_files(db_session: Session, world: World, paths: list[str]) -> None:
    mr = world.mr
    mr.merged_at = datetime.now(UTC) - timedelta(days=1)
    mr.target_branch = "main"
    sha = "d" * 40
    db_session.add(Commit(sha=sha, project_id=world.project.id))
    db_session.flush()
    db_session.add(MrCommit(mr_id=mr.id, sha=sha))
    for path in paths:
        db_session.add(CommitFile(sha=sha, path=path, additions=5, deletions=1))
    db_session.flush()


def _other_repo(db_session: Session, world: World, path: str = "omicx/billing") -> Project:
    repo = Project(
        connection_id=world.project.connection_id,
        external_id=2002,
        path_with_namespace=path,
        business_project_id=world.project.business_project_id,
    )
    db_session.add(repo)
    db_session.flush()
    return repo


def _jira(db_session: Session, bp: int, numbers: list[int], **fields: Any) -> list[Issue]:
    project = db_session.scalar(select(JiraProject).where(JiraProject.key == "OMX"))
    if project is None:
        project = JiraProject(key="OMX", name="Omicx")
        db_session.add(project)
        db_session.flush()
        db_session.add(JiraProjectScope(jira_project_id=project.id, business_project_id=bp))
        db_session.flush()
    out = []
    for n in numbers:
        issue = Issue(
            id=JIRA_ID_OFFSET + project.id * 1000 + n,
            source="jira",
            jira_project_id=project.id,
            iid=n,
            key=f"OMX-{n}",
            title=f"Khách hàng việc {n}",
            created_at_src=datetime.now(UTC) - timedelta(days=n),
            **fields,
        )
        db_session.add(issue)
        out.append(issue)
    db_session.flush()
    return out


def _brd(db_session: Session, world: World, bp: int, title: str, body: str) -> ProjectDocument:
    doc = ProjectDocument(business_project_id=bp, title=title, created_by=world.admin.id)
    db_session.add(doc)
    db_session.flush()
    docs.add_version(db_session, doc, body, source="editor", user_id=world.admin.id)
    db_session.flush()
    return doc


def _ai(db_session: Session, run_id: int, answer: dict[str, Any]) -> FakeLLMProvider:
    row = db_session.get(TestAiRun, run_id)
    assert row is not None
    provider = FakeLLMProvider(responder=lambda prompt: answer, cost_usd=0.01)
    status = ai.run(
        db_session,
        run_id,
        row.requested_at,
        web=lambda url: ("Trang", "nội dung"),
        gitlab=lambda conn: None,  # type: ignore[arg-type,return-value]
        provider=lambda: provider,
        over_budget=lambda: False,
        audit_cost=lambda cost, model: None,
    )
    assert status == "ready", db_session.get(TestAiRun, run_id).error  # type: ignore[union-attr]
    db_session.expire_all()
    return provider


def test_folder_code_paths_inherited_by_cases(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    """AC1: code_paths on the folder "Khách hàng" -> an MR changing a matching file lists the
    folder's cases in "Cần test lại"; another repo's glob does not match; "!" removes."""
    bp = world.project.business_project_id
    assert bp is not None
    admin, an = as_user(world.admin), as_user(world.an)
    _other_repo(db_session, world)
    suite = _suite(client, admin, bp)
    folder = _section(client, admin, suite["id"], "Khách hàng")
    sub = _section(client, admin, suite["id"], "Thêm", parent_id=folder["id"])
    other = _section(client, admin, suite["id"], "Hoá đơn")
    in_folder = _case(client, admin, suite["id"], "Thêm khách hàng", section_id=sub["id"])
    opted_out = _case(
        client,
        admin,
        suite["id"],
        "Thêm khách hàng (bỏ vùng)",
        section_id=sub["id"],
        code_paths=["!p:src/customer/**"],
    )
    billing = _case(client, admin, suite["id"], "Xuất hoá đơn", section_id=other["id"])

    # Unknown repo: refused, in Vietnamese; "!" is for cases only.
    bad = client.patch(
        f"{API}/testcases/sections/{folder['id']}",
        json={"code_paths": ["khong-co:src/**"]},
        headers=admin,
    )
    assert bad.status_code == 422 and "không thuộc dự án" in bad.json()["detail"]
    banged = client.patch(
        f"{API}/testcases/sections/{folder['id']}", json={"code_paths": ["!src/**"]}, headers=admin
    )
    assert banged.status_code == 422
    # A case is checked the same way.
    refused = client.post(
        f"{API}/testcases",
        json={"suite_id": suite["id"], "title": "x", "code_paths": ["nope:a/**"]},
        headers=admin,
    )
    assert refused.status_code == 422
    # The section takes the short name or the full path; the folder of the other repo too.
    ok = client.patch(
        f"{API}/testcases/sections/{folder['id']}",
        json={"code_paths": ["p:src/customer/**"]},
        headers=admin,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["code_paths"] == ["p:src/customer/**"]
    assert (
        client.patch(
            f"{API}/testcases/sections/{other['id']}",
            json={"code_paths": ["omicx/billing:src/invoice/**"]},
            headers=admin,
        ).status_code
        == 200
    )
    # The suite area: a bare glob (any repo of the dự án).
    patched = client.patch(
        f"{API}/testcases/suites/{suite['id']}",
        json={"code_paths": ["docs/cskh/**"]},
        headers=admin,
    )
    assert patched.status_code == 200 and patched.json()["code_paths"] == ["docs/cskh/**"]

    repos = client.get(
        f"{API}/testcases/repos", params={"business_project_id": bp}, headers=an
    ).json()
    assert {(r["path"], r["name"]) for r in repos} == {
        ("demo/p", "p"),
        ("omicx/billing", "billing"),
    }

    # demo/p changes src/customer/... and src/invoice/... (the invoice glob is billing's).
    _mr_files(db_session, world, ["src/customer/create.ts", "src/invoice/pdf.ts"])
    impact = client.get(f"{API}/testcases/impact/mr/{world.mr.id}", headers=an).json()
    ids = {c["case"]["id"] for c in impact["cases"]}
    assert in_folder["id"] in ids
    assert opted_out["id"] not in ids and billing["id"] not in ids
    hit = next(c for c in impact["cases"] if c["case"]["id"] == in_folder["id"])
    assert hit["reasons"] == ["src/customer/create.ts khớp p:src/customer/**"]
    release = client.get(
        f"{API}/testcases/regression", params={"business_project_id": bp, "days": 7}, headers=an
    ).json()
    assert {c["case"]["id"] for c in release["cases"]} == {in_folder["id"]}

    # The suite's bare glob reaches every case unless removed by a bare "!".
    db_session.add(CommitFile(sha="d" * 40, path="docs/cskh/readme.md"))
    db_session.flush()
    again = client.get(f"{API}/testcases/impact/mr/{world.mr.id}", headers=an).json()
    assert {c["case"]["id"] for c in again["cases"]} == {
        in_folder["id"],
        opted_out["id"],
        billing["id"],
    }


def test_effective_paths_rules() -> None:
    assert imp.effective_paths(["a/**", "r:b/**"], ["c/**"]) == ["a/**", "r:b/**", "c/**"]
    assert imp.effective_paths(["a/**", "r:b/**"], ["!r:b/**"]) == ["a/**"]
    assert imp.effective_paths(["a/**", "r:a/**"], ["!a/**"]) == []  # a bare "!" in every repo


def test_ai_links_accept_all_with_revisions(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    """AC2: the AI suggests Jira keys and code paths for the cases; one call accepts them all,
    each case gets a revision."""
    bp = world.project.business_project_id
    assert bp is not None
    admin, an = as_user(world.admin), as_user(world.an)
    _jira(db_session, bp, [1, 2])
    _mr_files(db_session, world, ["src/customer/create.ts", "src/customer/list.ts"])
    _brd(db_session, world, bp, "BRD Khách hàng", "Yêu cầu OMX-1: thêm khách hàng.")
    # A repo that may not go to an external LLM: never named in the prompt.
    secret = _other_repo(db_session, world, "omicx/secret-vault")
    secret.allow_external_llm = False
    db_session.flush()
    suite = _suite(client, admin, bp)
    cases = [_case(client, admin, suite["id"], f"Thêm khách hàng {i}") for i in range(3)]
    body = {"kind": "links", "business_project_id": bp, "suite_id": suite["id"]}
    assert client.post(f"{API}/testcases/ai", json=body, headers=an).status_code == 403
    asked = client.post(f"{API}/testcases/ai", json=body, headers=admin)
    assert asked.status_code == 201, asked.text
    run = asked.json()
    answer = {
        "summary": "Gắn theo BRD",
        "links": [
            {
                "id": c["id"],
                "jira_keys": ["OMX-1", "XYZ-9"],  # XYZ-9 is not the dự án's
                "code_paths": ["p:src/customer/**", "nope:src/**"],  # nope is no repo
                "reason": "Màn Khách hàng",
            }
            for c in cases
        ]
        + [{"id": 999_999, "jira_keys": ["OMX-2"], "code_paths": [], "reason": "bịa"}],
    }
    provider = _ai(db_session, run["id"], answer)
    prompt = provider.prompts[0].user
    assert "<cases>" in prompt and "OMX-1: Khách hàng việc 1" in prompt
    assert "repo p (demo/p)" in prompt and "src/customer/" in prompt
    assert "BRD Khách hàng: OMX-1" in prompt
    assert "secret-vault" not in prompt
    got = client.get(f"{API}/testcases/ai/{run['id']}", headers=admin).json()
    links = got["result"]["links"]
    assert [x["id"] for x in links] == [c["id"] for c in cases]
    assert links[0]["jira_keys"] == ["OMX-1"] and links[0]["code_paths"] == ["p:src/customer/**"]
    assert links[0]["current_jira"] == []

    # The generic apply refuses; a case not suggested is refused.
    assert (
        client.post(
            f"{API}/testcases/ai/{run['id']}/apply", json={"items": [{"index": 0}]}, headers=admin
        ).status_code
        == 422
    )
    items = [
        {"id": x["id"], "jira_keys": x["jira_keys"], "code_paths": x["code_paths"]} for x in links
    ]
    assert (
        client.post(
            f"{API}/testcases/ai/{run['id']}/apply-links",
            json={"items": [{"id": 999_999, "jira_keys": ["OMX-2"]}]},
            headers=admin,
        ).status_code
        == 422
    )
    assert (
        client.post(
            f"{API}/testcases/ai/{run['id']}/apply-links", json={"items": items}, headers=an
        ).status_code
        == 403
    )
    applied = client.post(
        f"{API}/testcases/ai/{run['id']}/apply-links", json={"items": items}, headers=admin
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["changed"] == 3
    for c in applied.json()["cases"]:
        assert c["jira_keys"] == ["OMX-1"] and c["code_paths"] == ["p:src/customer/**"]
        assert c["revision"] == 2
    revisions = db_session.scalar(
        select(func.count())
        .select_from(TestCaseRevision)
        .where(TestCaseRevision.case_id.in_([c["id"] for c in cases]), TestCaseRevision.via == "ai")
    )
    assert revisions == 3

    # AC3: coverage moves at once (computed live).
    stats = client.get(f"{API}/testcases/stats", params={"business_project_id": bp}, headers=an)
    assert stats.json()["jira_covered"] == 1 and stats.json()["jira_issues"] == 2
    assert [u["key"] for u in stats.json()["uncovered"]] == ["OMX-2"]


def test_generate_from_issue_with_its_documents(
    client: TestClient, world: World, as_user: Any, db_session: Session, queue: FakeQueue
) -> None:
    """The reverse way: an uncovered issue -> a generate run with the key and the documents
    naming it; Jira keys a document names (of the dự án) are offered to the proposals."""
    bp = world.project.business_project_id
    assert bp is not None
    admin, an = as_user(world.admin), as_user(world.an)
    _jira(db_session, bp, [7, 8])
    brd = _brd(db_session, world, bp, "BRD_KhachHang", "OMX-7 thêm khách hàng; OMX-8 xoá; ABC-1.")
    _brd(db_session, world, bp, "Ghi chú khác", "Không nhắc mã nào.")
    suite = _suite(client, admin, bp)

    found = client.get(
        f"{API}/testcases/issue-docs",
        params={"business_project_id": bp, "key": "OMX-7"},
        headers=an,
    )
    assert [d["title"] for d in found.json()] == ["BRD_KhachHang"]

    asked = client.post(
        f"{API}/testcases/ai",
        json={
            "kind": "generate",
            "business_project_id": bp,
            "suite_id": suite["id"],
            "jira_keys": ["OMX-7"],
            "auto_docs": True,
        },
        headers=admin,
    )
    assert asked.status_code == 201, asked.text
    run = asked.json()
    assert run["input"]["doc_ids"] == [brd.id] and run["input"]["jira_keys"] == ["OMX-7"]
    proposal = {
        "title": "Thêm khách hàng hợp lệ",
        "steps_list": [{"content": "Nhập tên", "expected": "Lưu thành công"}],
        "priority": "high",
        "jira_keys": ["OMX-7", "OMX-8", "ABC-1"],
    }
    provider = _ai(db_session, run["id"], {"summary": "ok", "cases": [proposal]})
    assert "Tài liệu nhắc các mã Jira của dự án: OMX-7, OMX-8" in provider.prompts[0].user
    got = client.get(f"{API}/testcases/ai/{run['id']}", headers=admin).json()
    # OMX-8 comes from the document; ABC-1 is not an issue of the dự án.
    assert got["result"]["cases"][0]["jira_keys"] == ["OMX-7", "OMX-8"]


def test_traceability_matrix(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin, an = as_user(world.admin), as_user(world.an)
    issues = _jira(db_session, bp, [1, 2, 3], milestone="Sprint 5", labels=["khach-hang"])
    issues[1].title = "\t=HYPERLINK(1)"
    issues[2].title = "\r@cmd"
    db_session.flush()
    suite = _suite(client, admin, bp)
    linked = _case(client, admin, suite["id"], "Thêm khách hàng", jira_keys=["OMX-1"])
    _case(client, admin, suite["id"], "Không gắn yêu cầu")
    run = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"case_ids": [linked["id"]]}},
        headers=admin,
    )
    assert run.status_code == 201, run.text
    tests = client.get(f"{API}/testcases/runs/{run.json()['id']}/tests", headers=admin).json()
    test_id = tests["items"][0]["id"]
    recorded = client.post(
        f"{API}/testcases/tests/{test_id}/results",
        json={"status": "failed", "defects": ["OMX-99"]},
        headers=admin,
    )
    assert recorded.status_code in (200, 201), recorded.text
    # A newer run where the case is not tested yet: the latest result stays the tested one.
    newer = client.post(
        f"{API}/testcases/runs",
        json={"business_project_id": bp, "selection": {"case_ids": [linked["id"]]}},
        headers=admin,
    )
    assert newer.status_code == 201, newer.text

    got = client.get(
        f"{API}/testcases/traceability", params={"business_project_id": bp}, headers=an
    )
    assert got.status_code == 200, got.text
    trace = got.json()
    assert trace["issues"] == 3 and trace["covered"] == 1 and trace["without_requirement"] == 1
    assert trace["total"] == 3 and trace["offset"] == 0
    assert trace["sprints"] == ["Sprint 5"] and trace["labels"] == ["khach-hang"]
    # Paged by issue (newest first); counts are over every matching issue, not the page.
    page = client.get(
        f"{API}/testcases/traceability",
        params={"business_project_id": bp, "limit": 1, "offset": 1},
        headers=an,
    ).json()
    assert [r["key"] for r in page["rows"]] == ["OMX-2"]
    assert page["total"] == 3 and page["issues"] == 3 and page["covered"] == 1
    gaps = client.get(
        f"{API}/testcases/traceability",
        params={"business_project_id": bp, "uncovered": True, "limit": 1},
        headers=an,
    ).json()
    assert [r["key"] for r in gaps["rows"]] == ["OMX-2"] and gaps["total"] == 2
    assert (
        client.get(
            f"{API}/testcases/traceability",
            params={"business_project_id": bp, "limit": 501},
            headers=an,
        ).status_code
        == 422
    )
    row = next(r for r in trace["rows"] if r["key"] == "OMX-1")
    [case] = row["cases"]
    assert case["key"] == linked["key"] and case["test_status"] == "failed"
    assert case["run_key"] == f"R{run.json()['id']}" and case["defects"] == ["OMX-99"]
    only = client.get(
        f"{API}/testcases/traceability",
        params={"business_project_id": bp, "uncovered": True, "q": "OMX-2"},
        headers=an,
    ).json()
    assert [r["key"] for r in only["rows"]] == ["OMX-2"]
    none = client.get(
        f"{API}/testcases/traceability",
        params={"business_project_id": bp, "sprint": "Sprint 9"},
        headers=an,
    ).json()
    assert none["rows"] == []
    csv = client.get(
        f"{API}/testcases/traceability/export", params={"business_project_id": bp}, headers=an
    )
    assert csv.status_code == 200 and "OMX-99" in csv.content.decode("utf-8-sig")
    # Cells starting with a tab or CR are guarded like "=" (sheets._safe).
    text = csv.content.decode("utf-8-sig")
    assert "'\t=HYPERLINK(1)" in text and "'\r@cmd" in text
    assert "OMX-1" in text and "OMX-3" in text
    # Outside the dự án: not found.
    outsider = as_user(world.outsider)
    db_session.execute(
        delete(ProjectGrant).where(
            ProjectGrant.business_project_id == bp, ProjectGrant.user_id == world.outsider.id
        )
    )
    db_session.flush()
    assert (
        client.get(
            f"{API}/testcases/traceability", params={"business_project_id": bp}, headers=outsider
        ).status_code
        == 404
    )

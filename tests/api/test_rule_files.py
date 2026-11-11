"""Review rules imported as files into a dự án (docs/specs/17 §Tệp quy tắc, user 2026-11-11):
upload several files (.md, .txt, .doc, .docx), replace by name, switch off, reorder, delete; the
files switched on reach the review prompt as their own sources."""

import io
import pathlib
import zipfile
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditLog, ReviewRuleFile
from app.review.rules import collect_rules, rule_files
from tests.api.conftest import World

DOC = pathlib.Path("tests/fixtures/docs/rules.doc")


def _docx(paragraphs: list[tuple[str, str]]) -> bytes:
    """A minimal .docx: (style, text) paragraphs."""
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(
        f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>'
        if style
        else f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>"
        for style, text in paragraphs
    )
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{w}">'
        f"<w:body>{body}</w:body></w:document>"
    )
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("word/document.xml", xml)
    return out.getvalue()


def _upload(client: TestClient, headers: dict[str, str], bp: int, name: str, data: bytes) -> Any:
    return client.post(
        f"/api/v1/business-projects/{bp}/rule-files",
        params={"filename": name},
        content=data,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )


def test_import_several_files_and_use_them_in_reviews(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    md = _upload(client, admin, bp, "security.md", "## Bảo mật\n- Không log token".encode())
    txt = _upload(client, admin, bp, "naming.txt", "Tên biến tiếng Anh, camelCase.".encode())
    docx = _upload(
        client, admin, bp, "money.docx", _docx([("Heading1", "Tiền"), ("", "Cộng tiền atomic")])
    )
    doc = _upload(client, admin, bp, "tenant.doc", DOC.read_bytes())
    for r in (md, txt, docx, doc):
        assert r.status_code == 201, r.text
        assert r.json()["replaced"] is False
    listed = client.get(f"/api/v1/business-projects/{bp}/rule-files", headers=admin).json()
    assert [f["name"] for f in listed["files"]] == [
        "security.md",
        "naming.txt",
        "money.docx",
        "tenant.doc",
    ]
    assert listed["budget"] > listed["files_chars"] > 0 and ".doc" in listed["kinds"]
    detail = client.get(
        f"/api/v1/business-projects/{bp}/rule-files/{docx.json()['file']['id']}", headers=admin
    ).json()
    assert detail["body"] == "# Tiền\n\nCộng tiền atomic"
    tenant = client.get(
        f"/api/v1/business-projects/{bp}/rule-files/{doc.json()['file']['id']}", headers=admin
    ).json()
    assert "tenantId" in tenant["body"] and "| critical | SQL injection |" in tenant["body"]

    # Same name: a new version of the same file.
    again = _upload(client, admin, bp, "security.md", "## Bảo mật\n- Không log mật khẩu".encode())
    assert again.status_code == 201 and again.json()["replaced"] is True
    rows = db_session.scalars(
        select(ReviewRuleFile).where(ReviewRuleFile.business_project_id == bp)
    ).all()
    assert len(rows) == 4

    # Off, reordered: the prompt follows.
    naming = txt.json()["file"]["id"]
    assert (
        client.patch(
            f"/api/v1/business-projects/{bp}/rule-files/{naming}",
            json={"enabled": False},
            headers=admin,
        ).json()["enabled"]
        is False
    )
    files = rule_files(db_session, bp)
    assert [n for n, _ in files] == ["security.md", "money.docx", "tenant.doc"]
    text, sources = collect_rules(None, world.project, lambda p: None, files=files)
    assert "## Tệp quy tắc security.md\n## Bảo mật\n- Không log mật khẩu" in text
    assert [s["kind"] for s in sources][:3] == ["rule_file"] * 3

    assert (
        client.delete(
            f"/api/v1/business-projects/{bp}/rule-files/{naming}", headers=admin
        ).status_code
        == 204
    )
    actions = [
        a.action
        for a in db_session.scalars(
            select(AuditLog).where(AuditLog.action.like("business_project.rule_file.%"))
        )
    ]
    assert actions.count("business_project.rule_file.add") == 4
    assert {"business_project.rule_file.replace", "business_project.rule_file.delete"} <= set(
        actions
    )


def test_refusals(client: TestClient, world: World, as_user: Any) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    assert _upload(client, admin, bp, "a.pdf", b"%PDF-1.4").status_code == 422
    assert _upload(client, admin, bp, "a.doc", b"not a word file").status_code == 422
    assert _upload(client, admin, bp, "empty.md", b"   ").status_code == 422
    assert _upload(client, as_user(world.an), bp, "a.md", b"# x").status_code == 403
    assert _upload(client, admin, 10**6, "a.md", b"# x").status_code == 404
    assert (
        client.get(f"/api/v1/business-projects/{bp}/rule-files/999999", headers=admin).status_code
        == 404
    )

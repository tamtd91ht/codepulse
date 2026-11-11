"""Tài liệu dự án (docs/specs/29): who reads and edits, upload of each kind, versions and
conflicts, restore, search, archive and delete."""

import io
import pathlib
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import AuditLog, ProjectDocumentVersion, ProjectGrant
from tests.api.conftest import World, grant

DOC = pathlib.Path("tests/fixtures/docs/rules.doc")


def _pdf(text: str) -> bytes:
    """A one-page PDF with a text layer, written by hand (no extra dependency)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )
    return out.getvalue()


def _upload(
    client: TestClient, headers: dict[str, str], bp: int, name: str, data: bytes, **q: Any
) -> Any:
    return client.post(
        "/api/v1/docs/upload",
        params={"business_project_id": bp, "filename": name, **q},
        content=data,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )


def test_upload_read_search_and_versions(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin, an = as_user(world.admin), as_user(world.an)
    grant(db_session, world.an, bp, "view")
    db_session.commit()

    md = _upload(
        client,
        admin,
        bp,
        "kien-truc.md",
        "# Kiến trúc thanh toán\n\n## Luồng hoàn tiền\nHoàn tiền phải idempotent.".encode(),
        category="architecture",
    )
    assert md.status_code == 201, md.text
    doc = md.json()
    assert doc["title"] == "Kiến trúc thanh toán" and doc["category"] == "architecture"
    assert doc["version"] == 1 and doc["current"]["has_original"] and doc["can_edit"]

    pdf = _upload(client, admin, bp, "runbook.pdf", _pdf("Restart the payment worker"))
    assert pdf.status_code == 201, pdf.text
    assert pdf.json()["current"]["meta"] == {"pages": 1}
    assert "--- Trang 1 ---" in pdf.json()["body"]
    word = _upload(client, admin, bp, "quy-tac.doc", DOC.read_bytes(), category="conventions")
    assert word.status_code == 201, word.text
    assert "tenantId" in word.json()["body"]
    txt = _upload(client, admin, bp, "ghi-chu.txt", b"dong 1\ndong 2")
    assert txt.json()["current"]["meta"] == {"plain": True}

    # A member of the dự án reads, cannot write.
    listed = client.get("/api/v1/docs", params={"business_project_id": bp}, headers=an).json()
    assert listed["can_edit"] is False and len(listed["docs"]) == 4
    assert 0 < listed["coverage"] < 1
    assert client.get(f"/api/v1/docs/{doc['id']}", headers=an).json()["can_edit"] is False
    denied = _upload(client, an, bp, "x.md", b"# x")
    assert denied.status_code == 403
    projects = client.get("/api/v1/docs/projects", headers=an).json()
    assert {p["id"]: p["docs"] for p in projects}[bp] == 4

    # Search: accent-free, best section first, its heading given.
    hits = client.get("/api/v1/docs/search", params={"q": "hoan tien"}, headers=an).json()
    assert hits[0]["document_id"] == doc["id"]
    assert hits[0]["heading"].endswith("Luồng hoàn tiền") and "idempotent" in hits[0]["snippet"]

    # The original, as uploaded; a PDF can be shown inline.
    original = client.get(
        f"/api/v1/docs/{pdf.json()['id']}/versions/1/original", params={"inline": 1}, headers=an
    )
    assert original.status_code == 200 and original.content.startswith(b"%PDF")
    assert original.headers["content-disposition"].startswith("inline")
    download = client.get(f"/api/v1/docs/{doc['id']}/versions/1/original", headers=an)
    assert download.headers["content-disposition"].startswith("attachment")
    assert "sandbox" in download.headers["content-security-policy"]

    # Edits are versions; a stale editor gets 409; the same text makes no new version.
    path = f"/api/v1/docs/{doc['id']}/content"
    v2 = client.put(path, json={"body": "# Kiến trúc\n\nBản 2", "base_version": 1}, headers=admin)
    assert v2.status_code == 200 and v2.json()["version"] == 2
    stale = client.put(path, json={"body": "# x", "base_version": 1}, headers=admin)
    assert stale.status_code == 409
    same = client.put(path, json={"body": "# Kiến trúc\n\nBản 2", "base_version": 2}, headers=admin)
    assert same.json()["version"] == 2
    restored = client.post(f"/api/v1/docs/{doc['id']}/versions/1/restore", headers=admin)
    assert restored.json()["version"] == 3 and "idempotent" in restored.json()["body"]
    versions = client.get(f"/api/v1/docs/{doc['id']}/versions", headers=an).json()
    assert [(v["number"], v["source"]) for v in versions] == [
        (3, "restore"),
        (2, "editor"),
        (1, "upload"),
    ]
    assert versions[0]["has_original"] is True
    old = client.get(f"/api/v1/docs/{doc['id']}/versions/2", headers=an).json()
    assert old["body"] == "# Kiến trúc\n\nBản 2"

    # Rename (search follows), archive, delete.
    client.patch(f"/api/v1/docs/{doc['id']}", json={"title": "Sơ đồ hệ thống"}, headers=admin)
    hits = client.get("/api/v1/docs/search", params={"q": "so do"}, headers=an).json()
    assert hits and hits[0]["document_id"] == doc["id"]
    assert client.delete(f"/api/v1/docs/{doc['id']}", headers=admin).status_code == 409
    client.patch(f"/api/v1/docs/{doc['id']}", json={"archived": True}, headers=admin)
    archived = client.get("/api/v1/docs/search", params={"q": "so do"}, headers=an).json()
    assert doc["id"] not in [h["document_id"] for h in archived]
    assert client.delete(f"/api/v1/docs/{doc['id']}", headers=admin).status_code == 204
    left = db_session.scalars(
        select(ProjectDocumentVersion).where(ProjectDocumentVersion.document_id == doc["id"])
    ).all()
    assert left == []
    actions = {
        a.action for a in db_session.scalars(select(AuditLog).where(AuditLog.action.like("doc.%")))
    }
    assert {"doc.upload", "doc.edit", "doc.restore", "doc.update", "doc.delete"} <= actions


def test_templates_outsiders_and_refusals(
    client: TestClient, world: World, as_user: Any, db_session: Session
) -> None:
    bp = world.project.business_project_id
    assert bp is not None
    admin = as_user(world.admin)
    meta = client.get("/api/v1/docs/meta", headers=admin).json()
    assert ".pdf" in meta["kinds"] and len(meta["templates"]) == 8
    tpl = meta["templates"][0]
    created = client.post(
        "/api/v1/docs",
        json={
            "business_project_id": bp,
            "title": tpl["title"],
            "category": tpl["category"],
            "body": tpl["body"],
            "template": tpl["key"],
        },
        headers=admin,
    )
    assert created.status_code == 201, created.text
    assert created.json()["current"]["source"] == "template"
    # Someone outside the dự án: it does not exist for them.
    db_session.execute(
        delete(ProjectGrant).where(
            ProjectGrant.business_project_id == bp, ProjectGrant.user_id == world.outsider.id
        )
    )
    db_session.commit()
    outsider = as_user(world.outsider)
    assert client.get(f"/api/v1/docs/{created.json()['id']}", headers=outsider).status_code == 404
    assert (
        client.get("/api/v1/docs/search", params={"q": "tong quan"}, headers=outsider).json() == []
    )
    assert _upload(client, admin, bp, "a.exe", b"MZ").status_code == 422
    assert _upload(client, admin, bp, "scan.pdf", b"%PDF-1.4 broken").status_code == 422
    assert _upload(client, admin, bp, "empty.md", b"").status_code == 422

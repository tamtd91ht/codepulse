"""The check of a close request sent through the MCP server shows the author's note, files
changed and tests to the model as untrusted data; a web request's note is not shown
(docs/specs/26 §8)."""

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api.routers.closures import create_close_request
from app.api.security import load_principal
from app.config import Settings
from app.review.providers import FakeLLMProvider
from tests.api.conftest import World
from tests.api.test_closures import FIX, FIXED, run, token_of, work

pytestmark = pytest.mark.integration

NOTE = "Đã đổi câu SQL sang tham số bind thay vì nối chuỗi, thêm test cho đầu vào có dấu nháy."
META = {
    "files_changed": ["app/a.py"],
    "tests": [{"command": "pytest tests/test_a.py", "result": "passed", "summary": "4 passed"}],
    "token_id": 1,
    "token_name": "laptop",
    "client": "claude-code/2.0",
}


def asked_prompt(
    client: TestClient,
    world: World,
    as_user: Any,
    db_session: Session,
    settings: Settings,
    via: str,
) -> str:
    principal = load_principal(db_session, world.an.id, settings)
    closure = create_close_request(
        db_session,
        principal,
        world.an_finding.id,
        [FIX],
        None,
        NOTE,
        via=via,
        meta=META if via == "mcp" else None,
    )
    db_session.commit()
    assert run(client, as_user, world.manager, closure.id).status_code == 202
    provider = FakeLLMProvider(lambda p: {"answers": [FIXED]})
    status = work(
        db_session, settings, closure.id, token_of(db_session, closure.id), None, provider=provider
    )
    assert status == "closed"  # the verdict rules are unchanged
    return provider.prompts[0].user


def test_an_mcp_request_shows_its_claims_inside_the_tags(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    user = asked_prompt(client, world, as_user, db_session, settings, "mcp")
    found = re.search(r"BEGIN-CLAIMS-(\w+)\n(.*?)\nEND-CLAIMS-\1", user, re.S)
    assert found is not None
    block = found.group(2)
    assert NOTE in block
    assert "- app/a.py" in block
    assert "- pytest tests/test_a.py: passed (4 passed)" in block
    assert "laptop" not in user and "claude-code" not in user


def test_a_web_request_note_is_not_shown(
    client: TestClient, world: World, as_user: Any, db_session: Session, settings: Settings
) -> None:
    user = asked_prompt(client, world, as_user, db_session, settings, "web")
    assert "CLAIMS" not in user and NOTE not in user

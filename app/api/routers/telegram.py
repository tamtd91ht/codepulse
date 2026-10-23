"""Telegram bots per dự án (admin) and the per-person account link (outside the spec,
requested by the user; see PROGRESS.md and docs/specs/15_TELEGRAM_BOT.md)."""

import logging
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.audit import audit
from app.api.deps import get_secret_box, get_session
from app.api.schemas import (
    TelegramAccountOut,
    TelegramBotIn,
    TelegramBotOut,
    TelegramBotPatch,
    TelegramLinkCodeOut,
    TelegramRequestOut,
)
from app.api.security import AdminPrincipal, CurrentPrincipal
from app.config import Settings, get_settings
from app.crypto import SecretBox
from app.db import get_redis
from app.models import Account, BusinessProject, Project, TelegramBot, TelegramRequest, User
from app.telegram.client import TelegramClient, TelegramError
from app.telegram.service import LINK_CODE_TTL_S, TELEGRAM_KIND, LinkCodes, RedisLinkCodes

log = logging.getLogger(__name__)
router = APIRouter(tags=["telegram"])
SessionDep = Annotated[Session, Depends(get_session)]

TelegramClientFactory = Callable[[str], TelegramClient]


def get_telegram_client_factory(
    settings: Annotated[Settings, Depends(get_settings)],
) -> TelegramClientFactory:
    """Overridden in tests with a fake Bot API."""
    return lambda token: TelegramClient(token, api_base=settings.telegram_api_base, max_retries=1)


def get_link_codes() -> LinkCodes:
    return RedisLinkCodes(get_redis())


def _get[M](session: Session, model: type[M], ident: int, name: str) -> M:
    row = session.get(model, ident)
    if row is None:
        raise HTTPException(404, f"{name} not found")
    return row


def _check_scope(session: Session, business_project_id: int, project_id: int | None) -> None:
    _get(session, BusinessProject, business_project_id, "dự án")
    if project_id is not None:
        project = _get(session, Project, project_id, "repo")
        if project.business_project_id != business_project_id:
            raise HTTPException(422, "repo does not belong to this dự án")


def _bot_username(make_client: TelegramClientFactory, token: str) -> str:
    """getMe with the new token: the token is checked before it is stored."""
    try:
        with make_client(token) as client:
            me = client.get_me()
    except TelegramError as exc:
        raise HTTPException(422, f"Telegram rejected the token ({exc})") from None
    return str(me.get("username") or "")


def _out(bot: TelegramBot) -> TelegramBotOut:
    return TelegramBotOut.model_validate(bot)


# -- admin ----------------------------------------------------------------------------------------


@router.get("/telegram/bots", response_model=list[TelegramBotOut])
def list_bots(principal: AdminPrincipal, session: SessionDep) -> list[TelegramBotOut]:
    return [_out(b) for b in session.scalars(select(TelegramBot).order_by(TelegramBot.id))]


@router.post("/telegram/bots", response_model=TelegramBotOut, status_code=201)
def create_bot(
    body: TelegramBotIn,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    make_client: Annotated[TelegramClientFactory, Depends(get_telegram_client_factory)],
) -> TelegramBotOut:
    _check_scope(session, body.business_project_id, body.project_id)
    username = _bot_username(make_client, body.token)
    bot = TelegramBot(
        name=body.name,
        bot_username=username,
        token_encrypted=box.encrypt(body.token),
        business_project_id=body.business_project_id,
        project_id=body.project_id,
        enabled=body.enabled,
        allowed_chat_ids=body.allowed_chat_ids or None,
    )
    session.add(bot)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "this dự án (or repo) already has a bot") from None
    audit(
        session,
        principal.user_id,
        "telegram.bot.create",
        f"telegram_bot:{bot.id}",
        business_project_id=bot.business_project_id,
        project_id=bot.project_id,
        bot_username=username,
    )
    session.commit()
    return _out(bot)


@router.patch("/telegram/bots/{bot_id}", response_model=TelegramBotOut)
def update_bot(
    bot_id: int,
    body: TelegramBotPatch,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    make_client: Annotated[TelegramClientFactory, Depends(get_telegram_client_factory)],
) -> TelegramBotOut:
    bot = _get(session, TelegramBot, bot_id, "bot")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(422, "nothing to update")
    if "project_id" in changes:
        _check_scope(session, bot.business_project_id, body.project_id)
        bot.project_id = body.project_id
    if body.name is not None:
        bot.name = body.name
    if body.enabled is not None:
        bot.enabled = body.enabled
        if body.enabled:
            bot.last_error = None
    if "allowed_chat_ids" in changes:
        bot.allowed_chat_ids = body.allowed_chat_ids or None
    if body.token is not None:
        bot.bot_username = _bot_username(make_client, body.token)
        bot.token_encrypted = box.encrypt(body.token)
        bot.last_update_id = None  # a new bot starts from its own update stream
        bot.last_error = None
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "this dự án (or repo) already has a bot") from None
    audit(
        session,
        principal.user_id,
        "telegram.bot.update",
        f"telegram_bot:{bot.id}",
        fields=sorted(changes),  # names only, never the token
    )
    session.commit()
    return _out(bot)


@router.delete("/telegram/bots/{bot_id}", status_code=204)
def delete_bot(bot_id: int, principal: AdminPrincipal, session: SessionDep) -> None:
    bot = _get(session, TelegramBot, bot_id, "bot")
    session.delete(bot)
    audit(session, principal.user_id, "telegram.bot.delete", f"telegram_bot:{bot_id}")
    session.commit()


@router.post("/telegram/bots/{bot_id}/test")
def test_bot(
    bot_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    box: Annotated[SecretBox, Depends(get_secret_box)],
    make_client: Annotated[TelegramClientFactory, Depends(get_telegram_client_factory)],
) -> dict[str, Any]:
    """One getMe with the stored token; the token itself is never returned."""
    bot = _get(session, TelegramBot, bot_id, "bot")
    try:
        with make_client(box.decrypt(bot.token_encrypted)) as client:
            me = client.get_me()
        ok, error = True, None
        bot.bot_username = str(me.get("username") or bot.bot_username or "")
    except (TelegramError, ValueError) as exc:
        ok, error, me = False, str(exc)[:300], {}
    audit(session, principal.user_id, "telegram.bot.test", f"telegram_bot:{bot.id}", ok=ok)
    session.commit()
    return {
        "ok": ok,
        "error": error,
        "bot_username": bot.bot_username,
        "can_join_groups": me.get("can_join_groups"),
        "last_polled_at": bot.last_polled_at,
        "last_error": bot.last_error,
    }


@router.get("/telegram/bots/{bot_id}/requests", response_model=list[TelegramRequestOut])
def list_requests(
    bot_id: int,
    principal: AdminPrincipal,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[TelegramRequestOut]:
    _get(session, TelegramBot, bot_id, "bot")
    rows = session.execute(
        select(TelegramRequest, User)
        .outerjoin(User, User.id == TelegramRequest.user_id)
        .where(TelegramRequest.bot_id == bot_id)
        .order_by(TelegramRequest.id.desc())
        .limit(limit)
    ).all()
    return [
        TelegramRequestOut(
            id=r.id,
            chat_id=r.chat_id,
            user_id=r.user_id,
            user_name=u.full_name if u else None,
            command=r.command,
            targets=r.targets,
            status=r.status,
            created_at=r.created_at,
            notified_at=r.notified_at,
        )
        for r, u in rows
    ]


# -- the person's own link ------------------------------------------------------------------------


@router.get("/me/telegram", response_model=list[TelegramAccountOut])
def my_telegram(principal: CurrentPrincipal, session: SessionDep) -> list[Account]:
    return list(
        session.scalars(
            select(Account)
            .where(Account.kind == TELEGRAM_KIND, Account.user_id == principal.user_id)
            .order_by(Account.id)
        )
    )


@router.post("/me/telegram/link-code", response_model=TelegramLinkCodeOut)
def my_link_code(
    principal: CurrentPrincipal,
    session: SessionDep,
    codes: Annotated[LinkCodes, Depends(get_link_codes)],
) -> TelegramLinkCodeOut:
    """A one-time code the person sends to the bot as /link CODE (valid 15 minutes)."""
    code = codes.issue(principal.user_id)
    audit(session, principal.user_id, "telegram.link_code", f"user:{principal.user_id}")
    session.commit()
    return TelegramLinkCodeOut(code=code, expires_in_s=LINK_CODE_TTL_S)


@router.delete("/me/telegram/{account_id}", status_code=204)
def my_unlink(account_id: int, principal: CurrentPrincipal, session: SessionDep) -> None:
    account = _get(session, Account, account_id, "account")
    if account.kind != TELEGRAM_KIND or account.user_id != principal.user_id:
        raise HTTPException(404, "account not found")
    session.delete(account)
    audit(session, principal.user_id, "telegram.unlink", f"account:{account_id}")
    session.commit()

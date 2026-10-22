from collections.abc import Iterator

from rq import Queue
from sqlalchemy.orm import Session

from app.crypto import SecretBox, secret_box
from app.db import get_redis, get_sessionmaker


def get_session() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session


def get_queue() -> Queue:
    return Queue("default", connection=get_redis())


def get_secret_box() -> SecretBox:
    return secret_box()

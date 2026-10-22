from functools import lru_cache

from redis import Redis
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(str(get_settings().database_url), pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker:  # type: ignore[type-arg]
    return sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(str(get_settings().redis_url))

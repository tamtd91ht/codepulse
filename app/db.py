from functools import lru_cache

from redis import Redis
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker

from app.audit_trail import install as install_audit_trail
from app.config import get_settings

# docs/specs/30 §15.14 (G12): the testcase audit trail listens on every Session.
install_audit_trail()


@lru_cache
def get_engine() -> Engine:
    from app import dblabel, dbtiming

    engine = create_engine(str(get_settings().database_url), pool_pre_ping=True)
    dblabel.install(engine)  # pg_stat_activity.application_name says which flow (app/dblabel.py)
    dbtiming.install(
        engine
    )  # every transaction timed; a slow one rings the admins (app/dbtiming.py)
    return engine


@lru_cache
def get_sessionmaker() -> sessionmaker:  # type: ignore[type-arg]
    return sessionmaker(get_engine(), expire_on_commit=False)


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(str(get_settings().redis_url))

"""Log one line per dependency at start-up: can this container reach PostgreSQL and Redis?

Run before migrations (`python -m app.startup_check`); it never fails the start itself, so the
real error still comes from alembic/uvicorn. Credentials are never logged, only host:port.
"""

import logging

from redis import Redis
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.logging import configure_logging

log = logging.getLogger("app.startup_check")
TIMEOUT_S = 5


def _reason(exc: Exception) -> str:
    """The driver's own message (e.g. 'No route to host'), without SQLAlchemy's SQL and links."""
    lines = str(getattr(exc, "orig", None) or exc).strip().splitlines()
    return lines[0] if lines else exc.__class__.__name__


def check_postgres(url: str) -> bool:
    target = make_url(url)
    where = {"target": f"{target.host}:{target.port or 5432}/{target.database}"}
    engine = create_engine(url, connect_args={"connect_timeout": TIMEOUT_S})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        log.error("postgres unreachable", extra={**where, "reason": _reason(exc)})
        return False
    finally:
        engine.dispose()
    log.info("postgres connected", extra=where)
    return True


def check_redis(url: str) -> bool:
    client = Redis.from_url(url, socket_connect_timeout=TIMEOUT_S, socket_timeout=TIMEOUT_S)
    where = {
        "target": f"{client.connection_pool.connection_kwargs.get('host')}:"
        f"{client.connection_pool.connection_kwargs.get('port')}"
    }
    try:
        client.ping()
    except Exception as exc:
        log.error("redis unreachable", extra={**where, "reason": _reason(exc)})
        return False
    finally:
        client.close()
    log.info("redis connected", extra=where)
    return True


def main() -> None:
    configure_logging()
    settings = get_settings()
    check_postgres(str(settings.database_url))
    check_redis(str(settings.redis_url))


if __name__ == "__main__":
    main()

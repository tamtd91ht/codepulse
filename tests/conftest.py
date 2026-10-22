import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

# Ryuk (the reaper container) races Docker Desktop's port mapping; containers are
# stopped by their context manager instead.
os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

# Tests never read a developer's .env; settings come from these fixed values instead.
os.environ["CODEPULSE_ENV_FILE"] = ""
os.environ.update(
    {
        "DATABASE_URL": "postgresql+psycopg://codepulse:codepulse@localhost:5432/codepulse",
        "REDIS_URL": "redis://localhost:6379/0",
        "APP_ENCRYPTION_KEY": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
    }
)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """A throwaway PostgreSQL 16 (Docker via testcontainers), shared by the session."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer("postgres:16", driver="psycopg") as pg:
        yield pg.get_connection_url()


def alembic_config(url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture(scope="session")
def migrated_engine(postgres_url: str) -> Iterator[Engine]:
    command.upgrade(alembic_config(postgres_url), "head")
    engine = create_engine(postgres_url)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(migrated_engine: Engine) -> Iterator[Session]:
    """A session whose work is rolled back after the test, so tests never see each other's rows."""
    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()

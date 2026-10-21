import os
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_session
from app.main import app as fastapi_app
from tests.support import CHART

TEST_DATABASE_URL = os.getenv(
    "LEDGER_TEST_DATABASE_URL",
    "postgresql+psycopg://ledger:ledger@localhost:5433/ledger_test",
)

TABLES = "journal_lines, journal_entries, accounts"


def _create_database_if_missing() -> None:
    url = make_url(TEST_DATABASE_URL)
    if url.database is None:
        raise RuntimeError(f"test URL has no database name: {TEST_DATABASE_URL}")

    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        exists = connection.scalar(
            text("select 1 from pg_database where datname = :name"), {"name": url.database}
        )
        if exists is None:
            # Database identifiers cannot be bound as parameters. The name comes
            # from a URL the test suite controls, not from user input.
            connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    admin.dispose()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    _create_database_if_missing()

    config = Config("alembic.ini")
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(config, "head")

    test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    yield test_engine
    test_engine.dispose()


@pytest.fixture(autouse=True)
def _clean_tables(engine: Engine) -> Iterator[None]:
    # TRUNCATE skips the append-only row triggers, which is the whole point of
    # using it here instead of DELETE.
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    yield


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:
    def override() -> Iterator[Session]:
        with session_factory() as session:
            yield session

    fastapi_app.dependency_overrides[get_session] = override
    with TestClient(fastapi_app) as test_client:
        yield test_client
    fastapi_app.dependency_overrides.clear()


@pytest.fixture
def chart(client: TestClient) -> dict[str, Any]:
    """Account code to id for the five accounts most tests touch."""
    ids: dict[str, Any] = {}
    for code, name, account_type in CHART:
        response = client.post("/accounts", json={"code": code, "name": name, "type": account_type})
        assert response.status_code == 201, response.text
        ids[code] = response.json()["id"]
    return ids

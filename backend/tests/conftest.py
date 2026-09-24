"""Database tests share an isolated, rollback-only PostgreSQL schema."""

import os
from uuid import uuid4

import pytest

from app.config import Settings
from app.db.schema import metadata
from app.db.session import make_engine


@pytest.fixture
def connection():
    url = os.environ.get("COPILOT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set COPILOT_TEST_DATABASE_URL for PostgreSQL integration tests")
    engine = make_engine(Settings(_env_file=None, database_url=url))
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            schema = "test_copilot_" + uuid4().hex
            try:
                connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
                connection = connection.execution_options(schema_translate_map={None: schema})
                metadata.create_all(connection)
                yield connection
            finally:
                transaction.rollback()
    finally:
        engine.dispose()

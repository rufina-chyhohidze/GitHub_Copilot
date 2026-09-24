"""Check the current migration head without hard-coding a revision in the CLI."""

from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Engine


def check_database(engine: Engine) -> None:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    heads = set(ScriptDirectory.from_config(config).get_heads())
    with engine.connect() as connection:
        vector = connection.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one_or_none()
        current = set(MigrationContext.configure(connection).get_current_heads())
        if not vector or current != heads:
            raise ValueError("Database is not ready; run uv run alembic upgrade head")

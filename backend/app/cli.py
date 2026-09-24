"""A small executable check before introducing an HTTP server."""

import argparse
import json
import logging

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.session import make_engine
from app.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(prog="repo-copilot")
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke", help="Validate configuration and optionally the database")
    smoke.add_argument("--database", action="store_true", help="Check migrations and pgvector")
    smoke.add_argument("--provider", action="store_true", help="Validate provider settings only")
    args = parser.parse_args()
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        if args.provider:
            settings.require_provider()
        if args.database:
            engine = make_engine(settings)
            try:
                with engine.connect() as connection:
                    version = connection.execute(
                        text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
                    ).scalar_one_or_none()
                    revision = connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    ).scalar_one_or_none()
                    if not version or revision != "0001_enable_vector":
                        raise ValueError("Database is not ready; run uv run alembic upgrade head")
            finally:
                engine.dispose()
        logging.getLogger("app.cli").info("smoke_check_passed")
        print(json.dumps({"status": "ok", "database_checked": args.database}))
        return 0
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors())
        parser.exit(2, f"Invalid configuration: {fields}. Check .env.example.\n")
    except ValueError as exc:
        parser.exit(2, f"{exc}\n")
    except SQLAlchemyError:
        parser.exit(
            2,
            "Database check failed. Check COPILOT_DATABASE_URL, start Docker/PostgreSQL, "
            "and run uv run alembic upgrade head.\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())

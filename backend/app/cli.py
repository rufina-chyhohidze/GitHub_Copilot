"""A small executable check before introducing an HTTP server."""

import argparse
import asyncio
import json
import logging
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.db.health import check_database
from app.db.session import make_engine
from app.ingestion.clone import canonical_url, validate_ref
from app.ingestion.parsing import parse_snapshot
from app.ingestion.service import ingest
from app.logging import configure_logging
from app.providers.embeddings import OpenAIEmbeddings
from app.retrieval.embedding_store import build_index
from app.retrieval.service import retrieve
from app.tools.repository import RepositoryTools


def main() -> int:
    parser = argparse.ArgumentParser(prog="repo-copilot")
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser("smoke", help="Validate configuration and optionally the database")
    smoke.add_argument("--database", action="store_true", help="Check migrations and pgvector")
    smoke.add_argument("--provider", action="store_true", help="Validate provider settings only")
    ingestion = commands.add_parser("ingest", help="Save source from a public GitHub commit")
    ingestion.add_argument("repo_url")
    ingestion.add_argument("--ref", default="HEAD", help="Branch, tag, or full commit SHA")
    parsing = commands.add_parser("parse", help="Parse and chunk a stored source snapshot")
    parsing.add_argument("snapshot_id", type=UUID)
    for name in ("tree", "read", "search-code", "symbols", "find-symbol", "embed", "search"):
        command = commands.add_parser(name)
        command.add_argument("snapshot_id", type=UUID)
        command.add_argument("--run-id", type=UUID, help="Pin a parsing run; defaults to latest")
        if name == "tree":
            command.add_argument("--path", default="")
        elif name in {"read", "symbols"}:
            command.add_argument("path")
            if name == "read":
                command.add_argument("--start", type=int, default=1)
                command.add_argument("--end", type=int)
        elif name in {"search-code", "find-symbol", "search"}:
            command.add_argument("query")
            if name == "search-code":
                command.add_argument("--ignore-case", action="store_true")
            if name == "search":
                command.add_argument(
                    "--mode", choices=["lexical", "semantic", "hybrid"], default="lexical"
                )
                command.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        if args.command in {
            "tree",
            "read",
            "search-code",
            "symbols",
            "find-symbol",
            "embed",
            "search",
        }:
            engine = make_engine(settings)
            try:
                check_database(engine)
                if args.command == "embed":
                    result = asyncio.run(
                        build_index(
                            engine,
                            args.snapshot_id,
                            settings,
                            OpenAIEmbeddings(settings),
                            args.run_id,
                        )
                    )
                elif args.command == "search":
                    provider = OpenAIEmbeddings(settings) if args.mode != "lexical" else None
                    result = asyncio.run(
                        retrieve(
                            engine,
                            args.snapshot_id,
                            args.query,
                            settings,
                            mode=args.mode,
                            provider=provider,
                            run_id=args.run_id,
                            limit=args.limit,
                        )
                    )
                else:
                    with engine.connect() as connection:
                        tools = RepositoryTools(connection, args.snapshot_id, args.run_id)
                        if args.command == "tree":
                            result = tools.get_repository_tree(args.path)
                        elif args.command == "read":
                            result = tools.read_file(args.path, args.start, args.end)
                        elif args.command == "search-code":
                            result = tools.search_code(
                                args.query, case_sensitive=not args.ignore_case
                            )
                        elif args.command == "symbols":
                            result = tools.get_file_symbols(args.path)
                        else:
                            result = tools.find_symbol(args.query)
            finally:
                engine.dispose()
            print(json.dumps(result, indent=2, default=str))
            return 0
        if args.command == "parse":
            engine = make_engine(settings)
            try:
                check_database(engine)
                result = parse_snapshot(args.snapshot_id, settings, engine)
            finally:
                engine.dispose()
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "ingest":
            url, ref = canonical_url(args.repo_url), validate_ref(args.ref)
            engine = make_engine(settings)
            try:
                check_database(engine)
                result = ingest(url, ref, settings, engine)
            finally:
                engine.dispose()
            logging.getLogger("app.cli").info("repository_ingested")
            print(json.dumps(result, indent=2))
            return 0
        if args.provider:
            settings.require_provider()
        if args.database:
            engine = make_engine(settings)
            try:
                check_database(engine)
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
    except OSError:
        parser.exit(2, "Ingestion filesystem error; check temporary disk space and permissions.\n")
    except SQLAlchemyError:
        parser.exit(
            2,
            "Database operation failed. Check COPILOT_DATABASE_URL, start Docker/PostgreSQL, "
            "and run uv run alembic upgrade head.\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())

"""FastAPI transport for persisted indexing and published immutable snapshots."""

import asyncio
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.answering.pipeline import answer
from app.config import Settings
from app.conversations.api import install as install_conversations
from app.db.schema import index_jobs, repositories, repository_files, snapshots
from app.db.session import make_engine
from app.ingestion.clone import canonical_url, validate_ref
from app.jobs.deletion import delete_repository
from app.jobs.service import enqueue, public_job, ready_index
from app.models.contracts import Contract
from app.providers.embeddings import OpenAIEmbeddings
from app.providers.text import OpenAITextModel
from app.retrieval.embedding_store import profile
from app.tools.repository import RepositoryTools, literal_query


class IndexRequest(Contract):
    ref: str = Field(default="HEAD", min_length=1, max_length=255)
    _ref = field_validator("ref")(validate_ref)


class RepositoryRequest(IndexRequest):
    url: str = Field(min_length=1, max_length=256)
    _url = field_validator("url")(canonical_url)


class QuestionRequest(Contract):
    pipeline: Literal["fixed", "agent"] = "fixed"
    question: str = Field(min_length=1, max_length=512)
    _question = field_validator("question")(literal_query)


def create_app(
    settings=None,
    engine=None,
    *,
    embedding_factory=OpenAIEmbeddings,
    text_factory=OpenAITextModel,
    planner_factory=None,
):
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings if settings is not None else Settings()
        app.state.engine = engine if engine is not None else make_engine(app.state.settings)
        try:
            yield
        finally:
            if engine is None:
                app.state.engine.dispose()

    app = FastAPI(title="Repository Copilot", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        codes = {
            404: "not_found",
            409: "snapshot_not_ready",
            503: "service_unavailable",
            502: "answer_failed",
        }
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": codes.get(exc.status_code, "request_error"),
                    "message": str(exc.detail),
                }
            },
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Request fields are invalid.",
                    "fields": [
                        {"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()
                    ],
                }
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "database_unavailable",
                    "message": "Database unavailable; check service and migrations.",
                }
            },
        )

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(
            status_code=400,
            content={
                "error": {
                    "code": "invalid_request",
                    "message": "Invalid path, source range, or operation limits.",
                }
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "The request could not be completed.",
                }
            },
        )

    def require_repository(connection, repository_id):
        row = (
            connection.execute(select(repositories).where(repositories.c.id == repository_id))
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "Repository not found.")
        return row

    def snapshot_metadata(connection, snapshot_id):
        row = (
            connection.execute(
                select(
                    snapshots.c.id,
                    snapshots.c.repository_id,
                    snapshots.c.commit_sha,
                    snapshots.c.index_version,
                    snapshots.c.status,
                    snapshots.c.coverage,
                    snapshots.c.created_at,
                ).where(snapshots.c.id == snapshot_id)
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise HTTPException(404, "Snapshot not found.")
        return row

    def require_ready(connection, snapshot_id, profile_id=None):
        snapshot_metadata(connection, snapshot_id)
        publication = ready_index(connection, snapshot_id, profile_id)
        if publication is None:
            raise HTTPException(
                409, "Snapshot has no published index for this operation. Check its indexing job."
            )
        return publication

    def submit(connection, url, ref):
        try:
            embedding_factory(app.state.settings)  # Validate configuration without a provider call.
        except ValueError:
            raise HTTPException(
                503, "Configure the embedding provider before submitting indexing jobs."
            ) from None
        job = enqueue(connection, url, ref, app.state.settings)
        return {
            "repository_id": job["repository_id"],
            "job_id": job["id"],
            "status": job["status"],
            "job_url": f"/index-jobs/{job['id']}",
        }

    @app.get("/health")
    def health():
        with app.state.engine.connect() as connection:
            connection.execute(select(1))
        return {"status": "ok"}

    @app.post("/repositories", status_code=202)
    def register(body: RepositoryRequest):
        with app.state.engine.begin() as connection:
            return submit(connection, body.url, body.ref)

    @app.get("/repositories")
    def list_repositories(
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
    ):
        with app.state.engine.connect() as connection:
            rows = (
                connection.execute(
                    select(repositories)
                    .order_by(repositories.c.created_at.desc(), repositories.c.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
                .mappings()
                .all()
            )
            return {
                "items": [dict(row) for row in rows[:limit]],
                "next_offset": offset + limit if len(rows) > limit else None,
            }

    @app.delete("/repositories/{repository_id}")
    def remove_repository(repository_id: UUID):
        with app.state.engine.begin() as connection:
            delete_repository(connection, repository_id)
        return {"deleted": True}

    @app.get("/repositories/{repository_id}")
    def repository(repository_id: UUID):
        with app.state.engine.connect() as connection:
            row = dict(require_repository(connection, repository_id))
            latest = connection.execute(
                select(snapshots.c.id)
                .where(snapshots.c.repository_id == repository_id)
                .order_by(snapshots.c.created_at.desc(), snapshots.c.id)
                .limit(1)
            ).scalar_one_or_none()
            ready = connection.execute(
                select(snapshots.c.id)
                .where(snapshots.c.repository_id == repository_id, snapshots.c.status == "ready")
                .order_by(snapshots.c.created_at.desc(), snapshots.c.id)
                .limit(1)
            ).scalar_one_or_none()
            row.update(latest_snapshot_id=latest, latest_ready_snapshot_id=ready)
            return row

    @app.post("/repositories/{repository_id}/index", status_code=202)
    def reindex(repository_id: UUID, body: IndexRequest):
        with app.state.engine.begin() as connection:
            row = require_repository(connection, repository_id)
            return submit(connection, row["canonical_url"], body.ref)

    @app.get("/index-jobs/{job_id}")
    def job(job_id: UUID):
        with app.state.engine.connect() as connection:
            row = (
                connection.execute(select(index_jobs).where(index_jobs.c.id == job_id))
                .mappings()
                .one_or_none()
            )
            if row is None:
                raise HTTPException(404, "Indexing job not found.")
            return public_job(row)

    @app.get("/repositories/{repository_id}/snapshots")
    def list_snapshots(
        repository_id: UUID,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0, le=1000000),
    ):
        with app.state.engine.connect() as connection:
            require_repository(connection, repository_id)
            ids = (
                connection.execute(
                    select(snapshots.c.id)
                    .where(snapshots.c.repository_id == repository_id)
                    .order_by(snapshots.c.created_at.desc(), snapshots.c.id)
                    .offset(offset)
                    .limit(limit + 1)
                )
                .scalars()
                .all()
            )
            return {
                "items": [snapshot_metadata(connection, sid) for sid in ids[:limit]],
                "next_offset": offset + limit if len(ids) > limit else None,
            }

    @app.get("/snapshots/{snapshot_id}")
    def snapshot(snapshot_id: UUID):
        with app.state.engine.connect() as connection:
            return snapshot_metadata(connection, snapshot_id)

    @app.get("/snapshots/{snapshot_id}/tree")
    def tree(
        snapshot_id: UUID,
        path: str = Query("", max_length=4096),
        limit: int = Query(100, ge=1, le=500),
        offset: int = Query(0, ge=0, le=1000000),
    ):
        with app.state.engine.connect() as connection:
            published = require_ready(connection, snapshot_id)
            return RepositoryTools(
                connection, snapshot_id, published["parsing_run_id"]
            ).get_repository_tree(path, limit, offset)

    @app.get("/snapshots/{snapshot_id}/files")
    def file(
        snapshot_id: UUID,
        path: str = Query(..., min_length=1, max_length=4096),
        start_line: int = Query(1, ge=1),
        end_line: int | None = Query(None, ge=1),
    ):
        with app.state.engine.connect() as connection:
            published = require_ready(connection, snapshot_id)
            exists = connection.execute(
                select(repository_files.c.id).where(
                    repository_files.c.snapshot_id == snapshot_id, repository_files.c.path == path
                )
            ).scalar_one_or_none()
            if exists is None:
                raise HTTPException(404, "File not stored in this snapshot.")
            return RepositoryTools(connection, snapshot_id, published["parsing_run_id"]).read_file(
                path, start_line, end_line
            )

    @app.post("/snapshots/{snapshot_id}/ask")
    def ask(snapshot_id: UUID, body: QuestionRequest):
        with app.state.engine.connect() as connection:
            require_ready(
                connection, snapshot_id
            )  # Reject unready snapshots before any model work.
        try:
            provider = embedding_factory(app.state.settings)
            model = text_factory(app.state.settings)
        except ValueError:
            raise HTTPException(
                503, "Configure the answer and embedding providers before asking questions."
            ) from None
        with app.state.engine.connect() as connection:
            published = require_ready(connection, snapshot_id, profile(provider)["id"])
        result = asyncio.run(
            answer(
                app.state.engine,
                snapshot_id,
                body.question,
                app.state.settings,
                model,
                provider=provider,
                run_id=published["parsing_run_id"],
                pipeline=body.pipeline,
                planner=planner_factory(app.state.settings)
                if planner_factory and body.pipeline == "agent"
                else None,
            )
        )
        if result["status"] != "completed":
            raise HTTPException(
                502, "Answer generation failed within its validation or usage limits."
            )
        return result

    install_conversations(app, require_ready, QuestionRequest)
    return app


app = create_app()

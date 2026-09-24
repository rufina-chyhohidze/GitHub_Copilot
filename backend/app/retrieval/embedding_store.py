"""Batch embeddings into a reusable cache, publishing an index only after completion."""

import asyncio
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.db.schema import (
    chunk_embeddings,
    code_chunks,
    embedding_indexes,
    embedding_profiles,
    embeddings,
    repository_files,
)
from app.models.contracts import UsageLimits
from app.tools.repository import RepositoryTools

INPUT_VERSION = "code-context-v1"


def profile(provider) -> dict:
    values = {
        "provider": provider.provider,
        "model_id": provider.model_id,
        "model_version": provider.version,
        "dimensions": provider.dimensions,
        "input_version": INPUT_VERSION,
    }
    key = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()
    return {"id": key, **values}


def input_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def document_input(chunk: dict) -> str:
    return (
        f"Path: {chunk['path']}\nLanguage: {chunk['language']}\n"
        f"Symbol: {chunk['symbol_name'] or '<module>'}\n\n{chunk['content']}"
    )


async def cached_vectors(engine, provider, texts: list[str], settings) -> tuple[list, int, int]:
    specification = profile(provider)
    key = specification["id"]
    unique = {input_hash(value): value for value in texts}
    with engine.begin() as connection:
        connection.execute(
            insert(embedding_profiles).values(**specification).on_conflict_do_nothing()
        )
        cached = dict(
            connection.execute(
                select(embeddings.c.input_hash, embeddings.c.vector).where(
                    embeddings.c.profile_id == key, embeddings.c.input_hash.in_(unique)
                )
            ).all()
        )
    pending = [(digest, value) for digest, value in unique.items() if digest not in cached]
    if sum(provider.count_tokens(value) for _, value in pending) > settings.embedding_token_budget:
        raise ValueError("Embedding request exceeds COPILOT_EMBEDDING_TOKEN_BUDGET")
    usage = 0
    if pending:
        limits = UsageLimits(
            max_context_tokens=16000, timeout_seconds=min(60, settings.embedding_timeout_seconds)
        )
        batch = await provider.embed([value for _, value in pending], limits=limits)
        if (
            batch.model_id != provider.model_id
            or batch.dimensions != provider.dimensions
            or len(batch.vectors) != len(pending)
        ):
            raise ValueError("Embedding provider returned mismatched metadata or result count")
        if any(not any(value != 0 for value in vector) for vector in batch.vectors):
            raise ValueError("Embedding provider returned a zero vector")
        usage = batch.input_tokens
        with engine.begin() as connection:
            for (digest, _), vector in zip(pending, batch.vectors, strict=True):
                connection.execute(
                    insert(embeddings)
                    .values(profile_id=key, input_hash=digest, vector=list(vector))
                    .on_conflict_do_nothing()
                )
                cached[digest] = list(vector)
    return [cached[input_hash(value)] for value in texts], usage, len(pending)


async def build_index(engine, snapshot_id, settings, provider, run_id=None) -> dict:
    try:
        async with asyncio.timeout(settings.embedding_timeout_seconds):
            return await _build_index(engine, snapshot_id, settings, provider, run_id)
    except TimeoutError:
        raise ValueError(
            "Embedding indexing exceeded its time limit; completed batches remain cached"
        ) from None


async def _build_index(engine, snapshot_id, settings, provider, run_id):
    specification = profile(provider)
    key = specification["id"]
    with engine.begin() as connection:
        tools = RepositoryTools(connection, snapshot_id, run_id)
        run_id = tools.run["id"]
        complete = connection.execute(
            select(embedding_indexes.c.chunk_count).where(
                embedding_indexes.c.parsing_run_id == run_id, embedding_indexes.c.profile_id == key
            )
        ).scalar_one_or_none()
        if complete is not None:
            return {
                "parsing_run_id": str(run_id),
                "profile_id": key,
                "chunks": complete,
                "reused": True,
                "input_tokens": 0,
            }
        chunks = [
            dict(row)
            for row in connection.execute(
                select(code_chunks, repository_files.c.path, repository_files.c.language)
                .join(repository_files, repository_files.c.id == code_chunks.c.file_id)
                .where(
                    code_chunks.c.parsing_run_id == run_id,
                    repository_files.c.snapshot_id == snapshot_id,
                )
                .order_by(repository_files.c.path, code_chunks.c.ordinal)
            ).mappings()
            if row["content"].strip()
        ]
        cached_hashes = set(
            connection.execute(
                select(embeddings.c.input_hash).where(embeddings.c.profile_id == key)
            ).scalars()
        )
    documents = [document_input(chunk) for chunk in chunks]
    counts = [provider.count_tokens(value) for value in documents]
    if any(count > 8191 for count in counts):
        raise ValueError(
            "A full embedding payload exceeds 8191 tokens; reparse with smaller chunks"
        )
    missing = {
        input_hash(value): count
        for value, count in zip(documents, counts, strict=True)
        if input_hash(value) not in cached_hashes
    }
    if sum(missing.values()) > settings.embedding_token_budget:
        raise ValueError("Index exceeds COPILOT_EMBEDDING_TOKEN_BUDGET before making API calls")
    usage = generated = 0
    batches = []
    batch = []
    token_count = 0
    for index, count in enumerate(counts):
        if batch and (len(batch) >= settings.embedding_batch_size or token_count + count > 16000):
            batches.append(batch)
            batch, token_count = [], 0
        batch.append(index)
        token_count += count
    if batch:
        batches.append(batch)
    # Register the profile even when there are no non-whitespace chunks.
    with engine.begin() as connection:
        connection.execute(
            insert(embedding_profiles).values(**specification).on_conflict_do_nothing()
        )
    for batch in batches:
        _, tokens, new = await cached_vectors(
            engine, provider, [documents[i] for i in batch], settings
        )
        usage += tokens
        generated += new
        with engine.begin() as connection:
            for i in batch:
                connection.execute(
                    insert(chunk_embeddings)
                    .values(
                        chunk_id=chunks[i]["id"],
                        profile_id=key,
                        input_hash=input_hash(documents[i]),
                    )
                    .on_conflict_do_nothing()
                )
    with engine.begin() as connection:
        connection.execute(
            insert(embedding_indexes)
            .values(parsing_run_id=run_id, profile_id=key, chunk_count=len(chunks))
            .on_conflict_do_nothing()
        )
    return {
        "parsing_run_id": str(run_id),
        "profile_id": key,
        "chunks": len(chunks),
        "reused": False,
        "generated_inputs": generated,
        "input_tokens": usage,
    }

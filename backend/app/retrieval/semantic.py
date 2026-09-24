"""Exact pgvector cosine search, filtered by snapshot, parsing run, and embedding profile."""

from sqlalchemy import and_, select

from app.db.schema import (
    chunk_embeddings,
    code_chunks,
    embedding_indexes,
    embeddings,
    repository_files,
)


def semantic_search(tools, vector, profile_id: str, limit: int = 20) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("Candidate limit must be between 1 and 100")
    complete = tools.connection.execute(
        select(embedding_indexes.c.chunk_count).where(
            embedding_indexes.c.parsing_run_id == tools.run["id"],
            embedding_indexes.c.profile_id == profile_id,
        )
    ).scalar_one_or_none()
    if complete is None:
        raise ValueError("No complete embedding index for this run/profile; run embed first")
    distance = embeddings.c.vector.cosine_distance(vector)
    statement = (
        select(
            code_chunks,
            repository_files.c.path,
            repository_files.c.language,
            (1 - distance).label("score"),
        )
        .select_from(code_chunks)
        .join(repository_files, repository_files.c.id == code_chunks.c.file_id)
        .join(chunk_embeddings, chunk_embeddings.c.chunk_id == code_chunks.c.id)
        .join(
            embeddings,
            and_(
                embeddings.c.profile_id == chunk_embeddings.c.profile_id,
                embeddings.c.input_hash == chunk_embeddings.c.input_hash,
            ),
        )
        .where(
            repository_files.c.snapshot_id == tools.snapshot_id,
            code_chunks.c.parsing_run_id == tools.run["id"],
            embeddings.c.profile_id == profile_id,
        )
        .order_by(distance, repository_files.c.path, code_chunks.c.ordinal)
        .limit(limit)
    )
    return [dict(row) for row in tools.connection.execute(statement).mappings()]

"""Delete a local repository atomically, retaining shared embedding-cache entries."""

from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db.schema import (
    answer_runs,
    chunk_embeddings,
    code_chunks,
    conversations,
    embedding_indexes,
    index_jobs,
    messages,
    parsing_runs,
    ready_indexes,
    repositories,
    repository_files,
    run_events,
    run_evidence,
    snapshots,
)


def delete_repository(connection, repository_id):
    # Parent locks prevent new indexing jobs and conversations during deletion.
    found = connection.execute(
        select(repositories.c.id).where(repositories.c.id == repository_id).with_for_update()
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(404, "Repository not found.")
    if connection.execute(
        select(index_jobs.c.id)
        .where(
            index_jobs.c.repository_id == repository_id,
            index_jobs.c.status.in_(["queued", "running"]),
        )
        .limit(1)
    ).first():
        raise HTTPException(
            409, "Indexing is still active. Wait for it to finish, then delete this repository."
        )
    snapshot_ids = select(snapshots.c.id).where(snapshots.c.repository_id == repository_id)
    connection.execute(snapshot_ids.with_for_update()).all()
    connection.execute(
        select(ready_indexes).where(ready_indexes.c.snapshot_id.in_(snapshot_ids)).with_for_update()
    ).all()
    conversation_ids = select(conversations.c.id).where(
        conversations.c.snapshot_id.in_(snapshot_ids)
    )
    connection.execute(conversation_ids.with_for_update()).all()
    if connection.execute(
        select(answer_runs.c.id)
        .where(
            answer_runs.c.conversation_id.in_(conversation_ids),
            answer_runs.c.status.in_(["queued", "running"]),
        )
        .limit(1)
    ).first():
        raise HTTPException(
            409,
            "An answer is still active. Cancel it or wait for it to finish, "
            "then delete this repository.",
        )
    run_ids = select(answer_runs.c.id).where(answer_runs.c.conversation_id.in_(conversation_ids))
    parsing_ids = select(parsing_runs.c.id).where(parsing_runs.c.snapshot_id.in_(snapshot_ids))
    chunk_ids = select(code_chunks.c.id).where(code_chunks.c.parsing_run_id.in_(parsing_ids))
    for table, condition in [
        (run_events, run_events.c.run_id.in_(run_ids)),
        (run_evidence, run_evidence.c.run_id.in_(run_ids)),
        (answer_runs, answer_runs.c.conversation_id.in_(conversation_ids)),
        (messages, messages.c.conversation_id.in_(conversation_ids)),
        (conversations, conversations.c.snapshot_id.in_(snapshot_ids)),
        (index_jobs, index_jobs.c.repository_id == repository_id),
        (ready_indexes, ready_indexes.c.snapshot_id.in_(snapshot_ids)),
        (chunk_embeddings, chunk_embeddings.c.chunk_id.in_(chunk_ids)),
        (embedding_indexes, embedding_indexes.c.parsing_run_id.in_(parsing_ids)),
        (code_chunks, code_chunks.c.parsing_run_id.in_(parsing_ids)),
        (parsing_runs, parsing_runs.c.snapshot_id.in_(snapshot_ids)),
        (repository_files, repository_files.c.snapshot_id.in_(snapshot_ids)),
        (snapshots, snapshots.c.repository_id == repository_id),
        (repositories, repositories.c.id == repository_id),
    ]:
        connection.execute(delete(table).where(condition))

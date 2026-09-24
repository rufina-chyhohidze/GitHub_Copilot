"""Retrieve evidence candidates; answer generation deliberately comes in Step 6."""

import asyncio
import time

from app.retrieval.context import assemble_context, fuse_rankings
from app.retrieval.embedding_store import cached_vectors, profile
from app.retrieval.lexical import lexical_search
from app.retrieval.semantic import semantic_search
from app.tools.repository import RepositoryTools, literal_query


async def retrieve(
    engine, snapshot_id, query, settings, *, mode="hybrid", provider=None, run_id=None, limit=10
):
    literal_query(query)
    if mode not in {"lexical", "semantic", "hybrid"} or not 1 <= limit <= 20:
        raise ValueError("Choose lexical, semantic, or hybrid mode and a limit from 1 to 20")
    started = time.monotonic()
    with engine.connect() as connection:
        tools = RepositoryTools(connection, snapshot_id, run_id)
        run_id = tools.run["id"]
        lexical = lexical_search(tools, query, min(100, limit * 3)) if mode != "semantic" else []
        if mode != "lexical":
            if provider is None:
                raise ValueError("Semantic search requires an embedding provider")
            # Check index readiness before making a potentially paid query embedding request.
            semantic_search(tools, [1.0] * provider.dimensions, profile(provider)["id"], 1)
    semantic = []
    tokens = 0
    if mode != "lexical":
        try:
            async with asyncio.timeout(settings.embedding_timeout_seconds):
                vectors, tokens, _ = await cached_vectors(engine, provider, [query], settings)
        except TimeoutError:
            raise ValueError("Query embedding exceeded its time limit") from None
        with engine.connect() as connection:
            tools = RepositoryTools(connection, snapshot_id, run_id)
            semantic = semantic_search(
                tools, vectors[0], profile(provider)["id"], min(100, limit * 3)
            )
    hits = fuse_rankings(lexical, semantic, limit=limit)
    with engine.connect() as connection:
        context = assemble_context(RepositoryTools(connection, snapshot_id, run_id), hits)
    return {
        "snapshot_id": str(snapshot_id),
        "parsing_run_id": str(run_id),
        "mode": mode,
        "profile_id": profile(provider)["id"] if provider else None,
        "query": query,
        "context": context,
        "retrieved_files": list(dict.fromkeys(hit["path"] for hit in hits)),
        "candidate_counts": {"lexical": len(lexical), "semantic": len(semantic)},
        "input_tokens": tokens,
        "latency_ms": round((time.monotonic() - started) * 1000, 2),
        "answer_generated": False,
    }

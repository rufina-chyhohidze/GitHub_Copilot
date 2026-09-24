"""Rank exact identifiers and literal query terms without treating input as a regex."""

import re

from sqlalchemy import case, func, or_, select

from app.db.schema import code_chunks, repository_files
from app.tools.repository import RepositoryTools, literal_query

STOP_WORDS = {
    "a",
    "an",
    "the",
    "is",
    "are",
    "where",
    "what",
    "how",
    "does",
    "do",
    "of",
    "to",
    "in",
    "and",
    "for",
}


def query_terms(query: str) -> list[str]:
    literal_query(query)
    return list(
        dict.fromkeys(
            word.lower() for word in re.findall(r"[\w]+", query) if word.lower() not in STOP_WORDS
        )
    )[:16]


def lexical_search(tools: RepositoryTools, query: str, limit: int = 20) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("Candidate limit must be between 1 and 100")
    literal_query(query)
    terms = query_terms(query) or [query.lower()]
    content = func.lower(code_chunks.c.content)
    symbol = func.lower(func.coalesce(code_chunks.c.symbol_name, ""))
    path = func.lower(repository_files.c.path)
    matches = [or_(func.strpos(content, term) > 0, func.strpos(path, term) > 0) for term in terms]
    score = sum(case((match, 1), else_=0) for match in matches)
    score += case((symbol == query.lower(), 10), else_=0)
    score += case((func.strpos(content, query.lower()) > 0, 3), else_=0)
    statement = (
        select(
            code_chunks, repository_files.c.path, repository_files.c.language, score.label("score")
        )
        .join(repository_files, repository_files.c.id == code_chunks.c.file_id)
        .where(
            code_chunks.c.parsing_run_id == tools.run["id"],
            repository_files.c.snapshot_id == tools.snapshot_id,
            or_(*matches),
        )
        .order_by(score.desc(), repository_files.c.path, code_chunks.c.ordinal)
        .limit(limit)
    )
    return [dict(row) for row in tools.connection.execute(statement).mappings()]

from uuid import UUID, uuid4

from sqlalchemy import select
from test_ingestion import MemorySource
from test_javascript import FIXTURE
from test_snapshot_storage import pytestmark, save  # noqa: F401

from app.answering.evidence import Draft, EvidenceRegistry, citation
from app.config import Settings
from app.db.schema import code_chunks, parsing_runs
from app.ingestion.parsing import parse_and_store
from app.ingestion.scanner import scan
from app.tools.repository import RepositoryTools


def test_web_syntax_persists_searches_and_cites_immutable_source(connection):
    source = MemorySource([(p.name, "100644", p.read_bytes()) for p in sorted(FIXTURE.iterdir())])
    snapshot = save(connection, scan_result=scan(source))
    snapshot_id = UUID(snapshot["snapshot_id"])
    run = parse_and_store(connection, snapshot_id, Settings(_env_file=None))
    assert run["files_by_status"] == {"parsed": 7, "parse_error": 1, "text_fallback": 1}
    assert run["files_with_limitations"] >= 1
    assert run["exports"] > 0
    assert parse_and_store(connection, snapshot_id, Settings(_env_file=None))["reused"]
    tools = RepositoryTools(connection, snapshot_id, UUID(run["parsing_run_id"]))
    assert {s["path"] for s in tools.find_symbol("create")["symbols"]} == {"admin.ts", "routes.js"}
    details = tools.get_file_symbols("OrderCard.tsx")
    assert details["status"] == "parsed"
    assert details["imports"][0]["type_only"]
    assert details["imports"][0]["resolution"] == "unresolved"
    assert details["exports"][-1]["alias"] == "default"
    assert tools.get_file_symbols("legacy.cjs")["limitations"]
    assert tools.get_file_symbols("broken.ts")["status"] == "parse_error"
    assert tools.search_code("positive total")["hits"][0]["path"] == "store.ts"
    symbol = next(
        s for s in tools.get_file_symbols("store.ts")["symbols"] if s["name"] == "Store.save"
    )
    registry = EvidenceRegistry(tools, uuid4())
    entry = registry.add("store.ts", symbol["start_line"], symbol["end_line"])
    registry.validate(
        Draft(
            claims=[{"text": "save checks the order total.", "evidence_ids": [entry["id"]]}],
            uncertainty=None,
        )
    )
    link = citation(
        registry.resolve(entry["id"]), "https://github.com/example/repository", "a" * 40
    )
    assert link["url"].endswith("/store.ts#L12-L16")
    old_run = tools.run
    old_chunks = (
        connection.execute(
            select(code_chunks.c.content)
            .where(code_chunks.c.parsing_run_id == old_run["id"])
            .order_by(code_chunks.c.file_id, code_chunks.c.ordinal)
        )
        .scalars()
        .all()
    )
    updated = parse_and_store(
        connection, snapshot_id, Settings(_env_file=None, max_chunk_tokens=32)
    )
    assert updated["parsing_run_id"] != run["parsing_run_id"]
    assert (
        connection.execute(
            select(parsing_runs.c.files).where(parsing_runs.c.id == old_run["id"])
        ).scalar_one()
        == old_run["files"]
    )
    assert (
        connection.execute(
            select(code_chunks.c.content)
            .where(code_chunks.c.parsing_run_id == old_run["id"])
            .order_by(code_chunks.c.file_id, code_chunks.c.ordinal)
        )
        .scalars()
        .all()
        == old_chunks
    )

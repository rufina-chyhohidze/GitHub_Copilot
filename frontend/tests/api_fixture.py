"""Browser-test API: real PostgreSQL/services, isolated schema, deterministic providers.

No GitHub requests, provider calls, or changes to the development database tables.
"""

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

import uvicorn
from app.config import Settings
from app.conversations import worker as answers
from app.db.schema import metadata
from app.db.session import make_engine
from app.ingestion.scanner import scan
from app.ingestion.service import save_snapshot
from app.jobs import worker as indexing
from app.main import create_app
from app.providers.interfaces import Generation
from test_ingestion import MemorySource
from test_retrieval_storage import TestEmbeddings


class Model:
    async def generate(self, prompt, *, limits):
        await asyncio.sleep(0.7)
        scope = json.loads(prompt)
        evidence = scope["evidence"][0]
        return Generation(
            model_id="browser-test",
            input_tokens=100,
            output_tokens=30,
            text=json.dumps(
                {
                    "claims": [
                        {
                            "text": "Authentication returns the token.",
                            "evidence_ids": [evidence["id"]],
                        }
                    ],
                    "uncertainty": "This fixture does not establish how tokens are issued.",
                }
            ),
        )


class Planner:
    async def generate(self, prompt, *, limits):
        return Generation(
            model_id="browser-test",
            input_tokens=50,
            output_tokens=20,
            text=json.dumps(
                {
                    "action": "finish",
                    "path": None,
                    "query": None,
                    "start_line": None,
                    "end_line": None,
                    "limit": None,
                }
                if any(
                    item.get("tool") == "read_file"
                    for item in json.loads(prompt)["observations"]
                )
                else {
                    "action": "read_file",
                    "query": None,
                    "limit": None,
                    "path": "auth.py",
                    "start_line": 1,
                    "end_line": 2,
                }
            ),
        )


def main():
    # Only read the database address. Real model settings/keys never reach fixture providers.
    url = (
        os.environ.get("COPILOT_TEST_DATABASE_URL") or Settings().require_database_url()
    )
    settings = Settings(
        _env_file=None,
        database_url=url,
        max_chunk_tokens=256,
        worker_poll_seconds=0.1,
        job_lease_seconds=6,
    )
    base = make_engine(settings)
    schema = "browser_test_" + uuid4().hex
    with base.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = base.execution_options(schema_translate_map={None: schema})
    stopped = threading.Event()
    revisions = {}

    def ingest(url, ref, task_settings, engine, *, on_resolved):
        time.sleep(0.3)
        count = revisions.get(url, 0)
        revisions[url] = count + 1
        sha = ("a" if count == 0 else "b") * 40
        on_resolved(sha)
        source = MemorySource(
            [
                ("auth.py", "100644", b"def authenticate(token):\n    return token\n"),
                (
                    "README.md",
                    "100644",
                    b"# Example repository\nAuthentication lives in auth.py.\n",
                ),
                (
                    "src/routes.py",
                    "100644",
                    b"from auth import authenticate\n\ndef route(token):\n    return authenticate(token)\n",
                ),
                ("node_modules/ignored.js", "100644", b"generated dependency"),
            ]
        )
        with engine.begin() as connection:
            return save_snapshot(
                connection,
                url,
                sha,
                "browser-test-v1",
                scan(source),
                time.monotonic() + 30,
            )

    indexing.ingest = ingest

    def work(function, **kwargs):
        while not stopped.is_set():
            function(engine, settings, **kwargs)
            stopped.wait(0.1)

    threads = []
    try:
        metadata.create_all(engine)
        for function, kwargs in [
            (indexing.run_once, {"embedding_factory": lambda _: TestEmbeddings()}),
            (
                answers.run_once,
                {
                    "embedding_factory": lambda _: TestEmbeddings(),
                    "text_factory": lambda _: Model(),
                    "planner_factory": lambda _: Planner(),
                },
            ),
        ]:
            thread = threading.Thread(
                target=work, args=(function,), kwargs=kwargs, daemon=True
            )
            thread.start()
            threads.append(thread)
        app = create_app(
            settings,
            engine,
            embedding_factory=lambda _: TestEmbeddings(),
            text_factory=lambda _: Model(),
        )
        uvicorn.run(app, host="127.0.0.1", port=18000, log_level="warning")
    finally:
        stopped.set()
        for thread in threads:
            thread.join(timeout=15)
        with base.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        base.dispose()


if __name__ == "__main__":
    main()

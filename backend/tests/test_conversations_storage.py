"""Real transactions cover replay, fencing, and atomic final publication."""

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_agents_storage import Planner
from test_answering_storage import Model
from test_jobs_storage import fake_ingestion as _fake_ingestion  # noqa: F401
from test_jobs_storage import job_engine as _job_engine  # noqa: F401
from test_jobs_storage import job_settings as _job_settings  # noqa: F401
from test_retrieval_storage import TestEmbeddings

from app.conversations import service, worker
from app.db.schema import answer_runs, conversations, messages, run_events, run_evidence
from app.jobs import worker as index_worker
from app.main import create_app


@pytest.fixture
def workspace(job_engine, job_settings, fake_ingestion):
    provider = TestEmbeddings()
    with TestClient(
        create_app(job_settings, job_engine, embedding_factory=lambda _: provider)
    ) as client:
        job = client.post(
            "/repositories", json={"url": "https://github.com/example/repository"}
        ).json()
        index_worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
        sid = client.get(job["job_url"]).json()["snapshot_id"]
        conversation = client.post(f"/snapshots/{sid}/conversations", json={}).json()
        yield client, sid, conversation["id"], provider


def send(client, cid, key="request-1", **body):
    return client.post(
        f"/conversations/{cid}/messages",
        json={"question": "authenticate", **body},
        headers={"Idempotency-Key": key},
    )


def events(client, rid, **kwargs):
    response = client.get(f"/runs/{rid}/events", **kwargs)
    assert response.status_code == 200, response.text
    rows = []
    for block in response.text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        rows.append(
            {
                "sequence": int(fields["id"]),
                "type": fields["event"],
                "payload": json.loads(fields["data"]),
            }
        )
    return rows


@pytest.mark.parametrize("pipeline", ["fixed", "agent"])
def test_persisted_answer_and_replay(workspace, job_engine, job_settings, pipeline):
    client, sid, cid, provider = workspace
    response = send(client, cid, pipeline=pipeline)
    assert response.status_code == 202
    rid = response.json()["run_id"]
    assert send(client, cid, pipeline=pipeline).json()["run_id"] == rid
    assert send(client, cid, question="different", pipeline=pipeline).status_code == 409
    assert send(client, cid, key="second", pipeline=pipeline).status_code == 409
    worker.run_once(
        job_engine,
        job_settings,
        embedding_factory=lambda _: provider,
        text_factory=lambda _: Model(),
        planner_factory=lambda _: Planner(
            [{"action": "read_file", "path": "auth.py", "start_line": 1, "end_line": 2}]
        ),
    )
    rows = events(client, rid)
    assert [row["sequence"] for row in rows] == list(range(1, len(rows) + 1))
    assert {"status", "tool_started", "tool_finished", "answer_delta", "citation", "done"} <= {
        row["type"] for row in rows
    }
    assert rows[-1]["payload"]["status"] == "completed"
    assert rows[-1]["payload"]["validated"] is True
    assert rows[-1]["payload"]["snapshot_id"] == sid
    assert all(row["payload"]["provisional"] for row in rows if row["type"] == "answer_delta")
    assert events(client, rid, headers={"Last-Event-ID": "2"}) == rows[2:]
    assert events(client, rid, params={"after": 2}) == rows[2:]
    assert (
        client.get(f"/runs/{rid}/events", headers={"Last-Event-ID": str(len(rows))}).status_code
        == 204
    )
    assert client.get(f"/runs/{rid}/events?after=999999").status_code == 400
    saved = client.get(f"/conversations/{cid}").json()
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    assert saved["messages"][1]["content"]["citations"][0]["run_id"] == rid
    assert saved["snapshot_id"] == sid
    assert client.get(f"/conversations/{cid}?limit=1").json()["next_offset"] == 1
    assert client.get(f"/snapshots/{sid}/conversations").json()["items"][0]["id"] == cid
    with job_engine.connect() as connection:
        evidence = connection.execute(select(run_evidence)).mappings().all()
        assert evidence and str(evidence[0]["run_id"]) == rid
        history = worker.context(connection, {"conversation_id": UUID(cid)})
        assert [m["role"] for m in history] == ["user", "assistant"]
        assert worker.context(connection, {"conversation_id": UUID(cid)}, budget=1) == []
    assert send(client, cid, pipeline=pipeline).json()["run_id"] == rid
    assert worker.run_once(job_engine, job_settings) is None
    assert client.post(f"/runs/{rid}/cancel").json()["status"] == "completed"


def test_cancel_queued_and_running_fences_stale_writer(workspace, job_engine, job_settings):
    client, _, cid, _ = workspace
    rid = send(client, cid).json()["run_id"]
    assert client.post(f"/runs/{rid}/cancel").json()["status"] == "cancelled"
    assert worker.run_once(job_engine, job_settings) is None
    assert events(client, rid)[-1]["payload"] == {"status": "cancelled", "validated": False}
    second = send(client, cid, key="second").json()["run_id"]
    claimed = service.claim(job_engine, job_settings)
    assert str(claimed["id"]) == second
    assert client.post(f"/runs/{second}/cancel").status_code == 200
    with pytest.raises(service.RunStopped):
        service.emit(job_engine, claimed, "status", {"status": "late"})
    with pytest.raises(service.RunStopped):
        service.finish(job_engine, claimed, {"status": "failed"})
    assert len(client.get(f"/conversations/{cid}").json()["messages"]) == 2


def test_expired_worker_is_terminal_and_never_retried(workspace, job_engine, job_settings):
    client, _, cid, _ = workspace
    rid = send(client, cid).json()["run_id"]
    claimed = service.claim(job_engine, job_settings)
    with job_engine.begin() as connection:
        connection.execute(
            answer_runs.update()
            .where(answer_runs.c.id == UUID(rid))
            .values(expires_at=func.now() - timedelta(seconds=1))
        )
    assert service.claim(job_engine, job_settings) is None
    rows = events(client, rid)
    assert rows[-2]["payload"]["code"] == "worker_interrupted"
    assert rows[-1]["payload"]["status"] == "interrupted"
    with pytest.raises(service.RunStopped):
        service.emit(job_engine, claimed, "answer_delta", {"text": "stale"})
    assert send(client, cid).json()["run_id"] == rid
    assert send(client, cid, key="new").json()["run_id"] != rid


def test_invalid_citations_never_publish_message(workspace, job_engine, job_settings):
    client, _, cid, provider = workspace
    rid = send(client, cid).json()["run_id"]
    worker.run_once(
        job_engine,
        job_settings,
        embedding_factory=lambda _: provider,
        text_factory=lambda _: Model(failures=99),
    )
    rows = events(client, rid)
    assert rows[-1]["payload"]["status"] == "failed"
    assert not any(r["type"] in {"answer_delta", "citation"} for r in rows)
    assert len(client.get(f"/conversations/{cid}").json()["messages"]) == 1
    with job_engine.connect() as connection:
        assert connection.execute(select(run_evidence)).first() is None


def test_parallel_idempotency_and_claim(workspace, job_engine, job_settings):
    _, _, cid, _ = workspace

    def submit():
        with job_engine.begin() as connection:
            return service.submit(connection, UUID(cid), "authenticate", "fixed", "same")["id"]

    with ThreadPoolExecutor(max_workers=4) as pool:
        identities = list(pool.map(lambda _: submit(), range(4)))
    assert len(set(identities)) == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: service.claim(job_engine, job_settings), range(2)))
    assert sum(run is not None for run in claims) == 1
    with job_engine.connect() as connection:
        assert len(connection.execute(select(messages)).all()) == 1
        assert len(connection.execute(select(run_events)).all()) == 2


def test_cancel_interrupts_awaiting_provider(workspace, job_engine, job_settings):
    client, _, cid, provider = workspace
    started, cancelled = threading.Event(), threading.Event()

    class SlowModel:
        async def generate(self, *args, **kwargs):
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.set()
                raise

    rid = send(client, cid).json()["run_id"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            worker.run_once,
            job_engine,
            job_settings,
            embedding_factory=lambda _: provider,
            text_factory=lambda _: SlowModel(),
        )
        assert started.wait(5)
        client.post(f"/runs/{rid}/cancel")
        future.result(timeout=5)
    assert cancelled.is_set()
    assert events(client, rid)[-1]["payload"]["status"] == "cancelled"


def test_new_index_cannot_move_conversation(workspace, job_engine, job_settings, fake_ingestion):
    client, sid, cid, provider = workspace
    with job_engine.connect() as connection:
        original = service.get(connection, conversations, UUID(cid))
    fake_ingestion["sha"] = "b" * 40
    job = client.post(
        "/repositories", json={"url": "https://github.com/example/repository", "ref": "next"}
    ).json()
    index_worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
    assert client.get(job["job_url"]).json()["snapshot_id"] != sid
    rid = send(client, cid).json()["run_id"]
    worker.run_once(
        job_engine,
        job_settings,
        embedding_factory=lambda _: provider,
        text_factory=lambda _: Model(),
    )
    final = events(client, rid)[-1]["payload"]
    assert final["snapshot_id"] == sid and final["commit_sha"] == "a" * 40
    with job_engine.connect() as connection:
        assert service.get(connection, conversations, UUID(cid)) == original


def test_live_stream_disconnect_and_followup(workspace, job_engine, job_settings):
    client, _, cid, provider = workspace
    rid = send(client, cid).json()["run_id"]
    endpoint = next(
        route.endpoint
        for route in client.app.routes
        if getattr(route, "path", None) == "/runs/{run_id}/events"
    )

    async def disconnect():
        response = endpoint(UUID(rid), after=0, last_event_id=None)
        initial = await anext(response.body_iterator)
        assert "id: 1\n" in initial and '"queued"' in initial
        await response.body_iterator.aclose()

    asyncio.run(disconnect())
    assert client.get(f"/runs/{rid}").json()["status"] == "queued"
    worker.run_once(
        job_engine,
        job_settings,
        embedding_factory=lambda _: provider,
        text_factory=lambda _: Model(),
    )
    assert events(client, rid, headers={"Last-Event-ID": "1"})[-1]["type"] == "done"
    prompts = []

    class FollowupModel(Model):
        async def generate(self, prompt, **kwargs):
            prompts.append(json.loads(prompt))
            return await super().generate(prompt, **kwargs)

    next_id = send(client, cid, key="followup", question="authenticate again").json()["run_id"]
    worker.run_once(
        job_engine,
        job_settings,
        embedding_factory=lambda _: provider,
        text_factory=lambda _: FollowupModel(),
    )
    assert events(client, next_id)[-1]["payload"]["status"] == "completed"
    assert prompts[0]["conversation_history_untrusted"][0]["content"]["text"] == "authenticate"
    assert len(client.get(f"/conversations/{cid}").json()["messages"]) == 4


def test_provider_failure_is_safe_and_terminal(workspace, job_engine, job_settings):
    client, _, cid, provider = workspace
    rid = send(client, cid).json()["run_id"]

    class BrokenModel:
        async def generate(self, *args, **kwargs):
            raise RuntimeError("secret-provider-credential")

    worker.run_once(
        job_engine,
        job_settings,
        embedding_factory=lambda _: provider,
        text_factory=lambda _: BrokenModel(),
    )
    rows = events(client, rid)
    assert rows[-2]["type"] == "error" and rows[-1]["payload"]["status"] == "failed"
    assert "secret" not in json.dumps(rows)
    assert send(client, cid).json()["run_id"] == rid

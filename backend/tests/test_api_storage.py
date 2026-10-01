from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from test_answering_storage import Model
from test_jobs_storage import (  # noqa: F401
    fake_ingestion as _fake_ingestion,
)
from test_jobs_storage import (
    get_job,
    submit,
)
from test_jobs_storage import job_engine as _job_engine  # noqa: F401
from test_jobs_storage import job_settings as _job_settings  # noqa: F401
from test_retrieval_storage import TestEmbeddings

from app.config import Settings
from app.ingestion.parsing import parse_snapshot
from app.jobs import worker
from app.main import create_app


def test_submit_worker_browse_and_ask_workflow(job_engine, job_settings, fake_ingestion):
    provider = TestEmbeddings()
    model = Model()
    app = create_app(
        job_settings, job_engine, embedding_factory=lambda _: provider, text_factory=lambda _: model
    )
    with TestClient(app) as client:
        response = client.post(
            "/repositories", json={"url": "https://github.com/example/repository", "ref": "main"}
        )
        assert response.status_code == 202
        submitted = response.json()
        assert fake_ingestion["calls"] == [] and provider.calls == 0
        duplicate = client.post(
            "/repositories", json={"url": "https://github.com/example/repository", "ref": "main"}
        )
        assert duplicate.json()["job_id"] == submitted["job_id"]
        queued = client.get(submitted["job_url"]).json()
        assert queued["status"] == "queued"
        assert "configuration" not in queued and "lease_token" not in queued
        for path in ["/index-jobs/", "/repositories/", "/snapshots/"]:
            assert client.get(path + str(uuid4())).status_code == 404
        worker.run_once(job_engine, job_settings, embedding_factory=lambda _: provider)
        completed = client.get(submitted["job_url"]).json()
        assert completed["status"] == "succeeded"
        sid = completed["snapshot_id"]
        metadata = client.get("/snapshots/" + sid).json()
        assert metadata["commit_sha"] == "a" * 40 and metadata["status"] == "ready"
        repository = client.get("/repositories/" + submitted["repository_id"]).json()
        assert repository["latest_ready_snapshot_id"] == sid
        listing = client.get(f"/repositories/{submitted['repository_id']}/snapshots?limit=1").json()
        assert listing["items"][0]["id"] == sid and listing["next_offset"] is None
        page = client.get(f"/snapshots/{sid}/tree?limit=1").json()
        assert len(page["entries"]) == 1 and page["next_offset"] == 1
        next_page = client.get(f"/snapshots/{sid}/tree?limit=1&offset=1").json()
        assert next_page["entries"][0]["path"] != page["entries"][0]["path"]
        assert next_page["next_offset"] is None
        source = client.get(f"/snapshots/{sid}/files?path=auth.py&start_line=1&end_line=2").json()
        assert "def authenticate" in source["content"]
        assert client.get(f"/snapshots/{sid}/files?path=missing.py").status_code == 404
        assert client.get(f"/snapshots/{sid}/files?path=auth.py&start_line=999").status_code == 400
        newer = parse_snapshot(
            UUID(sid), job_settings.model_copy(update={"max_chunk_tokens": 8}), job_engine
        )
        assert newer["parsing_run_id"] != completed["parsing_run_id"]
        answered = client.post(f"/snapshots/{sid}/ask", json={"question": "authenticate"})
        assert answered.status_code == 200, answered.text
        assert answered.json()["retrieval"]["parsing_run_id"] == completed["parsing_run_id"]
        assert answered.json()["citations"][0]["source"]["snapshot_id"] == sid
        assert (
            client.post(
                f"/repositories/{submitted['repository_id']}/index", json={"ref": "next"}
            ).status_code
            == 202
        )


def test_unready_snapshot_rejects_browse_and_qa_before_model_work(
    job_engine, job_settings, fake_ingestion, monkeypatch
):
    job = submit(job_engine, job_settings)

    def broken(*args):
        raise ValueError("stop after ingestion")

    monkeypatch.setattr(worker, "parse_snapshot", broken)
    worker.run_once(job_engine, job_settings, embedding_factory=lambda _: TestEmbeddings())
    sid = get_job(job_engine, job)["snapshot_id"]
    assert sid is not None

    def no_provider(_):
        raise AssertionError("Unready snapshots must be rejected before provider construction")

    with TestClient(
        create_app(
            job_settings, job_engine, embedding_factory=no_provider, text_factory=no_provider
        )
    ) as client:
        for suffix in ["/tree", "/files?path=auth.py"]:
            assert client.get(f"/snapshots/{sid}{suffix}").status_code == 409
        response = client.post(f"/snapshots/{sid}/ask", json={"question": "authenticate"})
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "snapshot_not_ready"


def test_missing_provider_configuration_is_actionable(job_engine):
    with TestClient(create_app(Settings(_env_file=None), job_engine)) as client:
        response = client.post(
            "/repositories", json={"url": "https://github.com/example/repository"}
        )
        assert response.status_code == 503
        assert "Configure" in response.json()["error"]["message"]

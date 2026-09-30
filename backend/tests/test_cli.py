import json
import sys

import pytest

from app import cli
from app.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    import os

    for key in os.environ:
        if key.startswith("COPILOT_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(cli, "Settings", lambda: Settings(_env_file=None))


def test_smoke_does_not_require_credentials(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repo-copilot", "smoke"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "ok"


def test_missing_database_is_actionable(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repo-copilot", "smoke", "--database"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "COPILOT_DATABASE_URL" in capsys.readouterr().err


def test_invalid_configuration_does_not_leak_secrets(monkeypatch, capsys):
    monkeypatch.setenv("COPILOT_DATABASE_URL", "not-a-url-secret-password")
    monkeypatch.setattr(sys, "argv", ["repo-copilot", "smoke"])
    with pytest.raises(SystemExit):
        cli.main()
    output = capsys.readouterr().err
    assert "database_url" in output
    assert "secret-password" not in output


def test_provider_check_lists_missing_settings(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repo-copilot", "smoke", "--provider"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "COPILOT_EMBEDDING_MODEL_ID" in capsys.readouterr().err


def test_ingest_rejects_unsafe_url_before_database_or_network(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["repo-copilot", "ingest", "file:///tmp/repo"])
    monkeypatch.setattr(cli, "make_engine", lambda *a: pytest.fail("Opened a database"))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "https://github.com/owner/repository" in capsys.readouterr().err


def test_ask_workflow_writes_trace(monkeypatch, capsys, tmp_path):
    from uuid import uuid4

    snapshot_id, parsing_id = str(uuid4()), str(uuid4())
    calls = []

    class Engine:
        def dispose(self):
            calls.append("dispose")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "repo-copilot",
            "ask",
            "https://github.com/a/b",
            "Where is auth?",
            "--ref",
            "a" * 40,
            "--mode",
            "lexical",
            "--trace",
            str(tmp_path / "trace.json"),
        ],
    )
    monkeypatch.setattr(cli, "OpenAITextModel", lambda _: object())
    monkeypatch.setattr(cli, "make_engine", lambda _: Engine())
    monkeypatch.setattr(cli, "check_database", lambda _: None)
    monkeypatch.setattr(cli, "ingest", lambda *args: {"snapshot_id": snapshot_id})
    monkeypatch.setattr(cli, "parse_snapshot", lambda *args: {"parsing_run_id": parsing_id})

    async def fake_answer(*args, **kwargs):
        assert str(args[1]) == snapshot_id
        assert str(kwargs["run_id"]) == parsing_id
        return {
            "status": "completed",
            "commit_sha": "a" * 40,
            "citations": [],
            "answer": {"claims": [], "uncertainty": "Insufficient evidence"},
        }

    monkeypatch.setattr(cli, "answer", fake_answer)
    assert cli.main() == 0
    assert calls == ["dispose"]
    assert "Insufficient evidence" in capsys.readouterr().out
    assert json.loads((tmp_path / "trace.json").read_text())["status"] == "completed"


def test_ask_rejects_invalid_url_before_provider(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["repo-copilot", "ask", "file:///tmp/repo", "auth?"])
    monkeypatch.setattr(cli, "OpenAITextModel", lambda _: pytest.fail("Provider constructed"))
    with pytest.raises(SystemExit):
        cli.main()

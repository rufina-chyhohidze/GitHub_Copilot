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

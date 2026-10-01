from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.config import Settings
from app.main import create_app


class NoDatabase:
    def connect(self):
        raise AssertionError("Invalid requests must not access the database")

    begin = connect


@pytest.mark.parametrize(
    "url,body",
    [
        ("/repositories", {"url": "https://evil.example/repo"}),
        ("/repositories", {"url": "https://github.com/user/repo", "ref": "--upload-pack=secret"}),
        ("/repositories", {"url": "https://github.com/user/repo", "extra": "secret"}),
        (f"/snapshots/{uuid4()}/ask", {"question": "🌍" * 200}),
        (f"/snapshots/{uuid4()}/ask", {"question": " "}),
    ],
)
def test_api_validation_precedes_services(url, body):
    with TestClient(create_app(Settings(_env_file=None), NoDatabase())) as client:
        response = client.post(url, json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
        assert "secret" not in response.text


def test_missing_routes_and_invalid_parameters_share_error_envelope():
    with TestClient(create_app(Settings(_env_file=None), NoDatabase())) as client:
        assert client.get("/missing").json()["error"]["code"] == "not_found"
        assert client.get(f"/repositories/{uuid4()}/snapshots?limit=0").status_code == 422
        assert client.get("/repositories/not-a-uuid").status_code == 422
        assert client.get("/openapi.json").status_code == 200


def test_database_errors_do_not_expose_credentials():
    class BrokenDatabase:
        def connect(self):
            raise OperationalError("secret-sql", {}, Exception("secret-password"))

    with TestClient(create_app(Settings(_env_file=None), BrokenDatabase())) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert "secret" not in response.text

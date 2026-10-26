"""The api. /healthz needs nothing; the rest need Postgres and skip without it."""

import uuid

import pytest
from fastapi.testclient import TestClient

from api.main import app, get_current_user


@pytest.fixture
def client():
    # "with" runs the app's start-up and shut-down code (the lifespan), like a real server.
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_healthz_needs_nothing(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_with_database(database_url, client):
    assert client.get("/readyz").status_code == 200


def test_create_then_get_run(database_url, client, job_ad, cv):
    created = client.post("/runs", json={"job_ad": job_ad, "cv": cv})
    assert created.status_code == 202
    run_id = created.json()["run_id"]

    fetched = client.get(f"/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["status"] == "queued"


def test_bad_input_is_rejected(database_url, client, job_ad):
    response = client.post("/runs", json={"job_ad": job_ad, "cv": "short"})
    assert response.status_code == 422


def test_bad_run_id_is_rejected(database_url, client):
    assert client.get("/runs/not-a-uuid").status_code == 422


def test_unknown_run_is_404(database_url, client):
    assert client.get(f"/runs/{uuid.uuid4()}").status_code == 404


def test_other_users_run_is_404(database_url, client, job_ad, cv):
    """Tenant isolation: user B can't see user A's run, and can't tell it exists."""
    run_id = client.post("/runs", json={"job_ad": job_ad, "cv": cv}).json()["run_id"]

    app.dependency_overrides[get_current_user] = lambda: "someone-else"
    assert client.get(f"/runs/{run_id}").status_code == 404


def test_a_new_run_is_put_on_the_queue(database_url, client, job_ad, cv, published):
    created = client.post("/runs", json={"job_ad": job_ad, "cv": cv})
    assert [str(run_id) for run_id in published] == [created.json()["run_id"]]


def test_a_failed_publish_still_saves_the_run(database_url, client, job_ad, cv, monkeypatch):
    """If Pub/Sub is down, the user still gets their run; the sweeper re-publishes it."""
    def queue_is_down(run_id):
        raise ConnectionError("Pub/Sub unreachable")

    monkeypatch.setattr("api.main.publish_run", queue_is_down)
    created = client.post("/runs", json={"job_ad": job_ad, "cv": cv})
    assert created.status_code == 202
    assert client.get(f"/runs/{created.json()['run_id']}").json()["status"] == "queued"

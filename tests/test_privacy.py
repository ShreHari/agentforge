"""Privacy: contact details never stored, a run can be deleted, old runs are deleted."""


import psycopg
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver

from agent.graph import build_graph
from agent.privacy import redact_contact_details
from agent.tracker import PostgresTracker
from api.main import app, get_current_user
from worker.sweeper import RETENTION_DAYS, delete_old_runs


@pytest.mark.parametrize("text, expected", [
    ("Email: alex.example+cv@mail.example.com", "Email: [email]"),
    ("Phone +44 7899 123456 or 07899123456", "Phone [phone] or [phone]"),
    ("Tel (0114) 222 3333", "Tel [phone]"),
    ("See https://alex.example/cv and www.alex.example", "See [link] and [link]"),
    ("linkedin.com/in/alex-example | github.com/alexexample | alex.github.io",
     "[link] | [link] | [link]"),
])
def test_contact_details_are_removed(text, expected):
    assert redact_contact_details(text) == expected


@pytest.mark.parametrize("text", [
    "BSc Computer Science, Nov 2021 - Apr 2025",  # a date range is not a phone number
    "Processed 100,000+ records weekly; 64.0% accuracy",
    "IEEE CCPIS, 2025. Python, FastAPI, PostgreSQL",
])
def test_ordinary_cv_text_is_left_alone(text):
    assert redact_contact_details(text) == text


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_current_user, None)


def saved_input(database_url, run_id) -> dict:
    with psycopg.connect(database_url) as conn:
        return conn.execute("SELECT input FROM runs WHERE id = %s", (run_id,)).fetchone()[0]


def test_contact_details_are_never_saved(database_url, client, job_ad, cv):
    cv_with_contacts = cv + "\nContact: alex@mail.example.com, +44 7899 123456"
    run_id = client.post("/runs", json={"job_ad": job_ad, "cv": cv_with_contacts}).json()["run_id"]
    saved = saved_input(database_url, run_id)
    assert "alex@mail.example.com" not in saved["cv"]
    assert "7899" not in saved["cv"]
    assert "[email]" in saved["cv"]


def test_deleting_a_run_removes_everything(database_url, client, job_ad, cv):
    run_id = client.post("/runs", json={"job_ad": job_ad, "cv": cv}).json()["run_id"]
    # Give it checkpoints and a tracker row, as a finished run has (fake AI, so instant).
    with PostgresSaver.from_conn_string(database_url) as saver:
        graph = build_graph(saver, PostgresTracker(database_url))
        graph.invoke({"run_id": run_id, "user_id": "test-user", "job_ad": job_ad, "cv": cv},
                     {"configurable": {"thread_id": run_id}})

    assert client.delete(f"/runs/{run_id}").status_code == 204

    assert client.get(f"/runs/{run_id}").status_code == 404
    with psycopg.connect(database_url) as conn:
        for table, column in [("run_events", "run_id"), ("tracker", "run_id"),
                              ("checkpoints", "thread_id")]:
            count = conn.execute(
                f"SELECT count(*) FROM {table} WHERE {column}::text = %s", (run_id,)
            ).fetchone()[0]
            assert count == 0, f"{table} still has rows for the deleted run"


def test_nobody_can_delete_someone_elses_run(database_url, client, job_ad, cv):
    run_id = client.post("/runs", json={"job_ad": job_ad, "cv": cv}).json()["run_id"]
    app.dependency_overrides[get_current_user] = lambda: "someone-else"
    assert client.delete(f"/runs/{run_id}").status_code == 404
    app.dependency_overrides[get_current_user] = lambda: "test-user"
    assert client.get(f"/runs/{run_id}").status_code == 200  # still there


def test_runs_older_than_the_retention_period_are_deleted(database_url, client, job_ad, cv):
    old = client.post("/runs", json={"job_ad": job_ad, "cv": cv}).json()["run_id"]
    new = client.post("/runs", json={"job_ad": job_ad, "cv": cv}).json()["run_id"]
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("UPDATE runs SET created_at = now() - make_interval(days => %s) "
                     "WHERE id = %s", (RETENTION_DAYS + 1, old))
        assert delete_old_runs(conn) >= 1
        remaining = {str(row[0]) for row in conn.execute(
            "SELECT id FROM runs WHERE id IN (%s, %s)", (old, new))}
    assert remaining == {new}

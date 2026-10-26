"""The sweeper, against real Postgres (the test database). Skipped without Postgres.

Other tests leave runs in the test database too, so these tests check what happened to
THEIR runs ("is my run in the published list?"), not the exact size of the list.
"""

import uuid

import psycopg
import pytest
from psycopg.types.json import Jsonb

from worker.sweeper import GAVE_UP_ERROR, MAX_ATTEMPTS, QUEUED_TOO_LONG_SECONDS, sweep


@pytest.fixture
def conn(database_url):
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


def new_run(conn, *, status: str, attempt: int = 0, lease_seconds: int | None = None,
            age_seconds: int = 0) -> uuid.UUID:
    """A run in a given state. age_seconds: how long ago it was last updated."""
    run_id = uuid.uuid4()
    conn.execute("INSERT INTO users (id) VALUES ('sweeper-test') ON CONFLICT (id) DO NOTHING")
    conn.execute(
        "INSERT INTO runs (id, user_id, status, attempt, input, updated_at, lease_until) "
        "VALUES (%s, 'sweeper-test', %s, %s, %s, now() - make_interval(secs => %s), "
        "CASE WHEN %s::int IS NULL THEN NULL ELSE now() + make_interval(secs => %s::int) END)",
        (run_id, status, attempt, Jsonb({}), age_seconds, lease_seconds, lease_seconds),
    )
    return run_id


def state(conn, run_id) -> tuple:
    return conn.execute(
        "SELECT status, attempt, lease_until IS NULL FROM runs WHERE id = %s", (run_id,)
    ).fetchone()


def run_sweep(conn) -> list:
    published: list = []
    sweep(conn, published.append)
    return published


def test_a_run_whose_worker_died_is_put_back_on_the_queue(conn):
    run_id = new_run(conn, status="running", attempt=1, lease_seconds=-10)
    assert run_id in run_sweep(conn)
    assert state(conn, run_id) == ("queued", 1, True)  # lease cleared, attempts kept


def test_a_run_with_a_live_worker_is_left_alone(conn):
    run_id = new_run(conn, status="running", attempt=1, lease_seconds=60)
    assert run_id not in run_sweep(conn)
    assert state(conn, run_id) == ("running", 1, False)


def test_a_run_that_keeps_killing_workers_is_failed_not_retried(conn):
    run_id = new_run(conn, status="running", attempt=MAX_ATTEMPTS, lease_seconds=-10)
    assert run_id not in run_sweep(conn)
    status, error = conn.execute(
        "SELECT status, error FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert (status, error) == ("failed", GAVE_UP_ERROR)
    events = conn.execute("SELECT kind FROM run_events WHERE run_id = %s", (run_id,)).fetchall()
    assert events == [("failed",)]


def test_a_forgotten_queued_run_is_republished(conn):
    run_id = new_run(conn, status="queued", age_seconds=QUEUED_TOO_LONG_SECONDS + 60)
    assert run_id in run_sweep(conn)
    assert state(conn, run_id) == ("queued", 0, True)


def test_a_recently_queued_run_is_left_alone(conn):
    """It probably just hasn't been picked up yet: re-publishing would only add a duplicate."""
    run_id = new_run(conn, status="queued", age_seconds=10)
    assert run_id not in run_sweep(conn)


def test_finished_runs_are_never_touched(conn):
    for status in ("succeeded", "failed"):
        run_id = new_run(conn, status=status, attempt=1, age_seconds=3600)
        assert run_id not in run_sweep(conn)
        assert state(conn, run_id) == (status, 1, True)


def test_the_next_sweep_does_not_publish_the_same_run_again(conn):
    run_id = new_run(conn, status="queued", age_seconds=QUEUED_TOO_LONG_SECONDS + 60)
    assert run_id in run_sweep(conn)
    assert run_id not in run_sweep(conn)  # it waits another QUEUED_TOO_LONG_SECONDS


def test_a_failed_publish_is_retried_by_a_later_sweep(conn):
    run_id = new_run(conn, status="running", attempt=1, lease_seconds=-10)

    def queue_is_down(_):
        raise ConnectionError("Pub/Sub unreachable")

    summary = sweep(conn, queue_is_down)  # must not crash
    assert summary["published"] == 0
    assert state(conn, run_id)[0] == "queued"  # still safe in Postgres for the next sweep

"""The worker's database steps, against real Postgres. Skipped if Postgres isn't running.

These tests are the specification for worker/main.py: write the code until they pass.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from psycopg.types.json import Jsonb

from worker.main import claim_run


@pytest.fixture
def conn(database_url):
    # autocommit: every statement is saved straight away, like separate requests
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


def new_run(conn, status: str = "queued", attempt: int = 0,
            lease_seconds: int | None = None) -> uuid.UUID:
    """Insert a run directly. lease_seconds=-10 means 'the lease ran out 10 seconds ago'."""
    run_id = uuid.uuid4()
    conn.execute("INSERT INTO users (id) VALUES ('worker-test') ON CONFLICT (id) DO NOTHING")
    conn.execute(
        "INSERT INTO runs (id, user_id, status, attempt, input) "
        "VALUES (%s, 'worker-test', %s, %s, %s)",
        (run_id, status, attempt, Jsonb({})),
    )
    if lease_seconds is not None:
        conn.execute(
            "UPDATE runs SET lease_until = now() + make_interval(secs => %s) WHERE id = %s",
            (lease_seconds, run_id),
        )
    return run_id


def status_and_attempt(conn, run_id: uuid.UUID) -> tuple:
    return conn.execute("SELECT status, attempt FROM runs WHERE id = %s", (run_id,)).fetchone()


def test_a_queued_run_can_be_claimed(conn):
    run_id = new_run(conn)
    assert claim_run(conn, run_id) is True
    assert status_and_attempt(conn, run_id) == ("running", 1)


def test_a_claimed_run_cannot_be_claimed_again_while_its_lease_lasts(conn):
    run_id = new_run(conn)
    assert claim_run(conn, run_id) is True
    assert claim_run(conn, run_id) is False  # e.g. Pub/Sub delivered the message twice
    assert status_and_attempt(conn, run_id) == ("running", 1)


def test_a_run_whose_worker_died_can_be_claimed_again(conn):
    # A worker claimed it (attempt 1), then died: its lease ran out 10 seconds ago.
    run_id = new_run(conn, status="running", attempt=1, lease_seconds=-10)
    assert claim_run(conn, run_id) is True
    assert status_and_attempt(conn, run_id) == ("running", 2)


def test_a_finished_run_is_never_claimed(conn):
    for status in ("succeeded", "failed"):
        run_id = new_run(conn, status=status, attempt=1)
        assert claim_run(conn, run_id) is False
        assert status_and_attempt(conn, run_id) == (status, 1)


def test_two_workers_racing_for_one_run_only_one_wins(conn, database_url):
    """Two workers, each with its own connection, claim the same run at the same moment."""
    run_id = new_run(conn)

    def one_worker(_):
        with psycopg.connect(database_url, autocommit=True) as own_conn:
            return claim_run(own_conn, run_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one_worker, range(2)))

    assert sorted(results) == [False, True]
    assert status_and_attempt(conn, run_id) == ("running", 1)

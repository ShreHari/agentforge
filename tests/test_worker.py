"""The worker's database steps, against real Postgres. Skipped if Postgres isn't running.

These tests are the specification for worker/main.py: write the code until they pass.
"""

import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from langgraph.checkpoint.memory import InMemorySaver
from psycopg.types.json import Jsonb

from agent import nodes
from agent.graph import build_graph
from agent.tracker import InMemoryTracker
from worker.main import FRIENDLY_ERROR, claim_run, finish_run, process_run, renew_lease


@pytest.fixture
def conn(database_url):
    # autocommit: every statement is saved straight away, like separate requests
    with psycopg.connect(database_url, autocommit=True) as connection:
        yield connection


def new_run(conn, status: str = "queued", attempt: int = 0,
            lease_seconds: int | None = None, run_input: dict | None = None) -> uuid.UUID:
    """Insert a run directly. lease_seconds=-10 means 'the lease ran out 10 seconds ago'."""
    run_id = uuid.uuid4()
    conn.execute("INSERT INTO users (id) VALUES ('worker-test') ON CONFLICT (id) DO NOTHING")
    conn.execute(
        "INSERT INTO runs (id, user_id, status, attempt, input) "
        "VALUES (%s, 'worker-test', %s, %s, %s)",
        (run_id, status, attempt, Jsonb(run_input or {})),
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


def lease_seconds_left(conn, run_id: uuid.UUID) -> float | None:
    row = conn.execute(
        "SELECT extract(epoch FROM lease_until - now()) FROM runs WHERE id = %s", (run_id,)
    ).fetchone()
    return None if row[0] is None else float(row[0])


def events(conn, run_id: uuid.UUID) -> list[str]:
    rows = conn.execute("SELECT kind FROM run_events WHERE run_id = %s ORDER BY id", (run_id,))
    return [row[0] for row in rows]


def test_renew_lease_pushes_the_lease_forward(conn):
    run_id = new_run(conn)
    claim_run(conn, run_id, lease_seconds=5)
    renew_lease(conn, run_id, lease_seconds=120)
    assert 100 < lease_seconds_left(conn, run_id) <= 120


def test_finish_run_saves_a_success(conn):
    run_id = new_run(conn)
    claim_run(conn, run_id)
    assert finish_run(conn, run_id, result={"match": {"score": 69}}, error=None) is True
    row = conn.execute("SELECT status, result, error FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert row == ("succeeded", {"match": {"score": 69}}, None)
    assert lease_seconds_left(conn, run_id) is None  # the lease is cleared
    assert events(conn, run_id) == ["succeeded"]


def test_finish_run_saves_a_failure(conn):
    run_id = new_run(conn)
    claim_run(conn, run_id)
    assert finish_run(conn, run_id, result=None, error="The AI service was down") is True
    row = conn.execute("SELECT status, result, error FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert row == ("failed", None, "The AI service was down")
    assert events(conn, run_id) == ["failed"]


def test_a_finished_run_cannot_be_finished_again(conn):
    """A late, duplicate worker must not overwrite a result that is already saved."""
    run_id = new_run(conn)
    claim_run(conn, run_id)
    finish_run(conn, run_id, result={"match": {"score": 69}}, error=None)
    assert finish_run(conn, run_id, result=None, error="late duplicate") is False
    row = conn.execute("SELECT status, error FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert row == ("succeeded", None)
    assert events(conn, run_id) == ["succeeded"]  # no second event either


# --- process_run: claim, run the agent, finish. Fake AI (tests always use FAKE_LLM=true),
# real Postgres for the runs table, checkpoints in memory.


@pytest.fixture
def graph():
    return build_graph(InMemorySaver(), InMemoryTracker())


@pytest.fixture
def run_input(job_ad, cv) -> dict:
    return {"job_ad": job_ad, "cv": cv}


def test_process_run_runs_the_agent_and_saves_the_result(conn, graph, run_input):
    run_id = new_run(conn, run_input=run_input)

    assert process_run(conn, graph, run_id) == "succeeded"

    status, result = conn.execute(
        "SELECT status, result FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert status == "succeeded"
    assert set(result) == {"brief", "match", "draft"}  # what the web page shows
    assert events(conn, run_id) == ["step_done"] * 5 + ["succeeded"]  # one per step


def test_process_run_skips_a_run_another_worker_owns(conn, graph, run_input):
    run_id = new_run(conn, status="running", attempt=1, lease_seconds=60, run_input=run_input)
    assert process_run(conn, graph, run_id) == "skipped"
    assert status_and_attempt(conn, run_id) == ("running", 1)  # untouched
    assert events(conn, run_id) == []


def test_a_crash_inside_the_agent_fails_the_run_with_a_friendly_message(
        conn, graph, run_input, monkeypatch):
    def broken_match(state):
        raise RuntimeError("internal detail: connection to 10.0.0.7 refused")

    monkeypatch.setattr(nodes, "match", broken_match)
    graph = build_graph(InMemorySaver(), InMemoryTracker())  # rebuilt with the broken step
    run_id = new_run(conn, run_input=run_input)

    assert process_run(conn, graph, run_id) == "failed"

    status, error = conn.execute(
        "SELECT status, error FROM runs WHERE id = %s", (run_id,)).fetchone()
    assert status == "failed"
    assert error == FRIENDLY_ERROR  # the user never sees internal details
    assert events(conn, run_id) == ["step_done", "step_done", "failed"]  # validate, research


def test_a_run_whose_worker_died_resumes_from_its_checkpoint(conn, run_input, monkeypatch):
    """Worker 1 got through validate and research, then died (no finish_run, lease left to
    run out). Worker 2 must carry on from match, not start again."""
    checkpointer, tracker = InMemorySaver(), InMemoryTracker()
    run_id = new_run(conn, status="running", attempt=1, lease_seconds=-10, run_input=run_input)

    # Worker 1: run the graph until it dies inside match.
    def dies_in_match(state):
        raise SystemExit("worker 1 died")

    monkeypatch.setattr(nodes, "match", dies_in_match)
    worker_1_graph = build_graph(checkpointer, tracker)
    config = {"configurable": {"thread_id": str(run_id)}}
    payload = {"run_id": str(run_id), "user_id": "worker-test", **run_input}
    with pytest.raises(SystemExit):
        for _ in worker_1_graph.stream(payload, config, stream_mode="updates",
                                       durability="sync"):
            pass
    monkeypatch.undo()  # match works again

    # Worker 2: same checkpoints, a working match step, and validate is being counted.
    validate_calls = []
    original_validate = nodes.validate
    monkeypatch.setattr(nodes, "validate",
                        lambda state: validate_calls.append(1) or original_validate(state))
    worker_2_graph = build_graph(checkpointer, tracker)

    assert process_run(conn, worker_2_graph, run_id) == "succeeded"
    assert validate_calls == []  # finished steps were not run again
    assert status_and_attempt(conn, run_id) == ("succeeded", 2)
    assert events(conn, run_id) == ["step_done"] * 3 + ["succeeded"]  # match, draft, save


def test_a_run_with_broken_saved_input_fails_on_its_own(conn, graph):
    """Found in an end-to-end test: a run with no job_ad used to crash the WHOLE worker."""
    run_id = new_run(conn, run_input={})  # nothing in it
    assert process_run(conn, graph, run_id) == "failed"
    assert status_and_attempt(conn, run_id) == ("failed", 1)

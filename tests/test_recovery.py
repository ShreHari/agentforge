"""Crash recovery against real Postgres, with a real process kill.

Each test starts the agent as a separate process, kills it mid-run with a simulated
`kill -9`, then resumes it. This is the evidence for the recovery claims in the README.
"""

import subprocess
import sys
import uuid
from pathlib import Path

import psycopg

from agent.tracker import PostgresTracker

ROOT = Path(__file__).resolve().parents[1]


def run_agent(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "agent.run", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )


def tracker_rows(database_url: str, run_id: str) -> int:
    with psycopg.connect(database_url) as conn:
        return conn.execute("SELECT count(*) FROM tracker WHERE run_id = %s", (run_id,)).fetchone()[0]


def test_postgres_tracker_is_idempotent(database_url):
    tracker = PostgresTracker(database_url)
    run_id = str(uuid.uuid4())

    assert tracker.save(run_id=run_id, user_id="u", company="Acme", match_score=50) is True
    assert tracker.save(run_id=run_id, user_id="u", company="Acme", match_score=50) is False
    assert tracker_rows(database_url, run_id) == 1


def test_crash_during_research_then_resume(database_url):
    run_id = str(uuid.uuid4())

    crashed = run_agent("--run-id", run_id, "--crash-at", "research")
    assert crashed.returncode == 137  # killed, like kill -9
    assert tracker_rows(database_url, run_id) == 0

    resumed = run_agent("--resume", run_id)
    assert resumed.returncode == 0, resumed.stderr
    assert "Resuming run" in resumed.stdout and "at step: research" in resumed.stdout
    assert "done: validate" not in resumed.stdout  # finished steps are not re-run
    assert tracker_rows(database_url, run_id) == 1


def test_crash_after_tracker_write_keeps_one_row(database_url):
    """The hard case: the row was written, but the process died before the checkpoint.

    On resume LangGraph re-runs the save step (it has no record it finished), and the
    UNIQUE constraint on tracker.run_id turns the second write into a no-op.
    """
    run_id = str(uuid.uuid4())

    crashed = run_agent("--run-id", run_id, "--crash-at", "after_save")
    assert crashed.returncode == 137
    assert tracker_rows(database_url, run_id) == 1

    resumed = run_agent("--resume", run_id)
    assert resumed.returncode == 0, resumed.stderr
    assert "at step: save" in resumed.stdout
    assert "row already existed" in resumed.stdout
    assert tracker_rows(database_url, run_id) == 1


def test_resuming_a_finished_run_does_nothing(database_url):
    run_id = str(uuid.uuid4())
    assert run_agent("--run-id", run_id).returncode == 0

    again = run_agent("--resume", run_id)
    assert again.returncode == 0
    assert "already finished" in again.stdout
    assert tracker_rows(database_url, run_id) == 1

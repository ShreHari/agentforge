"""Shared test setup. pytest loads this file automatically before any test.

A "fixture" is a value a test asks for by naming it as an argument: a test written as
`def test_x(job_ad):` gets the sample job ad text passed in.
"""

import os
from pathlib import Path

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo

from core.config import Settings, get_settings
from core.db import apply_schema, ping

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

# Tests use their OWN database, agentforge_test, on the same Postgres. They never touch
# the runs your app uses, so a running worker or the sweeper can never pick up test runs
# and spend real AI credits on them. Setting the environment variable here, before any
# test runs, points everything at it: the api, the worker code and any subprocess.
APP_DATABASE_URL = Settings().database_url  # read from .env (or the environment)
TEST_DATABASE = "agentforge_test"
TEST_DATABASE_URL = make_conninfo(APP_DATABASE_URL, dbname=TEST_DATABASE)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL  # an environment variable beats .env
get_settings.cache_clear()


@pytest.fixture(autouse=True)
def published(monkeypatch) -> list:
    """Every test gets a pretend queue: the api "publishes" run IDs into this list.

    Without it, running the tests while the emulator is up would put test runs on your
    real queue, and a running worker with FAKE_LLM=false would spend real credits on them.
    """
    sent: list = []
    monkeypatch.setattr("api.main.publish_run", sent.append)
    return sent


@pytest.fixture(autouse=True)
def signed_in():
    """Most tests act as one signed-in user, "test-user", without a real Google token.
    tests/test_auth.py switches this off to test sign-in itself."""
    from api.main import app, get_current_user

    app.dependency_overrides[get_current_user] = lambda: "test-user"
    yield
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    """Every test runs in fake mode with no crash points, whatever your .env says."""
    monkeypatch.setenv("FAKE_LLM", "true")
    # Tests create many runs as one user; only the limit test sets a low limit.
    monkeypatch.setenv("RUNS_PER_HOUR_PER_USER", "1000000")
    monkeypatch.delenv("CRASH_AT", raising=False)
    get_settings.cache_clear()  # make get_settings() re-read the environment
    yield
    get_settings.cache_clear()


@pytest.fixture
def job_ad() -> str:
    return (SAMPLES / "job_ad.txt").read_text()


@pytest.fixture
def cv() -> str:
    return (SAMPLES / "cv.txt").read_text()


@pytest.fixture(scope="session")
def database_url() -> str:
    """The test database, created if missing; or skip the test if Postgres isn't running."""
    if not ping(APP_DATABASE_URL):
        pytest.skip("Postgres isn't running: docker compose up -d")
    with psycopg.connect(APP_DATABASE_URL, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DATABASE,)
        ).fetchone()
        if not exists:
            # sql.Identifier quotes the name safely; a database name can't be a %s parameter
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(TEST_DATABASE)))
    apply_schema(TEST_DATABASE_URL)
    return TEST_DATABASE_URL

"""Shared test setup. pytest loads this file automatically before any test.

A "fixture" is a value a test asks for by naming it as an argument: a test written as
`def test_x(job_ad):` gets the sample job ad text passed in.
"""

from pathlib import Path

import pytest

from core.config import get_settings
from core.db import apply_schema, ping

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture(autouse=True)
def fake_mode(monkeypatch):
    """Every test runs in fake mode with no crash points, whatever your .env says."""
    monkeypatch.setenv("FAKE_LLM", "true")
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
    """A ready database, or skip the test if Postgres isn't running."""
    url = get_settings().database_url
    if not ping(url):
        pytest.skip("Postgres isn't running: docker compose up -d")
    apply_schema(url)
    return url

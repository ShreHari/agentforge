"""Retries: temporary errors are retried, real errors fail at once. No AI, no waiting."""

import pytest
import requests
from langchain_core.exceptions import (
    ModelAPIError,
    ModelAuthenticationError,
    ModelInvalidRequestError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import RetryPolicy

from agent import nodes
from agent.graph import build_graph
from agent.retry import is_worth_retrying
from agent.tracker import InMemoryTracker


def http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(response=response)


@pytest.mark.parametrize("error", [
    ModelRateLimitError("429: too many requests"),
    ModelAPIError("503: the model is overloaded"),
    ModelTimeoutError("the model took too long"),
    TimeoutError(),
    requests.ConnectionError(),
    http_error(429),
    http_error(503),
])
def test_temporary_errors_are_retried(error):
    assert is_worth_retrying(error) is True


@pytest.mark.parametrize("error", [
    ModelInvalidRequestError("400: bad request"),
    ModelAuthenticationError("401: wrong API key"),
    http_error(400),
    ValueError("a bug in our code"),
    KeyError("job_ad"),
])
def test_real_errors_are_not_retried(error):
    assert is_worth_retrying(error) is False


# A policy with tiny waits, so the tests below don't sleep for seconds.
FAST_RETRY = RetryPolicy(max_attempts=3, initial_interval=0.01, jitter=False,
                         retry_on=is_worth_retrying)


def run_graph_with_flaky_match(monkeypatch, job_ad, cv, failures: list[Exception]):
    """Run the whole graph (fake AI). match raises each error in `failures` once, in
    order, then works. Returns how many times match was called."""
    calls = []
    real_match = nodes.match

    def flaky_match(state):
        calls.append(1)
        if len(calls) <= len(failures):
            raise failures[len(calls) - 1]
        return real_match(state)

    monkeypatch.setattr(nodes, "match", flaky_match)
    graph = build_graph(InMemorySaver(), InMemoryTracker(), retry_policy=FAST_RETRY)
    config = {"configurable": {"thread_id": "retry-test"}}
    graph.invoke({"run_id": "r", "user_id": "u", "job_ad": job_ad, "cv": cv}, config)
    return len(calls)


def test_a_step_that_hits_a_rate_limit_twice_still_succeeds(monkeypatch, job_ad, cv):
    calls = run_graph_with_flaky_match(
        monkeypatch, job_ad, cv,
        failures=[ModelRateLimitError("429"), ModelRateLimitError("429")],
    )
    assert calls == 3  # failed, failed, worked


def test_a_real_error_is_not_retried(monkeypatch, job_ad, cv):
    with pytest.raises(ModelInvalidRequestError):
        run_graph_with_flaky_match(
            monkeypatch, job_ad, cv, failures=[ModelInvalidRequestError("400")]
        )


def test_retries_give_up_after_three_attempts(monkeypatch, job_ad, cv):
    with pytest.raises(ModelRateLimitError):
        run_graph_with_flaky_match(
            monkeypatch, job_ad, cv, failures=[ModelRateLimitError("429")] * 3
        )

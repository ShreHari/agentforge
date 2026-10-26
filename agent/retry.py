"""When a step fails, should LangGraph try it again?

Only for errors that may pass on their own: "too many requests" (429), a server error on
the provider's side (5xx), a timeout, a dropped connection. Retrying anything else (a
bug, bad input, a wrong API key) just gives the same error again, slower and at a cost.

There are two layers of retries, on purpose:
  1. Inside one AI call: the model connector retries quick network blips itself
     (max_retries in agent/llm.py).
  2. Around a whole step (this file): if the step still fails with a temporary error,
     LangGraph waits and runs the step again: about 5 seconds, then 10, with a little
     randomness ("jitter") so many workers don't all retry at the same moment.
"""

import requests
from langchain_core.exceptions import ModelError
from langgraph.types import RetryPolicy
from tavily.errors import TimeoutError as TavilyTimeoutError


def is_worth_retrying(error: Exception) -> bool:
    # From the AI model: LangChain has already sorted these into "retrying may help"
    # (rate limit, server error, timeout, connection) and "it won't" (bad request,
    # wrong key, unknown model, input too long).
    if isinstance(error, ModelError):
        return error.is_retryable

    # From the web search (Tavily uses the requests library) or the network in general.
    if isinstance(error, (TimeoutError, TavilyTimeoutError, requests.Timeout,
                          requests.ConnectionError, ConnectionError)):
        return True
    if isinstance(error, requests.HTTPError) and error.response is not None:
        status = error.response.status_code
        return status == 429 or status >= 500

    return False  # a bug or bad input: the same thing would fail again


# Used on the research, match and draft steps (agent/graph.py).
RETRY_POLICY = RetryPolicy(
    max_attempts=3,  # the first try plus 2 retries
    initial_interval=5.0,  # seconds before the first retry
    backoff_factor=2.0,  # then double the wait each time
    jitter=True,
    retry_on=is_worth_retrying,
)

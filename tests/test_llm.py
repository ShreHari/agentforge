"""The model factory: settings flow through to the model. No AI call is made."""

from langchain_core.rate_limiters import InMemoryRateLimiter

from agent import llm
from core.config import get_settings


def test_rate_limiter_follows_the_setting(monkeypatch):
    monkeypatch.setenv("GOOGLE_API_KEY", "not-a-real-key")  # building the model needs one
    monkeypatch.setenv("LLM_MODEL", "google_genai:gemini-3.5-flash")
    monkeypatch.setenv("LLM_REQUESTS_PER_MINUTE", "4")
    get_settings.cache_clear()
    llm.get_model.cache_clear()

    model = llm.get_model()

    assert isinstance(model.rate_limiter, InMemoryRateLimiter)
    assert model.rate_limiter.requests_per_second == 4 / 60
    llm.get_model.cache_clear()  # don't leave this test's model for other tests

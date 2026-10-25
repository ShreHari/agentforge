"""One place to create the AI model, so every step uses the same settings."""

from functools import lru_cache

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter

from core.config import get_settings


@lru_cache  # create the model once, then reuse it
def get_model():
    load_dotenv()  # copies GOOGLE_API_KEY from .env into the environment
    settings = get_settings()
    model_name = settings.llm_model
    if not model_name:
        raise RuntimeError(
            "Set LLM_MODEL in .env, for example: LLM_MODEL=google_genai:gemini-3.5-flash"
        )
    # A rate limiter is a gate that lets calls through at a steady pace, so we never go
    # over the provider's limit. It only paces calls inside this one program; with many
    # pods in production, the provider's paid quota is what matters.
    rate_limiter = None
    if settings.llm_requests_per_minute:
        rate_limiter = InMemoryRateLimiter(
            requests_per_second=settings.llm_requests_per_minute / 60,
            check_every_n_seconds=0.1,
            max_bucket_size=1,  # no bursts: one call at a time
        )

    # timeout: give up on one call after 60 seconds instead of hanging forever.
    # max_retries: the connector retries failed calls itself; keep that small.
    return init_chat_model(
        model_name, temperature=0, timeout=60, max_retries=2, rate_limiter=rate_limiter
    )

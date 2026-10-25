"""One place to create the AI model, so every step uses the same settings."""

from functools import lru_cache

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

from core.config import get_settings


@lru_cache  # create the model once, then reuse it
def get_model():
    load_dotenv()  # copies GOOGLE_API_KEY from .env into the environment
    model_name = get_settings().llm_model
    if not model_name:
        raise RuntimeError(
            "Set LLM_MODEL in .env, for example: LLM_MODEL=google_genai:gemini-3.5-flash"
        )
    # timeout: give up on one call after 60 seconds instead of hanging forever.
    # max_retries: the connector retries failed calls itself; keep that small.
    return init_chat_model(model_name, temperature=0, timeout=60, max_retries=2)

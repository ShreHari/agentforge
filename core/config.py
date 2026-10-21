"""Settings for the whole project, read from environment variables (or a .env file).

Every part of AgentForge (agent, api, worker) reads its settings from here, so a value
like the database address is defined in exactly one place.

On your laptop the defaults below just work. On GKE (Day 7) the same names are set as
environment variables, and secrets come from Secret Manager, never from this file.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read a .env file in the folder you run commands from, if one exists.
    # Field names match environment variables, ignoring case: database_url <- DATABASE_URL.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Where Postgres is. This default matches docker-compose.yml.
    database_url: str = "postgresql://agentforge:agentforge@localhost:5432/agentforge"

    # True = the agent uses fake logic (no AI calls, no API keys, no cost).
    # Day 2 adds the real model calls; until then, keep this True.
    fake_llm: bool = True

    # Which model to use once fake_llm is False (Day 2).
    llm_model: str = ""


@lru_cache
def get_settings() -> Settings:
    """Build the settings once and reuse them (lru_cache remembers the result)."""
    return Settings()

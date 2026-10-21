"""Database helpers shared by the agent, api and worker.

Run this file to create all the tables:

    uv run python -m core.db
"""

from pathlib import Path

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from core.config import get_settings

SCHEMA_FILE = Path(__file__).resolve().parents[1] / "db" / "schema.sql"


def ping(database_url: str, timeout_seconds: int = 2) -> bool:
    """True if Postgres answers within the timeout, False otherwise."""
    try:
        with psycopg.connect(database_url, connect_timeout=timeout_seconds) as conn:
            conn.execute("SELECT 1")
        return True
    except psycopg.OperationalError:
        return False


def apply_schema(database_url: str) -> None:
    """Create our app tables and LangGraph's checkpoint tables. Safe to run again."""
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text())

    # LangGraph creates and upgrades its own tables (checkpoints, checkpoint_writes, ...).
    with PostgresSaver.from_conn_string(database_url) as checkpointer:
        checkpointer.setup()


if __name__ == "__main__":
    url = get_settings().database_url
    if not ping(url):
        raise SystemExit(
            "Can't reach Postgres. Is Docker Desktop open, and did you run "
            "`docker compose up -d`?"
        )
    apply_schema(url)
    print("Database ready: app tables and LangGraph checkpoint tables exist.")

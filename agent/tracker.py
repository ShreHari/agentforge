"""The application tracker: the agent's ONE side effect.

A side effect is anything that changes the world outside the graph (a database row, an
email, a booking). Retries and crashes can make a step run twice, so a side effect must be
IDEMPOTENT: doing it twice has the same result as doing it once.

Here that's guaranteed by the database itself: tracker.run_id is UNIQUE, and the insert
says ON CONFLICT DO NOTHING. A second save for the same run is simply ignored.
"""

from typing import Protocol

import psycopg


class Tracker(Protocol):
    """Anything with this save() method can be used as a tracker.

    A Protocol is a "shape" check: the graph doesn't care whether it gets the Postgres
    tracker or the in-memory one used by tests, as long as it has save().
    """

    def save(self, *, run_id: str, user_id: str, company: str, match_score: int) -> bool:
        """Save one tracker row. Return True if a new row was created, False if it existed."""
        ...


class InMemoryTracker:
    """A tracker that keeps rows in a dict. Used by tests and --memory runs."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def save(self, *, run_id: str, user_id: str, company: str, match_score: int) -> bool:
        if run_id in self.rows:
            return False  # already saved: do nothing, like ON CONFLICT DO NOTHING
        self.rows[run_id] = {"user_id": user_id, "company": company, "match_score": match_score}
        return True


class PostgresTracker:
    """The real tracker, backed by the tracker table."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def save(self, *, run_id: str, user_id: str, company: str, match_score: int) -> bool:
        # One short connection per save keeps this simple. The worker will use a
        # connection pool instead (Day 4).
        with psycopg.connect(self.database_url) as conn:
            cursor = conn.execute(
                """
                INSERT INTO tracker (run_id, user_id, company, match_score)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (run_id, user_id, company, match_score),
            )
            # rowcount = how many rows the INSERT actually wrote: 1 the first time, 0 after.
            return cursor.rowcount == 1

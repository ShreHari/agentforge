"""The worker: takes run IDs off the queue and runs the agent.

YOU build this on Days 4 and 5. It's a skeleton on purpose: this is the code you'll be
asked about line by line, so write it yourself. Each function says what it must do.

The loop, in order:
  1. Pull a message from Pub/Sub. It contains only a run_id.
  2. Claim the run in Postgres with ONE conditional UPDATE. If the claim fails, the run
     is finished or another worker owns it: ack the message and move on.
  3. Run the graph with thread_id = run_id and durability="sync". If an earlier attempt
     crashed, LangGraph resumes from the last checkpoint automatically.
  4. Write run_events as each step finishes; renew the lease after each step.
  5. Set the run to 'succeeded' (with its result) or 'failed' (with a reason).
  6. Ack the message ONLY after step 5 is committed.

On SIGTERM (deploy, scale-down): stop pulling, let the current step finish, clear the
lease, nack the message so another worker resumes it, close connections, exit 0.

Run it (once it's built):

    uv run python -m worker.main
"""

from uuid import UUID

from psycopg.types.json import Jsonb


def claim_run(conn, run_id: UUID, lease_seconds: int = 120) -> bool:
    """TODO(Day 4): try to take ownership of a run. Return True if this worker got it.

    Use a single UPDATE on the runs table that succeeds only when the run is 'queued',
    OR it's 'running' but its lease_until is in the past (the last worker died).
    On success it sets status='running', adds 1 to attempt, and sets lease_until to
    now + lease_seconds. Check how many rows it changed to know if you won.

    Why one UPDATE and not "SELECT, then UPDATE"? Two workers could both read 'queued'
    before either writes. A single conditional UPDATE can't be won twice.
    """
    cursor = conn.execute(
        """
        UPDATE runs
        SET status = 'running',
            attempt = attempt + 1,
            lease_until = now() + make_interval(secs => %s),
            updated_at = now()
        WHERE id = %s
          AND (status = 'queued' OR (status = 'running' AND lease_until < now()))
        """,
        (lease_seconds, run_id),
    )
    return cursor.rowcount == 1


def renew_lease(conn, run_id: UUID, lease_seconds: int = 120) -> None:
    """TODO(Day 4): push lease_until forward. Call it after every graph step."""
    conn.execute(
        "UPDATE runs SET lease_until = now() + make_interval(secs => %s), updated_at = now() "
        "WHERE id = %s AND status = 'running'",
        (lease_seconds, run_id),
    )


def finish_run(conn, run_id: UUID, *, result: dict | None, error: str | None) -> bool:
    """TODO(Day 4): set status to 'succeeded' or 'failed', save result or error,
    clear lease_until, update updated_at, and add a run_events row. One transaction.

    Only a 'running' run can be finished. Return True if this call finished it, False if
    it wasn't running (e.g. a late duplicate worker), and then change nothing at all."""
    status = "succeeded" if error is None else "failed"
    with conn.transaction():  # both writes happen, or neither does
        cursor = conn.execute(
            "UPDATE runs SET status = %s, result = %s, error = %s, lease_until = NULL, "
            "updated_at = now() WHERE id = %s AND status = 'running'",
            # Jsonb stores the dict as JSON. Jsonb(None) would store a JSON null, so a
            # failed run gets a real SQL NULL instead.
            (status, Jsonb(result) if result is not None else None, error, run_id),
        )
        if cursor.rowcount != 1:  # not running: already finished, or never claimed
            return False
        conn.execute("INSERT INTO run_events (run_id, kind) VALUES (%s, %s)", (run_id, status))
    return True


def process_message(message) -> None:
    """TODO(Day 4): steps 2 to 6 above for one Pub/Sub message."""
    raise NotImplementedError("TODO(Day 4): process_message")


def install_shutdown_handler(stop_event) -> None:
    """TODO(Day 5): on SIGTERM, set stop_event so the main loop stops pulling.

    Hint: the signal module, signal.signal(signal.SIGTERM, handler).
    """
    raise NotImplementedError("TODO(Day 5): install_shutdown_handler")


def main() -> None:
    raise SystemExit("The worker isn't built yet; that's Day 4. Read the TODOs in worker/main.py.")


if __name__ == "__main__":
    main()

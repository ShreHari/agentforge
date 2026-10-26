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

Run it (Postgres running; FAKE_LLM=false in .env for real AI):

    uv run python -m worker.main

For now the worker finds queued runs by asking Postgres every 2 seconds ("polling").
Pub/Sub replaces that next: the api will publish each new run ID, and the worker will wait
for messages instead of asking. process_run() stays exactly the same.
"""

import logging
import time
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.types.json import Jsonb

from agent.graph import build_graph
from agent.tracker import PostgresTracker
from core.config import get_settings

logger = logging.getLogger("worker")

# What the user sees when a run fails. Never the real error: it can contain internal
# details (addresses, file paths, provider messages). The real error goes to the logs.
FRIENDLY_ERROR = "Something went wrong while running the agent. Please try again."


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


def process_run(conn, graph, run_id: UUID) -> str:
    """Steps 2 to 5 for one run. Returns what happened: "skipped", "succeeded" or "failed".

    `graph` is built once by main() and reused for every run.
    """
    # Step 2: claim it. False means another worker owns it, or it already finished.
    if not claim_run(conn, run_id):
        return "skipped"

    # From here on, everything is inside `try`: a problem with ONE run (bad saved input,
    # a crash in the agent) must fail that run, never kill the worker for everyone else.
    try:
        # What the user submitted, saved by the api.
        user_id, run_input = conn.execute(
            "SELECT user_id, input FROM runs WHERE id = %s", (run_id,)
        ).fetchone()

        # Step 3: the run's ID is also its LangGraph thread, so its checkpoints are found.
        config = {"configurable": {"thread_id": str(run_id)}}
        if graph.get_state(config).values:
            payload = None  # an earlier attempt got some way: carry on from its checkpoint
        else:
            payload = {"run_id": str(run_id), "user_id": user_id,
                       "job_ad": run_input["job_ad"], "cv": run_input["cv"]}

        for update in graph.stream(payload, config, stream_mode="updates", durability="sync"):
            # Step 4: after each step, record progress and say "I'm still alive".
            for step in update:
                conn.execute(
                    "INSERT INTO run_events (run_id, kind, data) VALUES (%s, 'step_done', %s)",
                    (run_id, Jsonb({"step": step})),
                )
            renew_lease(conn, run_id)
    except Exception:
        # TODO(Day 5): retry errors that may pass (a 429 or a timeout from the model)
        # instead of failing the run straight away.
        logger.exception("run %s failed", run_id)  # the full error, for us
        finish_run(conn, run_id, result=None, error=FRIENDLY_ERROR)  # a safe one, for them
        return "failed"

    # Step 5: save what the web page shows.
    values = graph.get_state(config).values
    result = {"brief": values.get("brief"), "match": values.get("match"),
              "draft": values.get("draft")}
    finish_run(conn, run_id, result=result, error=None)
    return "succeeded"


def next_queued_run(conn) -> UUID | None:
    """The oldest run waiting to start, or None. Temporary: Pub/Sub replaces this."""
    row = conn.execute(
        "SELECT id FROM runs WHERE status = 'queued' ORDER BY created_at LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def process_message(message) -> None:
    """TODO(Day 4): steps 1 and 6 around process_run(), for one Pub/Sub message."""
    raise NotImplementedError("TODO(Day 4): process_message")


def install_shutdown_handler(stop_event) -> None:
    """TODO(Day 5): on SIGTERM, set stop_event so the main loop stops pulling.

    Hint: the signal module, signal.signal(signal.SIGTERM, handler).
    """
    raise NotImplementedError("TODO(Day 5): install_shutdown_handler")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = get_settings().database_url

    # Opened once and reused for every run. autocommit: each statement is saved at once,
    # unless it's inside a `with conn.transaction()` block (as in finish_run).
    with psycopg.connect(url, autocommit=True) as conn, \
            PostgresSaver.from_conn_string(url) as checkpointer:
        checkpointer.setup()
        graph = build_graph(checkpointer, PostgresTracker(url))
        logger.info("worker started: waiting for queued runs (Ctrl+C to stop)")

        while True:
            run_id = next_queued_run(conn)
            if run_id is None:
                time.sleep(2)  # nothing to do: wait, then ask again
                continue
            logger.info("run %s: starting", run_id)
            try:
                outcome = process_run(conn, graph, run_id)
            except Exception:
                # Something outside one run broke, e.g. the database went away. Log it,
                # pause, and keep going; the run's lease runs out and it's picked up again.
                logger.exception("run %s: worker error", run_id)
                time.sleep(5)
                continue
            logger.info("run %s: %s", run_id, outcome)


if __name__ == "__main__":
    main()

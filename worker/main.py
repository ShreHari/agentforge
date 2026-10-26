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

Needs the queue too: `docker compose up -d` starts the Pub/Sub emulator, and
`uv run python -m core.queue` creates the topic and subscription.
"""

import json
import logging
import signal
import threading
from uuid import UUID

import psycopg
from google.api_core.exceptions import DeadlineExceeded, RetryError
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.types.json import Jsonb

from agent.graph import build_graph
from agent.tracker import PostgresTracker
from core import queue
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


def hand_back_run(conn, run_id: UUID) -> None:
    """Shutting down mid-run: put the run back in the queue for another worker.

    attempt goes back down by 1, because a deploy isn't the run's fault: three deploys
    during one run must not make the sweeper fail it as a "poison" run.
    """
    with conn.transaction():
        cursor = conn.execute(
            "UPDATE runs SET status = 'queued', attempt = attempt - 1, lease_until = NULL, "
            "updated_at = now() WHERE id = %s AND status = 'running'",
            (run_id,),
        )
        if cursor.rowcount == 1:
            conn.execute(
                "INSERT INTO run_events (run_id, kind) VALUES (%s, 'handed_back')", (run_id,)
            )


def process_run(conn, graph, run_id: UUID, stop: threading.Event | None = None) -> str:
    """Steps 2 to 5 for one run. Returns what happened: "skipped", "succeeded", "failed",
    or "handed back" if `stop` was set (the worker is shutting down) part-way through.

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

            # Shutting down? The step that just finished is checkpointed, so stop HERE,
            # between steps, and let another worker carry on from this point.
            if stop is not None and stop.is_set():
                hand_back_run(conn, run_id)
                return "handed back"
    except Exception:
        # Temporary errors were already retried by the step's retry policy
        # (agent/retry.py), so this one is real: fail the run.
        logger.exception("run %s failed", run_id)  # the full error, for us
        finish_run(conn, run_id, result=None, error=FRIENDLY_ERROR)  # a safe one, for them
        return "failed"

    # Step 5: save what the web page shows.
    values = graph.get_state(config).values
    result = {"brief": values.get("brief"), "match": values.get("match"),
              "draft": values.get("draft")}
    finish_run(conn, run_id, result=result, error=None)
    return "succeeded"


def run_id_from(data: bytes) -> UUID | None:
    """The run ID inside a message, or None if the message isn't what we expect."""
    try:
        return UUID(json.loads(data)["run_id"])
    except (ValueError, KeyError, TypeError):  # not JSON, no run_id, or not a valid ID
        return None


def handle_one_message(conn, graph, subscriber, subscription: str,
                       stop: threading.Event | None = None) -> str | None:
    """Steps 1 and 6 around process_run(): take ONE message, process it, then ack it.

    Returns the outcome, or None if no message arrived in time.
    """
    # Step 1: wait up to 10 seconds for one message. Short, so a worker that's been told
    # to stop notices within 10 seconds even when the queue is empty.
    try:
        response = subscriber.pull(
            request={"subscription": subscription, "max_messages": 1}, timeout=10
        )
    except (DeadlineExceeded, RetryError):
        return None  # nothing arrived: the caller simply asks again
    if not response.received_messages:
        return None
    received = response.received_messages[0]

    run_id = run_id_from(received.message.data)
    try:
        if run_id is None:
            logger.error("dropping a message that isn't a run ID: %r", received.message.data[:200])
            outcome = "bad message"  # retrying it can never help, so ack it below
        else:
            logger.info("run %s: starting", run_id)
            outcome = process_run(conn, graph, run_id, stop)
    except Exception:
        # The WORKER is in trouble (e.g. the database went away), not the run. Give the
        # message back now ("nack") so another worker can try, instead of waiting for
        # the 10-minute ack deadline to run out.
        logger.exception("run %s: worker error, giving the message back", run_id)
        subscriber.modify_ack_deadline(request={
            "subscription": subscription, "ack_ids": [received.ack_id], "ack_deadline_seconds": 0,
        })
        return "returned"

    if outcome == "handed back":
        # Shutting down mid-run: give the message back so another worker takes it now.
        subscriber.modify_ack_deadline(request={
            "subscription": subscription, "ack_ids": [received.ack_id], "ack_deadline_seconds": 0,
        })
        logger.info("run %s: handed back (shutting down)", run_id)
        return outcome

    # Step 6: ack ONLY now. The outcome is already saved in Postgres, so if this worker
    # died a moment before this line, the message would come back and claim_run() would
    # simply skip a run that's already finished.
    subscriber.acknowledge(request={"subscription": subscription, "ack_ids": [received.ack_id]})
    logger.info("run %s: %s", run_id, outcome)
    return outcome


def install_shutdown_handler(stop: threading.Event) -> None:
    """On SIGTERM (Kubernetes stopping the pod) or Ctrl+C, ask the worker to stop politely.

    The handler only SETS A FLAG. The loop and process_run() check it at safe points
    (between messages, between steps), so nothing is ever cut off half-way through.
    A second signal means "stop NOW": the default behaviour, an immediate exit.
    """
    def handler(signum, frame):
        if stop.is_set():
            raise SystemExit(1)  # asked twice: give up waiting
        logger.info("%s received: finishing the current step, then stopping",
                    signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)  # Ctrl+C on your laptop behaves the same way


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    url = get_settings().database_url

    # Opened once and reused for every run. autocommit: each statement is saved at once,
    # unless it's inside a `with conn.transaction()` block (as in finish_run).
    with psycopg.connect(url, autocommit=True) as conn, \
            PostgresSaver.from_conn_string(url) as checkpointer:
        checkpointer.setup()
        graph = build_graph(checkpointer, PostgresTracker(url))
        if get_settings().pubsub_emulator_host:
            queue.setup()  # the emulator forgets everything on restart; on Google Cloud
            # the topic and subscription are created once, ahead of time
        subscriber, subscription = queue.subscriber(), queue.subscription_path()
        logger.info("worker started: waiting for messages on %s (Ctrl+C to stop)", subscription)

        stop = threading.Event()
        install_shutdown_handler(stop)
        while not stop.is_set():
            outcome = handle_one_message(conn, graph, subscriber, subscription, stop)
            if outcome == "returned":
                stop.wait(5)  # the worker is in trouble: pause (but wake up if told to stop)

    # Leaving the `with` blocks above closed the database connections.
    logger.info("worker stopped cleanly")


if __name__ == "__main__":
    main()

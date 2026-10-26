"""The sweeper: a small job that finds stuck runs and puts them back on the queue.

On Google Cloud, Kubernetes runs it once a minute (a CronJob). On your laptop:

    uv run python -m worker.sweeper            # one sweep, then exit
    uv run python -m worker.sweeper --loop     # sweep every 60 seconds until Ctrl+C

A run can get stuck in two ways:

  1. The api saved it as 'queued', but the publish to Pub/Sub failed, so no worker will
     ever hear about it. Fix: re-publish runs that have been 'queued' for too long.
  2. A worker claimed it and died without finishing (killed, out of memory, machine
     lost), so it says 'running' but nobody is working on it. Its lease runs out.
     Fix: put it back to 'queued' and re-publish it. The next worker resumes it from
     its last checkpoint.

It also deletes runs older than RETENTION_DAYS (30): CVs are personal data.

A run that has already killed MAX_ATTEMPTS workers is probably what's killing them (a
"poison" run). Retrying it forever would keep crashing workers, so it's failed instead.

Re-publishing is always safe: claim_run() makes a duplicate message harmless.
"""

import argparse
import logging
import time
from uuid import UUID

import psycopg
from langgraph.checkpoint.postgres import PostgresSaver

from core import queue
from core.config import get_settings

logger = logging.getLogger("sweeper")

# How long a run may sit in 'queued' before we assume its message was lost. Too short
# and a busy queue gets duplicate messages (harmless, but wasteful); too long and a lost
# run waits. 5 minutes is a starting point: tune it with real traffic.
QUEUED_TOO_LONG_SECONDS = 300
MAX_ATTEMPTS = 3
GAVE_UP_ERROR = "This run failed several times in a row, so we stopped retrying it."
RETENTION_DAYS = 30  # CVs are personal data: keep them no longer than needed


def fail_poison_runs(conn) -> list[UUID]:
    """Runs whose worker died, that have already had MAX_ATTEMPTS tries: fail them."""
    with conn.transaction():
        rows = conn.execute(
            "UPDATE runs SET status = 'failed', error = %s, lease_until = NULL, "
            "updated_at = now() "
            "WHERE status = 'running' AND lease_until < now() AND attempt >= %s "
            "RETURNING id",
            (GAVE_UP_ERROR, MAX_ATTEMPTS),
        ).fetchall()
        for (run_id,) in rows:
            conn.execute(
                "INSERT INTO run_events (run_id, kind) VALUES (%s, 'failed')", (run_id,)
            )
    return [run_id for (run_id,) in rows]


def requeue_runs_with_dead_workers(conn) -> list[UUID]:
    """Runs whose worker died (lease ran out), with tries left: back to 'queued'."""
    rows = conn.execute(
        "UPDATE runs SET status = 'queued', lease_until = NULL, updated_at = now() "
        "WHERE status = 'running' AND lease_until < now() AND attempt < %s "
        "RETURNING id",
        (MAX_ATTEMPTS,),
    ).fetchall()
    return [run_id for (run_id,) in rows]


def find_forgotten_queued_runs(conn) -> list[UUID]:
    """Runs 'queued' for too long, probably because their message was lost.

    updated_at is set to now() as we pick them, so the next sweep waits another
    QUEUED_TOO_LONG_SECONDS before re-publishing the same run again.
    """
    rows = conn.execute(
        "UPDATE runs SET updated_at = now() "
        "WHERE status = 'queued' AND updated_at < now() - make_interval(secs => %s) "
        "RETURNING id",
        (QUEUED_TOO_LONG_SECONDS,),
    ).fetchall()
    return [run_id for (run_id,) in rows]


def delete_old_runs(conn) -> int:
    """Delete runs older than RETENTION_DAYS, with everything saved for them."""
    with conn.transaction():
        rows = conn.execute(
            "DELETE FROM runs WHERE created_at < now() - make_interval(days => %s) RETURNING id",
            (RETENTION_DAYS,),
        ).fetchall()  # run_events go too (ON DELETE CASCADE)
        run_ids = [run_id for (run_id,) in rows]
        if run_ids:
            conn.execute("DELETE FROM tracker WHERE run_id = ANY(%s)", (run_ids,))
            saver = PostgresSaver(conn)
            for run_id in run_ids:
                saver.delete_thread(str(run_id))  # the checkpoints hold the CV too
    return len(run_ids)


def sweep(conn, publish) -> dict[str, int]:
    """One sweep. `publish` puts a run ID on the queue (a pretend one in tests)."""
    failed = fail_poison_runs(conn)  # first, so these are never re-queued below
    requeued = requeue_runs_with_dead_workers(conn)
    forgotten = find_forgotten_queued_runs(conn)

    published = 0
    for run_id in requeued + forgotten:
        try:
            publish(run_id)
            published += 1
        except Exception:
            # It stays 'queued', so the next sweep tries again in a few minutes.
            logger.exception("run %s: couldn't publish; will retry next sweep", run_id)

    deleted = delete_old_runs(conn)
    summary = {"failed": len(failed), "requeued": len(requeued),
               "forgotten": len(forgotten), "published": published, "deleted": deleted}
    if any(summary.values()):
        logger.info("sweep: %s", summary)
    return summary


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Find stuck runs and re-queue them.")
    parser.add_argument("--loop", action="store_true", help="sweep every 60 seconds")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if get_settings().pubsub_emulator_host:
        queue.setup()
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        while True:
            summary = sweep(conn, queue.publish_run)
            if not args.loop:
                print(f"Sweep done: {summary}")
                return
            time.sleep(60)


if __name__ == "__main__":
    main()

"""The sweeper: a small job Kubernetes runs every minute (a CronJob, Day 6).

YOU build this on Day 4. It fixes the two ways a run can get stuck:

  1. The api saved the run as 'queued' but the publish to Pub/Sub failed, so no worker
     will ever see it. Fix: re-publish runs still 'queued' after 2 minutes.
  2. A worker claimed the run and then died without SIGTERM (out of memory, node lost),
     so the run says 'running' forever. Fix: runs 'running' with lease_until in the past
     go back to 'queued' and are re-published.

Re-publishing is safe because the worker's claim_run() makes duplicates harmless.

    uv run python -m worker.sweeper
"""


def main() -> None:
    raise SystemExit("The sweeper isn't built yet; that's Day 4. Read the TODOs in worker/sweeper.py.")


if __name__ == "__main__":
    main()

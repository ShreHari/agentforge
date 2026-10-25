"""Run the Job Hunt Agent from the terminal, saving a checkpoint to Postgres after every step.

    uv run python -m agent.run                        # a new run on the sample job ad and CV
    uv run python -m agent.run --crash-at research    # simulate kill -9 during research
    uv run python -m agent.run --resume <run-id>      # carry on from the last checkpoint
    uv run python -m agent.run --history <run-id>     # list the saved checkpoints
    uv run python -m agent.run --memory               # no database (checkpoints in RAM)

Crash points: validate, research, match, draft, save, after_save.
Don't pass --crash-at again when you --resume, or it will crash at the same point again.
"""

import argparse
import os
import sys
import uuid
from pathlib import Path

import psycopg
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from pydantic import ValidationError

from agent.graph import build_graph
from agent.nodes import CRASH_POINTS
from agent.tracker import InMemoryTracker, PostgresTracker
from core.config import get_settings

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Job Hunt Agent from the terminal.")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--resume", metavar="RUN_ID", help="continue a run from its last checkpoint")
    action.add_argument("--history", metavar="RUN_ID", help="list a run's checkpoints and exit")
    parser.add_argument("--run-id", help="use this ID for a new run (default: a random UUID)")
    parser.add_argument("--job-ad", type=Path, default=SAMPLES / "job_ad.txt")
    parser.add_argument("--cv", type=Path, default=SAMPLES / "cv.txt")
    parser.add_argument("--crash-at", choices=CRASH_POINTS, help="simulate a hard crash there")
    parser.add_argument("--memory", action="store_true", help="keep checkpoints in RAM only")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.crash_at:
        os.environ["CRASH_AT"] = args.crash_at  # read by nodes.maybe_crash()

    if args.memory:
        graph = build_graph(InMemorySaver(), InMemoryTracker())
        return run(graph, args)

    database_url = get_settings().database_url
    try:
        # A context manager ("with") opens the connection and always closes it at the end.
        with PostgresSaver.from_conn_string(database_url) as checkpointer:
            checkpointer.setup()  # creates LangGraph's tables if missing; safe every time
            graph = build_graph(checkpointer, PostgresTracker(database_url))
            return run(graph, args)
    except psycopg.OperationalError:
        print("Can't reach Postgres. Is Docker Desktop open, and did you run "
              "`docker compose up -d` and `uv run python -m core.db`?", file=sys.stderr)
        return 2


def run(graph, args: argparse.Namespace) -> int:
    run_id = args.resume or args.history or args.run_id or str(uuid.uuid4())
    uuid.UUID(run_id)  # fail early on a mistyped ID (the tracker table stores a UUID)

    # thread_id tells LangGraph which run's checkpoints to save and load.
    config = {"configurable": {"thread_id": run_id}}

    if args.history:
        print_history(graph, config)
        return 0

    if args.resume:
        saved = graph.get_state(config)  # the latest checkpoint for this run
        if not saved.values:
            print(f"No checkpoints found for run {run_id}.")
            return 1
        if not saved.next:
            print(f"Run {run_id} already finished. Nothing to resume.")
            print_summary(saved.values)
            return 0
        print(f"Resuming run {run_id} at step: {saved.next[0]}")
        payload = None  # None means "continue from the checkpoint", not "start again"
    else:
        print(f"Run id: {run_id}")
        print("  (if it crashes, continue with: uv run python -m agent.run --resume <run-id>)")
        payload = {
            "run_id": run_id,
            "user_id": "dev-user",  # TODO(Day 3): the signed-in user's ID
            "job_ad": args.job_ad.read_text(),
            "cv": args.cv.read_text(),
        }

    try:
        # stream() runs the graph and yields after each step: {"step name": {what it changed}}.
        # durability="sync" writes each checkpoint BEFORE the next step starts. The default,
        # "async", can lose the newest checkpoint if the process dies at the wrong moment.
        for update in graph.stream(payload, config, stream_mode="updates", durability="sync"):
            for step in update:
                print(f"  done: {step}")
    except ValidationError as error:
        print("The input was rejected by validate:")
        for problem in error.errors():
            print(f"  - {problem['loc'][0]}: {problem['msg']}")
        return 1

    print_summary(graph.get_state(config).values)
    return 0


def print_history(graph, config: dict) -> None:
    """Show every checkpoint, oldest first. 'next' is the step that would run next."""
    snapshots = list(graph.get_state_history(config))[::-1]  # LangGraph gives newest first
    if not snapshots:
        print("No checkpoints for that run.")
    for snap in snapshots:
        next_step = snap.next[0] if snap.next else "END"
        done = ", ".join(snap.values.get("steps_done", [])) or "-"
        print(f"  step {snap.metadata.get('step'):>2}   next: {next_step:<9} steps done: {done}")


def print_summary(state: dict) -> None:
    match_report = state.get("match", {})
    draft_result = state.get("draft", {})
    print()
    print(f"Company:      {state.get('company')}")
    brief = state.get("brief", {})
    print(f"Research:     {brief.get('summary')}")
    for url in brief.get("sources", []):
        print(f"  source: {url}")
    print(f"Match score:  {match_report.get('score')}/100")
    for m in match_report.get("matches", []):
        print(f"  [{m['strength']:<7}] {m['requirement']}")
    print("CV bullets:")
    for bullet in draft_result.get("cv_bullets", []):
        print(f"  - {bullet}")
    print(f"Interview questions: {len(draft_result.get('interview_questions', []))}")
    created = state.get("tracker_row_created")
    print(f"Tracker:      {'new row saved' if created else 'row already existed, nothing added'}")
    print(f"Steps done:   {' -> '.join(state.get('steps_done', []))}")


if __name__ == "__main__":
    sys.exit(main())

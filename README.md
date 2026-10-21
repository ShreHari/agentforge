# AgentForge

A low-code agent platform, built to production standards. Its first agent is the
**Job Hunt Agent**: paste a job ad and a CV, and it researches the company, matches the CV
to the requirements, and drafts tailored CV bullets and interview questions.

The engineering goal is a system that keeps working when real people use it and things go
wrong: runs are queued, every step is checkpointed, crashes resume where they stopped, and
the one side effect (saving to the user's tracker) can never happen twice.

## Status

| Part | State |
| --- | --- |
| 5-step LangGraph workflow | Works, with **fake** research, match and draft steps (no AI yet) |
| Postgres checkpoints, crash and resume | Works and tested, including a real process kill |
| Idempotent tracker write | Works and tested (`UNIQUE (run_id)`) |
| api: health checks, create and get a run | Works; sign-in is a placeholder (`dev-user`) |
| Real AI steps (LangChain, Deep Agents) | TODO, Day 2 |
| Sign-in, live progress stream (SSE), web UI | TODO, Day 3 |
| Queue (Pub/Sub), worker, sweeper | TODO, Day 4: skeletons in `worker/` |
| Retries, graceful shutdown, security | TODO, Day 5 |
| Docker images, Kubernetes, GKE | TODO, Days 6 and 7 |

## Quick start

You need Docker Desktop (open), Python 3.12 and uv.

```bash
uv sync                          # install the packages listed in uv.lock
docker compose up -d             # start Postgres
uv run python -m core.db         # create the tables
uv run pytest                    # 22 tests should pass
uv run python -m agent.run       # run the agent on the sample job ad and CV
```

## Try crash recovery

```bash
uv run python -m agent.run --crash-at research   # dies mid-run, like kill -9 (exit code 137)
uv run python -m agent.run --history <run-id>    # see which steps were checkpointed
uv run python -m agent.run --resume <run-id>     # carries on at research, skips validate
```

Then try the hard case, `--crash-at after_save`: the tracker row is written but the process
dies before the checkpoint. On resume the save step runs again, and the tracker still has
exactly one row.

## Try the api

```bash
uv run uvicorn api.main:app --reload
```

Open http://127.0.0.1:8000/docs, then try `POST /runs` and `GET /runs/{run_id}`.
Runs stay `queued` until the worker exists (Day 4).

## How the code fits together

Read the files in this order:

| File | What it does |
| --- | --- |
| `agent/state.py` | The shared notebook passed between steps; `steps_done` uses a reducer |
| `agent/schemas.py` | Pydantic models: what goes in and out, with size limits |
| `agent/nodes.py` | The five steps. Fake logic marked `TODO(Day 2)`; `maybe_crash()` for testing |
| `agent/tracker.py` | The one side effect, made idempotent by the database |
| `agent/graph.py` | Wires the steps: validate → research → match → draft → save |
| `agent/run.py` | Terminal runner: Postgres checkpoints, `durability="sync"`, resume, history |
| `core/config.py` | Settings from environment variables or `.env` |
| `core/db.py`, `db/schema.sql` | Tables: users, runs, run_events, tracker |
| `api/main.py` | FastAPI: `/healthz`, `/readyz`, `POST /runs`, `GET /runs/{id}` |
| `worker/` | Skeletons you'll fill in on Days 4 and 5 |
| `tests/` | Unit tests (no database) and integration tests (skip without Postgres) |
| `practice/` | Learning exercises, kept separate from the app |

## What recovery does and doesn't guarantee

- A step that finished and was checkpointed is **not** re-run after a crash.
- The step that was running when it crashed **is** re-run, including any model calls it
  made. That costs tokens but is safe, because research, match and draft only read.
- The save step can run twice; the `UNIQUE (run_id)` constraint makes the second write a
  no-op. Every side effect must be designed this way.
- Checkpoints are written with `durability="sync"`. LangGraph's default, `"async"`, can lose
  the newest checkpoint if the process dies at the wrong moment.

This is at-least-once execution with idempotent side effects, not "exactly once".

## Troubleshooting

| Message | Fix |
| --- | --- |
| `Can't reach Postgres` | Open Docker Desktop, then `docker compose up -d` |
| `failed to connect to the docker API ... docker.sock` | Docker Desktop is closed: open it |
| `relation "tracker" does not exist` | Run `uv run python -m core.db` |
| api logs `error connecting in 'pool-1'` | Postgres is down; the api keeps retrying, `/readyz` returns 503 |
| Tests show `s` (skipped) | Normal when Postgres isn't running; start it to run them all |

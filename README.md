# AgentForge

A low-code agent platform, being built in public to production standards. Its first agent
is the **Job Hunt Agent**: add a CV and a job ad, and it researches the company, rates how
well the CV matches each requirement, and drafts tailored CV bullets and likely interview
questions.

The engineering goal is a system that keeps working when real people use it and things go
wrong: runs are queued, every step is checkpointed, crashes resume where they stopped, the
one side effect (saving to the user's tracker) can never happen twice, and the AI's answers
are measured by evals, not trusted.

## Status

| Part | State |
| --- | --- |
| 5-step LangGraph workflow: validate → research → match → draft → save | ✅ Works with real AI calls (Gemini via LangChain) |
| Research step as a **Deep Agent** (plans, searches the web, writes a sourced brief) | ✅ Search limit enforced in code; invented URLs dropped |
| Evals for match and draft (15 cases) and for research (4 cases) | ✅ Rule checks plus a self-checked LLM judge |
| Postgres checkpoints, crash and resume | ✅ Tested with a real process kill |
| Idempotent tracker write | ✅ Tested (`UNIQUE (run_id)`) |
| CV upload: PDF or Word to text | ✅ File safety limits; nothing stored |
| Web page: upload, check the text, run, see results | ✅ Runs stay "queued" until the worker loop exists |
| Worker: claim with lease, renew, finish | ✅ Race-safe; tested with two workers at once |
| Worker loop that runs the agent, Pub/Sub queue, sweeper | 🚧 In progress |
| Sign-in, retries, graceful shutdown, privacy basics | ⬜ Planned |
| Docker images, Kubernetes on GKE, load tests, real users | ⬜ Planned |

62 automated tests. Sign-in is still a placeholder (`dev-user`).

## Evidence: what the evals found

Tests check that the code is right. **Evals** check that the AI's answers are good: they run
the real model on fixed cases (clean, messy and adversarial inputs, including prompt
injection) and mark every answer. One change at a time, one commit per change, a saved
report for each run.

| Change | Score | What it fixed |
| --- | --- | --- |
| Baseline | 0/6 | Every CV bullet exaggerated ("critical bugs", "scalable APIs", made-up impact) |
| Draft prompt v2: banned describing words, made-up reasons and padding | 3/6 | Bullets became plain and true |
| Match prompt v2: a precise rule for "partial" | 5/6 | Stopped Kubernetes flipping to "partial" because the CV had Docker |
| Draft prompt v3: swap a word only if it means exactly the same | 6/6 | "automated tests" no longer becomes "unit tests" |
| Grown to 15 cases (5 clean, 5 messy, 5 adversarial) | **15/15 on two runs** | Gate: at least 12/15 |
| Research evals, baseline | 2/4 | For made-up companies it said "not found" but listed sources about similarly named companies |
| Research prompt v2: sources only about this exact company | **4/4** | |

Reports are in `evals/reports/`, each tagged with the git commit and model that produced it.

## Quick start

You need Docker Desktop (open), Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env             # then add your keys (see below)
uv sync                          # install the packages listed in uv.lock
docker compose up -d             # start Postgres (on port 5433)
uv run python -m core.db         # create the tables
uv run pytest                    # all tests should pass
```

Tests always run in fake mode: no AI calls, no keys, no cost.

### Use the real AI

In `.env`, set `FAKE_LLM=false`, plus `LLM_MODEL`, `GOOGLE_API_KEY` and `TAVILY_API_KEY`
(web search). Then:

```bash
uv run python -m agent.run            # one full run on the sample job ad and CV
uv run python -m evals.run            # match and draft evals
uv run python -m evals.research       # research evals
```

### The web page

```bash
uv run uvicorn api.main:app --reload
```

Open http://127.0.0.1:8000. The api docs are at http://127.0.0.1:8000/docs.

## Try crash recovery

```bash
uv run python -m agent.run --crash-at research   # dies mid-run, like kill -9 (exit code 137)
uv run python -m agent.run --history <run-id>    # see which steps were checkpointed
uv run python -m agent.run --resume <run-id>     # carries on at research, skips validate
```

Then try the hard case, `--crash-at after_save`: the tracker row is written but the process
dies before the checkpoint. On resume the save step runs again, and the tracker still has
exactly one row.

## How the code fits together

| File | What it does |
| --- | --- |
| `agent/graph.py` | Wires the steps: validate → research → match → draft → save |
| `agent/nodes.py` | The five steps, their prompts, and fake versions for tests |
| `agent/tools.py` | `search_web`, the research agent's tool (Tavily) |
| `agent/llm.py` | One shared model, with a rate limiter |
| `agent/schemas.py` | Pydantic models: what goes in and out, with size limits |
| `agent/state.py` | The state passed between steps |
| `agent/tracker.py` | The one side effect, made idempotent by the database |
| `agent/run.py` | Terminal runner: checkpoints, resume, history |
| `api/main.py` | FastAPI: the web page, `/parse`, `/runs`, health checks |
| `api/parse.py` | CV files to text, with safety limits |
| `web/index.html` | The web page (no build step) |
| `worker/` | Claims runs from the queue and runs the agent (in progress) |
| `evals/` | Eval cases, the LLM judge, and saved reports |
| `core/`, `db/schema.sql` | Settings and tables: users, runs, run_events, tracker |
| `tests/` | Unit tests, plus integration tests that skip without Postgres |
| `practice/` | Learning exercises, kept separate from the app |

## Design decisions

- **AI for judgement, code for rules.** The model rates each requirement; code turns the
  ratings into a score. The model writes bullets; code enforces the limits. The research
  agent may *ask* for many searches; code allows three.
- **An agent only where the work is open-ended.** Research loops, plans and uses tools.
  Match and draft are single, predictable model calls.
- **Measure, then change one thing.** Every prompt change is backed by an eval report, and
  eval examples are never copied into prompts (that would raise the score without
  improving the agent).
- **The api never runs the agent.** It saves the run and answers at once; workers do the
  slow work, so the api stays fast and a deploy can't kill a run halfway.
- **Untrusted input everywhere.** Job ads, CVs and web pages are treated as data, never
  instructions. Uploaded files are checked by their bytes, not their names. The page shows
  AI output as text, never as HTML.

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
| `Can't reach Postgres` | Open Docker Desktop, then `docker compose up -d`. Check `DATABASE_URL` uses port 5433 |
| `failed to connect to the docker API ... docker.sock` | Docker Desktop is closed: open it |
| `relation "tracker" does not exist` | Run `uv run python -m core.db` |
| `429 RESOURCE_EXHAUSTED` from Gemini | Too many requests a minute: set `LLM_REQUESTS_PER_MINUTE` in `.env` |
| api logs `error connecting in 'pool-1'` | Postgres is down; the api keeps retrying, `/readyz` returns 503 |
| Tests show `s` (skipped) | Normal when Postgres isn't running; start it to run them all |

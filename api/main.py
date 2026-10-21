"""The AgentForge API: the front counter that takes orders and hands out ticket numbers.

Start it (with Postgres running and `uv run python -m core.db` done):

    uv run uvicorn api.main:app --reload

Then open http://127.0.0.1:8000/docs to try every endpoint in the browser.

The api NEVER runs the agent itself. It saves the run as 'queued' and answers at once;
a worker picks the run up later (Day 4). That's why POST /runs returns 202 Accepted
("I've got it, it isn't done yet") rather than 200 OK.
"""

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from agent.schemas import JobHuntInput
from core.config import get_settings

RunStatus = Literal["queued", "running", "succeeded", "failed"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once at start-up (before yield) and once at shut-down (after yield).

    A connection pool keeps a few database connections open and lends them out, so each
    request doesn't pay to open a new one. open(wait=False) lets the api start even if
    Postgres is down; /readyz then reports "not ready" until it comes back.
    """
    app.state.pool = ConnectionPool(
        get_settings().database_url,
        min_size=1,
        max_size=5,
        open=False,
        kwargs={"autocommit": True, "row_factory": dict_row},
    )
    app.state.pool.open(wait=False)
    yield
    app.state.pool.close()


app = FastAPI(title="AgentForge API", version="0.1.0", lifespan=lifespan)


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


def get_current_user() -> str:
    """Who is calling.

    TODO(Day 3): verify the Google ID token from the Authorization header and return the
    account's ID. Until then everyone is 'dev-user'. Because every endpoint gets the user
    through this one function, swapping in real sign-in is a one-place change.
    """
    return "dev-user"


# --- Response shapes (FastAPI also uses these to build the /docs page) ---


class RunCreated(BaseModel):
    run_id: UUID
    status: RunStatus


class RunView(BaseModel):
    run_id: UUID
    status: RunStatus
    attempt: int
    error: str | None
    result: dict | None
    created_at: datetime
    updated_at: datetime


# --- Health checks, used by Kubernetes on Day 6 ---


@app.get("/healthz")
def healthz() -> dict:
    """Liveness: "is the process alive?" Checks nothing else, on purpose.

    If this checked the database, a database blip would make Kubernetes restart every
    api pod, which fixes nothing and makes the outage worse.
    """
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response, pool: ConnectionPool = Depends(get_pool)) -> dict:
    """Readiness: "can I serve requests right now?" Needs the database.

    503 tells Kubernetes (and the load balancer) to stop sending traffic here for now,
    without restarting the pod.
    """
    try:
        with pool.connection(timeout=2) as conn:
            conn.execute("SELECT 1")
    except Exception:
        response.status_code = 503
        return {"status": "not ready", "reason": "database unreachable"}
    return {"status": "ready"}


# --- Runs ---


@app.post("/runs", status_code=202)
def create_run(
    body: JobHuntInput,  # FastAPI checks the request body against this model -> 422 if bad
    pool: ConnectionPool = Depends(get_pool),
    user_id: str = Depends(get_current_user),
) -> RunCreated:
    """Save a new run as 'queued' and return its ID straight away."""
    run_id = uuid4()

    # One transaction: all three inserts happen, or none of them do.
    with pool.connection() as conn, conn.transaction():
        conn.execute(
            "INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (user_id,)
        )
        conn.execute(
            "INSERT INTO runs (id, user_id, input) VALUES (%s, %s, %s)",
            (run_id, user_id, Jsonb(body.model_dump())),
        )
        conn.execute("INSERT INTO run_events (run_id, kind) VALUES (%s, 'queued')", (run_id,))

    # TODO(Day 4): publish {"run_id": ...} to Pub/Sub HERE, after the commit above.
    # If the publish fails, the run stays 'queued' and the sweeper re-publishes it.

    return RunCreated(run_id=run_id, status="queued")


@app.get("/runs/{run_id}")
def get_run(
    run_id: UUID,  # a badly formed ID gets a 422 before this code even runs
    pool: ConnectionPool = Depends(get_pool),
    user_id: str = Depends(get_current_user),
) -> RunView:
    """One run's status and result, for its owner only."""
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT id AS run_id, status, attempt, error, result, created_at, updated_at
            FROM runs
            WHERE id = %s AND user_id = %s
            """,
            (run_id, user_id),
        ).fetchone()

    # Someone else's run gets the same 404 as a run that doesn't exist, so the api never
    # reveals which run IDs are real. "AND user_id = %s" is the tenant isolation.
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunView(**row)


# TODO(Day 3): GET /runs/{run_id}/events, streaming run_events as Server-Sent Events.

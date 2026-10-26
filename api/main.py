"""The AgentForge API: the front counter that takes orders and hands out ticket numbers.

Start it (with Postgres running and `uv run python -m core.db` done):

    uv run uvicorn api.main:app --reload

Then open http://127.0.0.1:8000/docs to try every endpoint in the browser.

The api NEVER runs the agent itself. It saves the run as 'queued' and answers at once;
a worker picks the run up later (Day 4). That's why POST /runs returns 202 Accepted
("I've got it, it isn't done yet") rather than 200 OK.
"""

import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import FileResponse
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from pydantic import BaseModel

from agent.privacy import redact_contact_details
from agent.schemas import MAX_CV_CHARS, JobHuntInput
from api.auth import user_id_from_token
from api.parse import MAX_BYTES, ParseError, extract_text
from core.config import get_settings
from core.queue import publish_run
from core.queue import setup as queue_setup

logger = logging.getLogger("api")

RunStatus = Literal["queued", "running", "succeeded", "failed"]
WEB_PAGE = Path(__file__).resolve().parents[1] / "web" / "index.html"


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
    if get_settings().pubsub_emulator_host:
        try:
            queue_setup()  # the emulator forgets its topic on restart; recreate it
        except Exception:
            logger.warning("Pub/Sub emulator not reachable; new runs won't be published")
    yield
    app.state.pool.close()


app = FastAPI(title="AgentForge API", version="0.1.0", lifespan=lifespan)


def get_pool(request: Request) -> ConnectionPool:
    return request.app.state.pool


def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """Who is calling: the Google account ID from the sign-in token (see api/auth.py).

    Every endpoint that touches a user's data gets the user through this one function.
    No valid token means 401, so the request never reaches any data.
    """
    client_id = get_settings().google_client_id
    if not client_id:
        # Fail CLOSED: if sign-in isn't set up, nobody gets in. Never fall back to a
        # shared "dev-user", or every visitor would see every CV.
        raise HTTPException(status_code=503, detail="Sign-in isn't set up on this server.")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Please sign in.",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        return user_id_from_token(authorization.removeprefix("Bearer "), client_id)
    except ValueError as error:  # forged, expired, or issued for another website
        raise HTTPException(status_code=401, detail="Your sign-in has expired. Please sign in again.",
                            headers={"WWW-Authenticate": "Bearer"}) from error


# --- Response shapes (FastAPI also uses these to build the /docs page) ---


class RunCreated(BaseModel):
    run_id: UUID
    status: RunStatus


class ParsedCV(BaseModel):
    text: str
    characters: int
    too_long: bool  # over the limit for a run: the user must shorten it before running


class RunView(BaseModel):
    run_id: UUID
    status: RunStatus
    attempt: int
    error: str | None
    result: dict | None
    created_at: datetime
    updated_at: datetime


# --- The web page ---


@app.get("/", include_in_schema=False)  # include_in_schema=False: keep it off the /docs page
def home(request: Request) -> FileResponse:
    """The whole UI is one HTML page. Its JavaScript calls /parse and /runs, like any client."""
    # Google's sign-in button needs to see which site it's on. Over plain http (your
    # laptop) Google asks for this looser setting; over https, the recommended stricter one.
    policy = ("no-referrer-when-downgrade" if request.url.scheme == "http"
              else "strict-origin-when-cross-origin")
    return FileResponse(WEB_PAGE, headers={"Referrer-Policy": policy})


@app.get("/config")
def config() -> dict:
    """Public settings the page needs. A Google client ID isn't a secret: every site
    with a Google sign-in button shows its client ID in the page."""
    return {"google_client_id": get_settings().google_client_id}


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


# --- CV upload ---


@app.post("/parse", dependencies=[Depends(get_current_user)])
def parse_cv(file: UploadFile) -> ParsedCV:
    """Turn an uploaded PDF or Word CV into text, for the user to check before a run.

    Nothing is stored: the file is read, turned into text and thrown away.

    This is a plain `def`, not `async def`, on purpose. Reading a PDF keeps the CPU busy;
    FastAPI runs plain functions in a separate thread, so one big PDF can't freeze the
    api for everyone else. Signed-in users only, so strangers can't use it for free.
    """
    data = file.file.read(MAX_BYTES + 1)  # read at most 1 byte past the limit, never more
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="The file is too big. The limit is 2 MB.")
    try:
        # Contact details are removed here too, so the text the user checks is exactly
        # what will be saved and what the AI will read.
        text = redact_contact_details(extract_text(data))
    except ParseError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return ParsedCV(text=text, characters=len(text), too_long=len(text) > MAX_CV_CHARS)


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
        # The per-user limit. Two requests at the very same moment could both slip under
        # it; that's fine for a cost guard (it's a speed bump, not a bank balance).
        limit = get_settings().runs_per_hour_per_user
        recent = conn.execute(
            "SELECT count(*) AS n FROM runs "
            "WHERE user_id = %s AND created_at > now() - interval '1 hour'",
            (user_id,),
        ).fetchone()
        if recent and recent["n"] >= limit:
            raise HTTPException(
                status_code=429,
                detail=f"You've started {limit} runs in the last hour. Please try again later.",
            )
        conn.execute(
            "INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (user_id,)
        )
        conn.execute(
            "INSERT INTO runs (id, user_id, input) VALUES (%s, %s, %s)",
            # Contact details are removed BEFORE saving: never stored, never sent to the AI.
            (run_id, user_id, Jsonb({"job_ad": redact_contact_details(body.job_ad),
                                     "cv": redact_contact_details(body.cv)})),
        )
        conn.execute("INSERT INTO run_events (run_id, kind) VALUES (%s, 'queued')", (run_id,))

    # Tell the workers, AFTER the commit: a worker must never get a run ID that isn't
    # saved yet. If the publish fails, the run is still safe in Postgres as 'queued', and
    # the sweeper re-publishes it. So the user still gets their 202, never an error.
    try:
        publish_run(run_id)
    except Exception:
        logger.exception("run %s saved but not published; the sweeper will retry", run_id)

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
    return RunView.model_validate(row)  # check the database row against RunView


@app.delete("/runs/{run_id}", status_code=204)
def delete_run(
    run_id: UUID,
    pool: ConnectionPool = Depends(get_pool),
    user_id: str = Depends(get_current_user),
) -> Response:
    """Delete a run and everything saved for it: the CV and job ad, the result, its progress
    events, its tracker row and its LangGraph checkpoints. Owner only."""
    with pool.connection() as conn, conn.transaction():
        deleted = conn.execute(
            "DELETE FROM runs WHERE id = %s AND user_id = %s RETURNING id", (run_id, user_id)
        ).fetchone()  # run_events go too: ON DELETE CASCADE in db/schema.sql
        if deleted is None:
            raise HTTPException(status_code=404, detail="Run not found")
        conn.execute("DELETE FROM tracker WHERE run_id = %s", (run_id,))
        PostgresSaver(conn).delete_thread(str(run_id))  # the checkpoints hold the CV too
    return Response(status_code=204)

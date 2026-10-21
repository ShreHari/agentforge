"""The state: the shared notebook passed from step to step.

Each step (node) reads the state and returns ONLY the keys it changed. LangGraph merges
those changes in and saves a checkpoint (a copy of the whole state) after every step.
"""

import operator
from typing import Annotated, TypedDict


class JobHuntState(TypedDict, total=False):
    # total=False means no key is required up front: the run starts with only the
    # inputs below, and later steps fill in the rest.

    # --- Inputs, set when the run starts ---
    run_id: str  # also the LangGraph thread_id, so a run and its checkpoints share an ID
    user_id: str
    job_ad: str
    cv: str

    # --- Filled in by the steps ---
    company: str  # validate
    brief: dict  # research  (a CompanyBrief, stored as a plain dict)
    match: dict  # match     (a MatchReport)
    draft: dict  # draft     (a Draft)
    tracker_row_created: bool  # save: True the first time, False if the row already existed

    # --- A list that GROWS instead of being replaced ---
    # Annotated[..., operator.add] is a "reducer". Normally a new value replaces the old one.
    # With operator.add, LangGraph does old_list + new_list, so each step can add
    # ["research"] and the list keeps every step's name, in order.
    steps_done: Annotated[list[str], operator.add]


# Why plain dicts instead of Pydantic models in the state?
# Checkpoints are saved to Postgres and loaded back, possibly by a different worker
# running newer code. Plain dicts of text and numbers always load back cleanly.

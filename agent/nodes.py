"""The five steps (nodes) of the Job Hunt Agent: validate -> research -> match -> draft -> save.

Every node is an ordinary function: it reads the state and returns ONLY the keys it changed.

FAKE MODE: research, match and draft use simple fake logic for now (no AI, no API keys,
no cost), so you can learn the graph, checkpoints and crash recovery first. Day 2 swaps
them for real model calls. Search this file for "TODO(Day 2)".
"""

import os
import re

from agent.schemas import CompanyBrief, Draft, JobHuntInput, MatchReport, RequirementMatch
from agent.state import JobHuntState
from agent.tracker import Tracker
from core.config import get_settings


# --------------------------------------------------------------------------------------
# Helpers for testing crash recovery
# --------------------------------------------------------------------------------------

CRASH_POINTS = ("validate", "research", "match", "draft", "save", "after_save")


def maybe_crash(point: str) -> None:
    """Dev only: kill the process instantly when CRASH_AT=<point>.

    os._exit skips all clean-up, exactly like `kill -9` or a pod being killed for using
    too much memory. Exit code 137 is what Linux reports for that (128 + signal 9).
    """
    if os.environ.get("CRASH_AT") == point:
        print(f"  !! simulated crash at '{point}' (like kill -9)", flush=True)
        os._exit(137)


def require_fake_mode(step: str, real_version: str) -> None:
    """Stop with a clear message if someone turns off fake mode before Day 2 is built."""
    if not get_settings().fake_llm:
        raise NotImplementedError(
            f"TODO(Day 2): the real '{step}' step ({real_version}) isn't built yet. "
            "Set FAKE_LLM=true until it is."
        )


# --------------------------------------------------------------------------------------
# Step 1: validate
# --------------------------------------------------------------------------------------


def validate(state: JobHuntState) -> dict:
    """Check the inputs and find the company name."""
    maybe_crash("validate")

    # Pydantic checks the lengths and strips whitespace. Bad input raises a
    # ValidationError here, so the run stops at the first step and costs nothing.
    data = JobHuntInput(job_ad=state.get("job_ad", ""), cv=state.get("cv", ""))

    return {
        "job_ad": data.job_ad,
        "cv": data.cv,
        "company": find_company(data.job_ad),
        "steps_done": ["validate"],
    }


def find_company(job_ad: str) -> str:
    """Look for a 'Company: ...' line; otherwise use the first line of the ad.

    TODO(Day 2): ask the model to extract the company name instead.
    """
    match = re.search(r"^\s*company\s*:\s*(.+)$", job_ad, re.IGNORECASE | re.MULTILINE)
    if match:
        return match.group(1).strip()
    return job_ad.splitlines()[0].strip()[:80]


# --------------------------------------------------------------------------------------
# Step 2: research
# --------------------------------------------------------------------------------------


def research(state: JobHuntState) -> dict:
    """Find out about the company.

    TODO(Day 2): replace with a Deep Agent that plans, searches the web, reads pages
    through the safe-fetch tool and writes a sourced brief.
    """
    maybe_crash("research")
    require_fake_mode("research", "a Deep Agent with web search")

    company = state["company"]
    brief = CompanyBrief(
        company=company,
        summary=f"[FAKE] Placeholder brief for {company}. No web search was done.",
        sources=[],
    )
    return {"brief": brief.model_dump(), "steps_done": ["research"]}


# --------------------------------------------------------------------------------------
# Step 3: match
# --------------------------------------------------------------------------------------

# Words too common to say anything about a skill.
STOPWORDS = {
    "a", "an", "and", "or", "the", "of", "in", "on", "to", "for", "with", "as", "such",
    "using", "use", "experience", "knowledge", "basic", "strong", "good", "ability",
    "skills", "skill", "working", "work", "is", "are", "be", "your", "you", "we", "our",
}


def find_requirements(job_ad: str) -> list[str]:
    """Every bullet line in the ad (starting with -, * or •), at most 15."""
    lines = re.findall(r"^\s*[-*•]\s+(.+)$", job_ad, re.MULTILINE)
    return [line.strip() for line in lines][:15]


def keywords(text: str) -> set[str]:
    """Lower-case words of 2+ letters, minus stopwords. Keeps tech names like c++ and node.js."""
    words = re.findall(r"[a-z][a-z0-9+#.]+", text.lower())
    return {word.strip(".") for word in words} - STOPWORDS


def match(state: JobHuntState) -> dict:
    """Score how well the CV covers each requirement, by shared keywords.

    TODO(Day 2): replace with a model call using structured output (MatchReport), which
    understands that "built REST services" covers "API development".
    """
    maybe_crash("match")
    require_fake_mode("match", "a model call with structured output")

    # Each non-empty CV line, without its bullet mark ("- Built APIs" -> "Built APIs").
    cv_lines = [line.strip().lstrip("-*• ") for line in state["cv"].splitlines() if line.strip()]
    results: list[RequirementMatch] = []

    for requirement in find_requirements(state["job_ad"]):
        wanted = keywords(requirement)
        if not wanted:
            continue
        # The CV line sharing the most keywords with this requirement.
        best_line = max(cv_lines, key=lambda line: len(wanted & keywords(line)), default="")
        overlap = len(wanted & keywords(best_line)) / len(wanted)

        if overlap >= 0.5:
            strength = "strong"
        elif overlap > 0:
            strength = "partial"
        else:
            strength = "missing"
        results.append(
            RequirementMatch(
                requirement=requirement,
                strength=strength,
                evidence=best_line if overlap > 0 else None,
            )
        )

    points = sum(1.0 if m.strength == "strong" else 0.5 if m.strength == "partial" else 0.0
                 for m in results)
    score = round(100 * points / len(results)) if results else 0

    report = MatchReport(matches=results, score=score)
    return {"match": report.model_dump(), "steps_done": ["match"]}


# --------------------------------------------------------------------------------------
# Step 4: draft
# --------------------------------------------------------------------------------------


def draft(state: JobHuntState) -> dict:
    """Write tailored CV bullets and likely interview questions.

    TODO(Day 2): replace with a model call using structured output (Draft).
    """
    maybe_crash("draft")
    require_fake_mode("draft", "a model call with structured output")

    matches = state["match"]["matches"]
    covered = [m for m in matches if m["strength"] != "missing"]
    gaps = [m for m in matches if m["strength"] == "missing"]

    bullets = [f"[FAKE] Show impact for '{m['requirement']}': {m['evidence']}" for m in covered]
    questions = [f"[FAKE] Tell me about a time you used {m['requirement']}." for m in covered]
    questions += [f"[FAKE] How would you get up to speed on {m['requirement']}?" for m in gaps]

    result = Draft(cv_bullets=bullets[:5], interview_questions=questions[:10])
    return {"draft": result.model_dump(), "steps_done": ["draft"]}


# --------------------------------------------------------------------------------------
# Step 5: save
# --------------------------------------------------------------------------------------


def make_save(tracker: Tracker):
    """Build the save step around a tracker.

    The graph gets the Postgres tracker in real runs and an in-memory one in tests. This
    is "dependency injection": the step is handed what it needs instead of creating it.
    """

    def save(state: JobHuntState) -> dict:
        maybe_crash("save")  # crash BEFORE the write: nothing saved yet
        created = tracker.save(
            run_id=state["run_id"],
            user_id=state["user_id"],
            company=state["company"],
            match_score=state["match"]["score"],
        )
        maybe_crash("after_save")  # crash AFTER the write but BEFORE the checkpoint
        return {"tracker_row_created": created, "steps_done": ["save"]}

    return save

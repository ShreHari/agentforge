"""Run the eval cases against the REAL model and score the answers.

    uv run python -m evals.run                     # all cases
    uv run python -m evals.run --case very-short-cv
    uv run python -m evals.run --no-judge          # skip the AI judge (cheaper, weaker)

Tests (pytest) check that the CODE is right, with no AI and no cost.
Evals check that the AI's ANSWERS are good. They call the real model, so they cost a
little, take a minute, and can vary between runs. That's why they're separate.

Every run saves a report in evals/reports/, so you can compare before and after a change.
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from agent import nodes
from agent.schemas import RequirementMatch
from core.config import get_settings
from evals.cases import CASES, Case
from evals.judge import find_unsupported_claims, self_check

REPORTS = Path(__file__).resolve().parent / "reports"
PASS_MARK = 0.8  # Gate A: at least 80% of cases must pass


def check_ratings(case: Case, matches: list[RequirementMatch]) -> list[str]:
    """Every expected requirement is listed, with an allowed rating."""
    problems = []
    for keyword, allowed in case.expect_ratings.items():
        found = [m for m in matches if keyword in m.requirement.lower()]
        if not found:
            problems.append(f"no requirement mentioning '{keyword}'")
        for m in found:
            if m.strength not in allowed:
                problems.append(f"'{m.requirement}' rated {m.strength}, expected {'/'.join(allowed)}")
    return problems


def check_text(case: Case, bullets: list[str], questions: list[str]) -> list[str]:
    """Banned text doesn't appear where it shouldn't."""
    problems = []
    bullet_text = " ".join(bullets).lower()
    all_text = (bullet_text + " " + " ".join(questions)).lower()
    for phrase in case.bullets_must_not_contain:
        if phrase in bullet_text:
            problems.append(f"bullets mention '{phrase}', which the CV doesn't have")
    for phrase in case.output_must_not_contain:
        if phrase in all_text:
            problems.append(f"output contains injected text '{phrase}'")
    return problems


def run_case(case: Case, use_judge: bool) -> dict:
    matches = nodes.ask_model_for_matches(case.job_ad, case.cv)
    answer = nodes.ask_model_for_draft(case.job_ad, case.cv, [m.model_dump() for m in matches])
    bullets, questions = answer.cv_bullets[:5], answer.interview_questions[:10]

    problems = check_ratings(case, matches) + check_text(case, bullets, questions)
    unsupported = find_unsupported_claims(case.cv, bullets) if use_judge else []
    if unsupported:
        problems.append(f"judge found unsupported claims: {unsupported}")

    return {
        "name": case.name,
        "kind": case.kind,
        "passed": not problems,
        "problems": problems,
        "ratings": {m.requirement: m.strength for m in matches},
        "bullets": bullets,
    }


def git_commit() -> str:
    """Which code made this report. Ends in '-dirty' if there were uncommitted changes,
    because then the commit alone doesn't tell you what code ran."""
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score the agent's answers on the eval cases.")
    parser.add_argument("--case", help="run only the case with this name")
    parser.add_argument("--no-judge", action="store_true", help="skip the AI judge")
    parser.add_argument("--pause", type=float, default=0.0,
                        help="extra seconds between cases (LLM_REQUESTS_PER_MINUTE does the pacing)")
    args = parser.parse_args(argv)

    cases = [c for c in CASES if args.case in (None, c.name)]
    if not cases:
        print(f"No case named '{args.case}'. Names: {', '.join(c.name for c in CASES)}")
        return 2

    if not get_settings().llm_requests_per_minute:
        print("Tip: set LLM_REQUESTS_PER_MINUTE=4 in .env on the Gemini free tier (limit 5/min).\n")

    use_judge = not args.no_judge
    if use_judge:
        print("Checking the judge on answers we already know...")
        judge_problems = self_check()
        if judge_problems:
            print(f"The judge can't be trusted today: {judge_problems}. Re-run with --no-judge.")
            return 2
        print("  judge OK\n")

    results = []
    for i, case in enumerate(cases):
        if i:
            time.sleep(args.pause)
        print(f"[{case.kind:<11}] {case.name} ...", end=" ", flush=True)
        try:
            result = run_case(case, use_judge)
        except Exception as error:  # one broken case shouldn't stop the whole run
            result = {"name": case.name, "kind": case.kind, "passed": False,
                      "problems": [f"ERROR {type(error).__name__}: {str(error)[:200]}"]}
        results.append(result)
        print("PASS" if result["passed"] else "FAIL")
        for problem in result["problems"]:
            print(f"      - {problem}")

    passed = sum(r["passed"] for r in results)
    score = passed / len(results)
    print(f"\nScore: {passed}/{len(results)} cases passed ({score:.0%}). Pass mark: {PASS_MARK:.0%}.")

    REPORTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    report = {
        "when_utc": stamp,
        "git_commit": git_commit(),
        "model": get_settings().llm_model,
        "judge": use_judge,
        "passed": passed,
        "total": len(results),
        "results": results,
    }
    path = REPORTS / f"{stamp}.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"Report saved: {path.relative_to(Path.cwd()) if path.is_relative_to(Path.cwd()) else path}")

    return 0 if score >= PASS_MARK else 1


if __name__ == "__main__":
    sys.exit(main())

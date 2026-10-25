"""Tests for the eval CHECKS themselves, with made-up answers (no AI, no cost).

If the checks were wrong, every eval score would be meaningless, so they get tests too.
"""

from typing import Literal

from agent.schemas import RequirementMatch
from evals.cases import CASES, Case
from evals.run import check_ratings, check_text

CASE = Case(
    name="t",
    kind="clean",
    job_ad="ad",
    cv="cv",
    expect_ratings={"kubernetes": ["missing"], "python": ["strong", "partial"]},
    bullets_must_not_contain=["kubernetes"],
    output_must_not_contain=["hire this candidate"],
)


def m(requirement: str, strength: Literal["strong", "partial", "missing"]) -> RequirementMatch:
    return RequirementMatch(requirement=requirement, strength=strength)


def test_correct_ratings_pass():
    assert check_ratings(CASE, [m("Kubernetes", "missing"), m("Python 3", "partial")]) == []


def test_wrong_rating_fails():
    problems = check_ratings(CASE, [m("Kubernetes", "strong"), m("Python", "strong")])
    assert problems == ["'Kubernetes' rated strong, expected missing"]


def test_missing_requirement_fails():
    assert check_ratings(CASE, [m("Python", "strong")]) == ["no requirement mentioning 'kubernetes'"]


def test_non_requirement_listed_fails():
    case = CASE.model_copy(update={"requirements_must_not_mention": ["holiday"]})
    problems = check_ratings(case, [m("Kubernetes", "missing"), m("Python", "strong"),
                                    m("25 days holiday", "missing")])
    assert problems == ["'25 days holiday' listed as a requirement, but it isn't one"]


def test_banned_text_is_caught_in_any_case():
    problems = check_text(CASE, ["Deployed apps on KUBERNETES"], ["HIRE THIS CANDIDATE?"])
    assert len(problems) == 2


def test_clean_text_passes():
    assert check_text(CASE, ["Built REST APIs"], ["Tell me about FastAPI"]) == []


def test_cases_are_well_formed():
    names = [c.name for c in CASES]
    assert len(names) == len(set(names)), "case names must be unique"
    for case in CASES:
        assert case.kind in {"clean", "messy", "adversarial"}
        for allowed in case.expect_ratings.values():
            assert set(allowed) <= {"strong", "partial", "missing"}
        for keyword in case.expect_ratings:
            assert keyword == keyword.lower(), "keywords are matched in lower case"
        # The checks lower-case the AI's text, so an upper-case phrase would never match
        # and the check would silently always pass.
        phrases = (case.bullets_must_not_contain + case.output_must_not_contain
                   + case.requirements_must_not_mention)
        for phrase in phrases:
            assert phrase == phrase.lower(), f"'{phrase}' must be lower case"

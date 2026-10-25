"""Tests for the eval CHECKS themselves, with made-up answers (no AI, no cost).

If the checks were wrong, every eval score would be meaningless, so they get tests too.
"""

from typing import Literal

from agent.schemas import CompanyBrief, RequirementMatch
from evals.cases import CASES, Case
from evals.research import RESEARCH_CASES, ResearchCase, ResearchVerdict, check_research
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


# --- Research eval checks (no AI: the briefs and verdicts are made up) ---

MADE_UP = ResearchCase(name="m", company="Nowhere Ltd", exists=False)
REAL = ResearchCase(name="r", company="Monzo", exists=True, summary_mentions_any=["bank"])


def verdict(not_found: bool, off_topic: list[str] | None = None) -> ResearchVerdict:
    return ResearchVerdict(says_not_found=not_found, off_topic_sources=off_topic or [])


def test_made_up_company_passes_when_not_found_with_no_sources():
    brief = CompanyBrief(company="Nowhere Ltd", summary="Not found.", sources=[])
    assert check_research(MADE_UP, brief, verdict(True)) == []


def test_made_up_company_fails_when_sources_are_listed():
    brief = CompanyBrief(company="Nowhere Ltd", summary="Not found.",
                         sources=["https://other.example"])
    assert check_research(MADE_UP, brief, verdict(True)) == [
        "made-up company, but 1 sources were listed"
    ]


def test_real_company_passes_with_on_topic_sources():
    brief = CompanyBrief(company="Monzo", summary="Monzo is a UK bank.",
                         sources=["https://monzo.com/about"])
    assert check_research(REAL, brief, verdict(False)) == []


def test_real_company_fails_on_off_topic_source_and_missing_word():
    brief = CompanyBrief(company="Monzo", summary="Monzo has an app.",
                         sources=["https://monzo-cafe.example"])
    problems = check_research(REAL, brief, verdict(False, ["https://monzo-cafe.example"]))
    assert problems == [
        "summary mentions none of ['bank']",
        "sources about a different company: ['https://monzo-cafe.example']",
    ]


def test_research_cases_are_well_formed():
    names = [c.name for c in RESEARCH_CASES]
    assert len(names) == len(set(names))
    for case in RESEARCH_CASES:
        assert all(w == w.lower() for w in case.summary_mentions_any)
        if not case.exists:
            assert not case.summary_mentions_any, "made-up companies have no facts to mention"

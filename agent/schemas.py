"""The shape of the data going into and out of the Job Hunt Agent.

These are Pydantic models. Unlike a TypedDict, Pydantic CHECKS the data while the program
runs: if a CV is too long or a field is missing, it raises a ValidationError. That's why
anything a user sends us goes through a model here first.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Size limits protect cost (every character goes to the LLM) and stop abuse.
MAX_JOB_AD_CHARS = 20_000
MAX_CV_CHARS = 15_000


class JobHuntInput(BaseModel):
    """What the user submits."""

    # Remove spaces and blank lines at the start and end of every text field.
    model_config = ConfigDict(str_strip_whitespace=True)

    job_ad: str = Field(min_length=50, max_length=MAX_JOB_AD_CHARS)
    cv: str = Field(min_length=100, max_length=MAX_CV_CHARS)


class CompanyBrief(BaseModel):
    """Output of the research step."""

    company: str
    summary: str
    sources: list[str] = Field(default_factory=list)  # URLs the research used


class RequirementMatch(BaseModel):
    """How well the CV covers one requirement from the job ad."""

    requirement: str
    strength: Literal["strong", "partial", "missing"]
    evidence: str | None = None  # the CV line that supports it, if any


class RequirementList(BaseModel):
    """How well a CV covers each requirement in a job ad."""

    matches: list[RequirementMatch]


class MatchReport(BaseModel):
    """Output of the match step."""

    matches: list[RequirementMatch]
    # 0 = no overlap, 100 = every requirement strong
    score: int = Field(ge=0, le=100)


class Draft(BaseModel):
    """Output of the draft step."""

    cv_bullets: list[str] = Field(max_length=5)
    interview_questions: list[str] = Field(max_length=10)


class DraftAnswer(BaseModel):
    """Tailored CV bullets and likely interview questions for one job."""

    cv_bullets: list[str] = Field(
        description="Up to 5 CV bullet points, each restating real experience from the CV to fit the job; fewer if the CV is short"
    )
    interview_questions: list[str] = Field(
        description="10 interview questions this candidate is likely to be asked for this job"
    )

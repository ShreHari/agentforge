"""Input validation: bad input must be rejected before it costs anything."""

import pytest
from pydantic import ValidationError

from agent.schemas import MAX_CV_CHARS, JobHuntInput


def test_sample_input_is_valid(job_ad, cv):
    data = JobHuntInput(job_ad=job_ad, cv=cv)
    assert data.job_ad.startswith("Company:")


def test_whitespace_is_stripped(job_ad, cv):
    data = JobHuntInput(job_ad=f"\n\n  {job_ad}  \n", cv=cv)
    assert data.job_ad == job_ad.strip()


def test_cv_too_long_is_rejected(job_ad):
    with pytest.raises(ValidationError) as error:
        JobHuntInput(job_ad=job_ad, cv="x" * (MAX_CV_CHARS + 1))
    assert error.value.errors()[0]["loc"] == ("cv",)


def test_job_ad_too_short_is_rejected(cv):
    with pytest.raises(ValidationError):
        JobHuntInput(job_ad="Hiring now", cv=cv)


def test_missing_field_is_rejected(job_ad):
    with pytest.raises(ValidationError):
        JobHuntInput(job_ad=job_ad)  # pyright: ignore[reportCallIssue]  (missing cv on purpose)

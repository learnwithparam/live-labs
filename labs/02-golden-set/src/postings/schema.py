"""The fields the job board's filters read from a posting. One shape for the model, the golden set and the score."""

from typing import Literal

from pydantic import BaseModel

# The seven filters a job seeker clicks. Each one is scored on its own, so a broken filter cannot hide in a total.
FIELDS = ["seniority", "workplace", "employment", "salary_min", "salary_max", "currency", "period"]


class Posting(BaseModel):
    seniority: Literal["intern", "junior", "mid", "senior", "staff", "principal"]
    workplace: Literal["onsite", "hybrid", "remote"]
    employment: Literal["full_time", "part_time", "contract", "internship"]
    salary_min: float | None
    salary_max: float | None
    currency: str | None
    period: Literal["hour", "month", "year"] | None


def same(field: str, got: object, approved: object) -> bool:
    """A number is right to the unit; a code is right whatever its case."""
    # 3500000.0 and 3500000 are the same salary, so numbers are compared, not their text.
    if isinstance(approved, (int, float)) and isinstance(got, (int, float)):
        return abs(got - approved) < 0.5
    # "inr" and "INR" are the same currency, so case never makes an answer wrong.
    if isinstance(approved, str) and isinstance(got, str):
        return got.strip().upper() == approved.upper()
    return got == approved

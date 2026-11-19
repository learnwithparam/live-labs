"""The lab's promise, with numbers: the prose draft is refused for what it never measured, the RFC is
approved with zero unmeasured failure modes inside the signed budget, and neither the benchmark nor
the budget changed since the person signed them. No model is involved: the truth is the signed files."""

import pytest

from brickhaven_rfc.config import MIN_CASES_PER_FAILURE_MODE, RFC
from brickhaven_rfc.review import review
from brickhaven_rfc.rfc import read_rfc


@pytest.mark.proof
def test_the_rfc_is_signed_off_with_zero_unmeasured_failure_modes():
    draft, structured = review(read_rfc(RFC / "draft.md")), review(read_rfc(RFC / "rfc.toml"))
    assert draft.changed == structured.changed == []  # the truth is what was signed, untouched
    # The naive draft: every risk is a worry with no number, and the times do not fit.
    assert len(draft.unmeasured) == 5 and draft.total_ms > draft.signed_ms and not draft.approved
    # The metric: zero unmeasured failure modes, each with its sample, inside the signed time.
    assert len(structured.unmeasured) == 0
    assert all(mode.cases >= MIN_CASES_PER_FAILURE_MODE for mode in structured.modes)
    assert structured.total_ms <= structured.signed_ms and not structured.plan_gaps and structured.approved

"""Invoice numbers: FH-2026-00042.

The tax office in two of Fernhill's markets requires an unbroken sequence per calendar year, so the
counter restarts at 1 every January.
"""

import re

PATTERN = re.compile(r"^FH-(\d{4})-(\d{5})$")


def next_invoice_number(previous: str | None, year: int) -> str:
    """The number after `previous`, for an invoice issued in `year`."""
    if previous is None:
        return f"FH-{year}-00001"
    match = PATTERN.match(previous)
    if not match:
        raise ValueError(f"not an invoice number: {previous!r}")
    counter = int(match.group(2)) + 1
    return f"FH-{year}-{counter:05d}"

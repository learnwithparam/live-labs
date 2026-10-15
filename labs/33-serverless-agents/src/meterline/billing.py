"""Meterline's billing exports: each customer's contract, September usage and the draft invoice.

In production these are an API call to the metering store and the billing system. Here they are files
in `data/`, so the job reads the same shapes with no network and every run starts from the same books.
"""

import json
from functools import cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[2] / "data"


class MissingExport(Exception):
    """The metering export has no row for this customer. Retrying may help if the export lands late."""


@cache
def customers() -> dict[str, dict]:
    listed = json.loads((DATA / "customers.json").read_text())["customers"]
    return {customer["id"]: customer for customer in listed}


@cache
def usage() -> dict[str, int]:
    return json.loads((DATA / "usage-2026-09.json").read_text())["api_calls"]


@cache
def drafts() -> dict[str, dict]:
    return json.loads((DATA / "invoices-2026-09.json").read_text())["drafts"]


@cache
def checked_totals() -> dict[str, float]:
    """Finance's hand-checked totals. The job never reads these; the proof grades the job against them."""
    return json.loads((DATA / "checked-totals-2026-09.json").read_text())["totals"]


def tonights_jobs() -> list[str]:
    """One job per enterprise customer with a September draft invoice, in customer order."""
    return sorted(drafts())


def fetch(customer_id: str) -> dict:
    """Everything one job needs. A missing usage row raises, so the step's retry budget decides what happens."""
    calls = usage().get(customer_id)
    if calls is None:
        raise MissingExport(f"No September usage export for {customer_id}; the metering store has no row for it.")
    customer, draft = customers()[customer_id], drafts()[customer_id]
    return {"customer_id": customer_id, "name": customer["name"], "contract": customer["contract"],
            "api_calls": calls, "invoice": draft["invoice"], "draft_total": draft["total"], "draft_lines": draft["lines"]}

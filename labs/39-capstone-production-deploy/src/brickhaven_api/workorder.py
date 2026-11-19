"""The task the service does: turn one tenant message into a work order, and refuse an answer that is not one.

The model is asked for JSON with fixed fields. Checking the fields here means a bad answer becomes a
clear error for the caller instead of a malformed order in a coordinator's queue.
"""

import json
from dataclasses import asdict, dataclass

TRADES = ("plumbing", "electrical", "heating", "locksmith", "pest", "general")
URGENCIES = ("emergency", "urgent", "routine")

SYSTEM_PROMPT = (
    "You turn a tenant maintenance message into one work order. Reply with JSON only, with these keys: "
    f"trade (one of {', '.join(TRADES)}), urgency (one of {', '.join(URGENCIES)}), "
    "unit (the flat named in the message, or null if none is named), "
    "safety_flag (true for gas, smoke, sparks, flooding or anyone locked in, else false), "
    "access_window (when the tenant is home, as written, or null)."
)


class WorkOrderError(ValueError):
    """The model answered, but not with a usable work order."""


@dataclass
class WorkOrder:
    trade: str
    urgency: str
    unit: str | None
    safety_flag: bool
    access_window: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def parse_work_order(text: str) -> WorkOrder:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise WorkOrderError("no JSON object in the answer")
    try:
        fields = json.loads(text[start:end + 1])
    except json.JSONDecodeError as error:
        raise WorkOrderError(f"the JSON did not parse: {error}") from error
    if fields.get("trade") not in TRADES or fields.get("urgency") not in URGENCIES:
        raise WorkOrderError("trade or urgency is not one of the allowed values")
    if not isinstance(fields.get("safety_flag"), bool):
        raise WorkOrderError("safety_flag must be true or false")
    return WorkOrder(fields["trade"], fields["urgency"], fields.get("unit"), fields["safety_flag"], fields.get("access_window"))

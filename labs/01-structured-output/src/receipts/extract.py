"""A receipt in (photo, PDF or text), a reconciled ledger entry out, with one repair attempt."""

import base64
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from receipts.schema import Receipt

SYSTEM = (
    "You turn a receipt into a ledger entry. It may be a phone photo, a PDF or the text of an email; "
    "a photo can be tilted or faded, so read each amount on its own row. Copy amounts exactly as printed. Use the receipt's own currency "
    "as an ISO 4217 code. Sum every tax line into `tax`. A coupon printed as its own line is a "
    "negative line item; do not count it again in `discount`. When the receipt says prices include "
    "tax (incl. VAT, enth. MwSt, TTC), set `tax_included` to true. Reply with JSON only."
)


IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
# What a person types the first time: no schema, no rule. `receipts naive` shows what it books.
NAIVE = "Extract this receipt as JSON."


def read_receipt(path: Path) -> str | list[dict]:
    """The user message content for one receipt: text as is, a photo or a PDF as a base64 data URL."""
    suffix = path.suffix.lower()
    if suffix == ".txt":
        return path.read_text()
    data = base64.b64encode(path.read_bytes()).decode()
    if suffix in IMAGE_TYPES:
        return [{"type": "image_url", "image_url": {"url": f"data:{IMAGE_TYPES[suffix]};base64,{data}"}}]
    if suffix == ".pdf":
        pdf = {"filename": path.name, "file_data": f"data:application/pdf;base64,{data}"}
        return [{"type": "file", "file": pdf}]
    raise ValueError(f"{path.name}: expected .txt, .jpg, .png or .pdf")


def naive(receipt: str | list[dict], client, model: str) -> str:
    """The obvious first try, with no schema and no rule. Returns the raw reply."""
    content = [{"type": "text", "text": NAIVE}] + (receipt if isinstance(receipt, list) else [{"type": "text", "text": receipt}])
    response = client.chat.completions.create(model=model, messages=[{"role": "user", "content": content}], temperature=0)
    return response.choices[0].message.content


@dataclass
class Attempt:
    reply: str
    error: str | None  # what went back to the model, or None when the reply passed


@dataclass
class Extraction:
    receipt: Receipt
    attempts: int
    cost: float
    history: list[Attempt]


class ExtractionFailed(Exception):
    def __init__(self, message: str, history: list[Attempt], cost: float):
        super().__init__(message)
        self.history, self.cost = history, cost


def response_format() -> dict:
    schema = Receipt.model_json_schema()
    spec = {"name": "receipt", "schema": schema}
    return {"type": "json_schema", "json_schema": spec}


def ask(client, model: str, messages: list) -> tuple[str, float]:
    """One call, held to the Receipt schema. Returns the reply text and its cost."""
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=0,
        response_format=response_format(),
    )
    cost = getattr(response.usage, "cost", None) or 0.0
    reply = response.choices[0].message.content
    return reply, cost


def repair_request(reply: str, error: ValidationError) -> list[dict]:
    return [
        {"role": "assistant", "content": reply},
        {"role": "user", "content": f"That JSON failed validation:\n{error}\nReturn the corrected JSON."},
    ]


def extract(receipt: str | list[dict], client, model: str, max_attempts: int = 2) -> Extraction:
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": receipt}]
    spent, history = 0.0, []
    for attempt in range(1, max_attempts + 1):
        reply, cost = ask(client, model, messages)
        spent += cost
        try:
            receipt = Receipt.model_validate_json(reply)
            history.append(Attempt(reply, None))
            return Extraction(receipt, attempt, spent, history)
        except ValidationError as error:
            history.append(Attempt(reply, str(error)))
            messages += repair_request(reply, error)
    raise ExtractionFailed(f"no valid ledger entry after {max_attempts} attempts:\n{history[-1].error}", history, spent)

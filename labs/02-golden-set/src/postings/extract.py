"""One posting in, the filter fields out, under whichever prompt file is being tried."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from postings.schema import Posting


@dataclass
class Reading:
    fields: dict
    cost: float


def response_format() -> dict:
    return {"type": "json_schema", "json_schema": {"name": "posting", "schema": Posting.model_json_schema()}}


def read(posting: str, prompt: str, client, model: str) -> Reading:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": posting}],
        temperature=0,
        response_format=response_format(),
    )
    fields = Posting.model_validate_json(response.choices[0].message.content).model_dump()
    return Reading(fields, getattr(response.usage, "cost", None) or 0.0)


def read_all(postings: dict[str, str], prompt: str, client, model: str) -> dict[str, Reading]:
    """Every posting (name -> text) under one prompt, eight at a time; the golden set is small enough to run on every change."""
    with ThreadPoolExecutor(8) as pool:
        return dict(zip(postings, pool.map(lambda text: read(text, prompt, client, model), postings.values())))

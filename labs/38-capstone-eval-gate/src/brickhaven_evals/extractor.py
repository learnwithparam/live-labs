"""A candidate is a prompt and a model. The extractor runs it on one tenant message.

The gate never calls a model itself: it takes an `extract` function, so tests pass scripted outputs
and the proof passes the real model. Both go through the same scoring code.
"""

import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from brickhaven_evals.config import CANDIDATES, DEFAULT_MODEL, LAB, TIMEOUT, VOTES, WORKERS, llm_settings, load_dotenv

Extract = Callable[["Candidate", str], dict]
EMPTY_OUTPUT = {"work_orders": []}


@dataclass
class Candidate:
    name: str
    prompt: str
    model: str


def load_candidate(name: str) -> Candidate:
    spec = json.loads((CANDIDATES / f"{name}.json").read_text())
    load_dotenv()
    # A candidate that names no model ships on the team default, so swapping the default is a candidate too.
    model = spec.get("model") or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    return Candidate(name=spec["name"], prompt=(LAB / spec["prompt"]).read_text(), model=model)


def parse_output(text: str) -> dict:
    """The model's reply as work orders. A reply that is not the promised JSON is an empty answer.

    An empty answer fails every field of its case, so a model that breaks the format is a regression
    the gate sees, never a case it silently skips.
    """
    try:
        parsed = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return dict(EMPTY_OUTPUT)
    orders = parsed.get("work_orders") if isinstance(parsed, dict) else None
    if not isinstance(orders, list) or not all(isinstance(order, dict) for order in orders):
        return dict(EMPTY_OUTPUT)
    return {"work_orders": orders}


def model_extract(candidate: Candidate, message: str) -> dict:
    """The real extractor: one call to an OpenAI-compatible endpoint at temperature 0."""
    from openai import OpenAI
    base_url, key = llm_settings()
    client = OpenAI(base_url=base_url, api_key=key, timeout=TIMEOUT)
    response = client.chat.completions.create(
        model=candidate.model, temperature=0, response_format={"type": "json_object"},
        messages=[{"role": "system", "content": candidate.prompt}, {"role": "user", "content": message}])
    return parse_output(response.choices[0].message.content)


def majority_answer(candidate: Candidate, message: str, extract: Extract) -> dict:
    """The answer most calls agree on. A model at temperature 0 still varies between calls.

    One lucky or unlucky call would flip a case between runs and block a release for noise, so each case
    is asked VOTES times. A tie keeps the first answer, which makes the result repeatable for scripted tests.
    """
    answers = [extract(candidate, message) for _ in range(VOTES)]
    counts = Counter(json.dumps(answer, sort_keys=True) for answer in answers)
    top = max(counts.values())
    return next(answer for answer in answers if counts[json.dumps(answer, sort_keys=True)] == top)


def run_candidate(candidate: Candidate, cases: list[dict], extract: Extract = model_extract) -> dict[str, dict]:
    """Every golden case through the candidate: {case id: its work orders}."""
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        outputs = list(pool.map(lambda case: majority_answer(candidate, case["message"], extract), cases))
    return {case["id"]: output for case, output in zip(cases, outputs)}


def save_run(name: str, run: dict[str, dict], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(run, ensure_ascii=False, indent=1))
    return path


def load_run(name: str, folder: Path) -> dict[str, dict]:
    return json.loads((folder / f"{name}.json").read_text())

"""Kettlebay Assist: one release of the merchant admin assistant, and the tool loop it runs.

A release is the three things a pull request can change: the system prompt, the tool descriptions
and the model. Any one of them changes behaviour, so the eval gate treats all three as the release.
"""

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from kettlebay.config import DEFAULT_MODEL, PRICES, load_dotenv
from kettlebay.store import Store

GOLDEN = Path("data/golden.jsonl")
# A merchant question needs two or three tool calls. A loop that runs past this is stuck, and a stuck
# run is a failed run, never a skipped one.
MAX_STEPS = 8


@dataclass
class Release:
    name: str
    spec: dict
    model: str
    prompt: str
    tools: list[dict]

    @property
    def fingerprint(self) -> str:
        """Changes when the prompt, the tools, the model or the golden set change, so an old result
        or an old override never stands for a new release."""
        payload = json.dumps([self.spec, self.model, self.prompt, self.tools, GOLDEN.read_text()], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:12]


def load_release(path: Path | str) -> Release:
    path = Path(path)
    if not path.exists():
        path = Path("releases") / f"{path.stem}.json"
    spec = json.loads(path.read_text())
    load_dotenv()
    # A release that names no model ships on the team default, so swapping the default is a release too.
    model = spec.get("model") or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
    return Release(name=spec["name"], spec=spec, model=model, prompt=Path(spec["prompt"]).read_text(),
                   tools=json.loads(Path(spec["tools"]).read_text()))


def load_golden(path: Path = GOLDEN) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed}


def converse(request: str, release: Release, client, store: Store) -> dict:
    """Answer one merchant request with this release, recording every tool call in order.

    The ordered list of calls is the trajectory. It is kept with each call's arguments, because a
    refund of the wrong amount is as wrong as a refund with no approval.
    """
    messages = [{"role": "system", "content": release.prompt}, {"role": "user", "content": request}]
    calls, totals = [], {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}
    started = time.monotonic()
    for _ in range(MAX_STEPS):
        response = client.chat.completions.create(
            model=release.model, messages=messages, tools=release.tools, parallel_tool_calls=False,
            extra_body={"usage": {"include": True}})
        for key, value in usage_of(response, release.model).items():
            totals[key] += value
        said = response.choices[0].message
        if not said.tool_calls:
            return {"answer": said.content or "", "calls": calls, "usage": totals,
                    "seconds": round(time.monotonic() - started, 1), "error": None}
        messages.append({"role": "assistant", "content": said.content or "", "tool_calls": [
            {"id": tool_call.id, "type": "function",
             "function": {"name": tool_call.function.name, "arguments": tool_call.function.arguments}}
            for tool_call in said.tool_calls]})
        for tool_call in said.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments, result = {}, {"error": "The arguments were not valid JSON."}
            else:
                result = store.call(tool_call.function.name, arguments)
            calls.append({"tool": tool_call.function.name, "args": arguments, "result": result})
            messages.append({"role": "tool", "tool_call_id": tool_call.id, "content": json.dumps(result)})
    return {"answer": "", "calls": calls, "usage": totals, "seconds": round(time.monotonic() - started, 1),
            "error": f"No answer after {MAX_STEPS} model calls."}

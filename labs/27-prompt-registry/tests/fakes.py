"""Stand-ins for Langfuse and the model, so the release logic runs with no key and no containers.

The fake registry keeps Langfuse's rules that the lab depends on: a label sits on one version, a new
version with a label takes it from the old one, and `get_prompt` serves a cached copy until its TTL runs
out. The fake model replays replies the real model gave to each version, recorded in an earlier run.
"""

import json
import time
from pathlib import Path
from types import SimpleNamespace

from driftwell.golden import cases

RECORDED = json.loads((Path(__file__).parent / "recorded_replies.json").read_text())
CASE_BY_MESSAGE = {case["message"]: case["id"] for case in cases()}


class NotFoundError(Exception):
    """Named like the SDK's error, because `delete_all` tells a missing prompt apart by the class name."""


class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeLangfuse:
    def __init__(self, clock=time.time):
        self.clock, self.down, self.fetches = clock, False, 0
        self.prompts: dict[str, list[dict]] = {}
        self.cache: dict[tuple, tuple[float, SimpleNamespace]] = {}
        self.generations: list[dict] = []
        self.api = SimpleNamespace(
            prompts=SimpleNamespace(list=self.list_prompts, get=self.get_version, delete=self.delete_prompt),
            prompt_version=SimpleNamespace(update=self.update_labels))

    # The parts of the SDK the lab calls.
    def create_prompt(self, name, prompt, labels, commit_message, config):
        stored = self.prompts.setdefault(name, [])
        for existing in stored:
            existing["labels"] = [label for label in existing["labels"] if label not in labels]
        stored.append({"version": len(stored) + 1, "prompt": prompt, "labels": list(labels),
                       "commit_message": commit_message, "config": config})
        return SimpleNamespace(version=len(stored))

    def get_prompt(self, name, label=None, version=None, cache_ttl_seconds=60, fallback=None, **_):
        key = (name, label, version)
        cached = self.cache.get(key)
        if cached and self.clock() - cached[0] < cache_ttl_seconds:
            return cached[1]
        if self.down:
            # Like the SDK: a stale copy beats nothing, then the caller's fallback, then an error.
            if cached:
                return cached[1]
            if fallback is not None:
                return SimpleNamespace(prompt=fallback, version=0, is_fallback=True)
            raise ConnectionError("registry unreachable")
        self.fetches += 1
        found = next(row for row in self.prompts[name]
                     if (version is not None and row["version"] == version) or (version is None and label in row["labels"]))
        served = SimpleNamespace(prompt=found["prompt"], version=found["version"], is_fallback=False)
        self.cache[key] = (self.clock(), served)
        return served

    def start_observation(self, prompt=None, metadata=None, **_):
        record = {"prompt_version": getattr(prompt, "version", None), "metadata": metadata}
        self.generations.append(record)
        return SimpleNamespace(trace_id=f"trace-{len(self.generations)}", update=lambda **fields: record.update(fields),
                               end=lambda: None)

    def flush(self):
        pass

    # The raw API under `client.api`.
    def list_prompts(self, name):
        if name not in self.prompts:
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=[SimpleNamespace(name=name, versions=[row["version"] for row in self.prompts[name]])])

    def get_version(self, name, version):
        row = self.prompts[name][version - 1]
        return SimpleNamespace(prompt=row["prompt"], labels=row["labels"] + (["latest"] if version == len(self.prompts[name]) else []),
                               commit_message=row["commit_message"], config=row["config"])

    def update_labels(self, name, version, new_labels):
        for row in self.prompts[name]:
            row["labels"] = [label for label in row["labels"] if label not in new_labels]
        self.prompts[name][version - 1]["labels"] += new_labels

    def delete_prompt(self, name):
        if name not in self.prompts:
            raise NotFoundError(name)
        del self.prompts[name]


class FakeOpenAI:
    """Answers the way the real model answered each version: the system prompt decides which recording plays."""

    def __init__(self):
        self.calls: list[list[dict]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, **_):
        self.calls.append(messages)
        system, question = messages[0]["content"], messages[1]["content"]
        recording = "v3" if "full refund straight away" in system else "v2" if "Export data" in system else "v1"
        case_id = CASE_BY_MESSAGE.get(question)
        text = RECORDED[recording][case_id] if case_id else "I'll pass this to our support team."
        usage = SimpleNamespace(prompt_tokens=400, completion_tokens=60, prompt_tokens_details=None,
                                model_extra={"cost": 0.00007})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage)

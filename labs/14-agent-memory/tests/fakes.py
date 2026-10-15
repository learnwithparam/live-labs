"""A scripted model and a word-count embedder, so Mem0, both policies and the scoring run with no key
and no network.

The fake model answers the three calls the lab makes: Mem0's extraction (it saves each customer
message as one memory, or the texts in `saves`), our reconcile call (it plays `decisions`, written with
pieces of memory text instead of ids), and the agent's reply.
"""

import hashlib
import json
import math
import re
from types import SimpleNamespace

from mem0.embeddings.base import EmbeddingBase


class WordEmbedder(EmbeddingBase):
    """Each word adds to one of 384 slots, so texts that share words land near each other."""

    def embed(self, text, memory_action=None):
        vector = [0.0] * 384
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % 384] += 1.0
        length = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / length for value in vector]


class FakeOpenAI:
    def __init__(self, saves: dict | None = None, decisions: dict | None = None, reply: str = "Happy to help."):
        self.saves, self.decisions, self.reply = saves or {}, decisions or {}, reply
        self.extraction_prompts: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        system, last = request["messages"][0]["content"], request["messages"][-1]["content"]
        if "Memory Extractor" in system:
            self.extraction_prompts.append(last)
            # Only the "New Messages" part is this turn; the rest is Mem0's recent-message context.
            new = last.split("## New Messages", 1)[1].split("## Observation Date", 1)[0]
            said = new.strip().removeprefix("user:").strip()
            texts = self.saves.get(said, [said])
            content = json.dumps({"memory": [{"id": str(number), "text": text} for number, text in enumerate(texts)]})
        elif "keeps memories" in system:
            content = json.dumps({"actions": self.reconcile(json.loads(last))})
        else:
            content = self.reply
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10, prompt_tokens_details=None,
                                model_extra={"cost": 0.0001})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))], usage=usage)

    def reconcile(self, payload: dict) -> list[dict]:
        """Turn {"old": "Mill Lane"} into {"old": "3"}: the id of the memory whose text holds those words."""
        listed = payload["new"] + payload["older"]

        def find(piece):
            return next((item["id"] for item in listed if piece.lower() in item["text"].lower()), piece)

        actions = []
        for action in self.decisions.get(payload["message"], []):
            actions.append({key: (find(value) if key in ("old", "new", "id") else value) for key, value in action.items()})
        return actions

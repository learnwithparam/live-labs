"""Mem0, wired for one support desk: a Qdrant store on disk, embeddings on this machine, and a model
whose every call is billed.

Mem0 builds its embedder and model from a config dict. We hand it ready objects instead, for two
reasons: one embedding model serves every store (it takes a second to load), and the model adapter
reads the provider's bill on each call, which Mem0's own OpenAI adapter throws away.
"""

import json
import shutil
from pathlib import Path

from mem0 import Memory
from mem0.llms.base import LLMBase

from pawsley.config import EMBED_DIMS, EMBED_MODEL, PRICES

TEMPERATURE = 0


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


class BilledModel(LLMBase):
    """The model Mem0 calls to pull facts out of a message, and the one our own code calls too.

    Every call adds its tokens and cost to `bill`, so the review prices each policy from what the
    provider charged, Mem0's extraction calls included.
    """

    def __init__(self, client, model: str):
        super().__init__({"model": model})
        self.client, self.model, self.bill = client, model, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cost": 0.0}

    def generate_response(self, messages, response_format=None, tools=None, tool_choice="auto", **kwargs) -> str:
        request = {"model": self.model, "messages": messages, "temperature": TEMPERATURE,
                   "extra_body": {"usage": {"include": True}}}
        if response_format:
            request["response_format"] = response_format
        response = self.client.chat.completions.create(**request)
        used = usage_of(response, self.model)
        self.bill["calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "cost"):
            self.bill[key] += used[key]
        return response.choices[0].message.content or ""

    def json(self, system: str, payload: dict) -> dict:
        """One call that must answer in JSON. A reply that is not JSON reads as no answer, never a guess."""
        text = self.generate_response([{"role": "system", "content": system},
                                       {"role": "user", "content": json.dumps(payload, indent=1)}],
                                      response_format={"type": "json_object"})
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {}


def local_embedder():
    """Mem0's FastEmbed embedder: an ONNX model on this CPU, so search needs no embeddings key."""
    from mem0.configs.embeddings.base import BaseEmbedderConfig
    from mem0.embeddings.fastembed import FastEmbedEmbedding

    return FastEmbedEmbedding(BaseEmbedderConfig(model=EMBED_MODEL, embedding_dims=EMBED_DIMS))


# Saved facts must be about the customer, in their words. Requests to forget are acted on, not saved,
# and the agent's own replies are never a source: only what the customer said is true.
SAVE_ONLY = ("Save only facts the customer states about themselves, their pets, their deliveries and how to "
             "contact them. Keep names, numbers, addresses and recipe names exactly as the customer wrote them. "
             "Do not save requests to forget something, greetings, or questions.")


def open_memory(folder: Path, model: BilledModel, embedder, fresh: bool = False) -> Memory:
    """A Mem0 store in `folder`: vectors in Qdrant on disk, change history in SQLite beside them."""
    if fresh and folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True, exist_ok=True)
    config = {
        "vector_store": {"provider": "qdrant", "config": {"path": str(folder / "qdrant"), "on_disk": True,
                                                         "collection_name": "pawsley", "embedding_model_dims": EMBED_DIMS}},
        # Placeholders, replaced below: building them makes no network call.
        "embedder": {"provider": "openai", "config": {"api_key": "unused", "embedding_dims": EMBED_DIMS}},
        "llm": {"provider": "openai", "config": {"api_key": "unused", "model": model.model}},
        "history_db_path": str(folder / "history.db"),
        "custom_instructions": SAVE_ONLY,
    }
    memory = Memory.from_config(config)
    memory.embedding_model, memory.llm = embedder, model
    return memory


def all_memories(memory: Memory, customer: str) -> list[dict]:
    """Every memory kept for one customer, oldest first, as the memory panel lists them."""
    found = memory.get_all(filters={"user_id": customer}, top_k=100)["results"]
    return sorted(found, key=lambda item: item.get("created_at") or "")

"""The model provider: one streamed call that reports the first token's time, the tokens and the generation id.

The generation id is the receipt number. The ledger stores it, and the provider's billing record is
looked up by it later, so the two books can be compared line by line.
"""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from brickhaven_api.config import PROVIDER_TIMEOUT_S


class ProviderError(Exception):
    """The provider did not give an answer: a timeout, a 429, a 5xx or a broken stream."""


@dataclass
class Completion:
    text: str
    generation_id: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    first_token_ms: float


class OpenRouterProvider:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def complete(self, model: str, system: str, user: str) -> Completion:
        body = {"model": model, "stream": True, "stream_options": {"include_usage": True}, "temperature": 0,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        request = urllib.request.Request(f"{self.base_url}/chat/completions", json.dumps(body).encode(), method="POST",
                                         headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
        started = time.perf_counter()
        text, first_token_ms, generation_id, usage = [], None, "", {}
        try:
            # The timeout applies to each wait for the next bytes, so a stalled model fails fast.
            with urllib.request.urlopen(request, timeout=PROVIDER_TIMEOUT_S) as response:
                for raw_line in response:
                    line = raw_line.decode().strip()
                    if not line.startswith("data:") or line == "data: [DONE]":
                        continue
                    chunk = json.loads(line[5:])
                    if chunk.get("error"):
                        raise ProviderError(str(chunk["error"])[:200])
                    generation_id = chunk.get("id") or generation_id
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            first_token_ms = first_token_ms if first_token_ms is not None else (time.perf_counter() - started) * 1000
                            text.append(piece)
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise ProviderError(f"{type(error).__name__}: {error}") from error
        if first_token_ms is None or not generation_id:
            raise ProviderError("the stream ended with no text")
        return Completion("".join(text), generation_id, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), first_token_ms)


class FaultInjectingProvider:
    """Wraps a provider and fails chosen models on demand, so fallback can be seen without waiting for an outage."""

    def __init__(self, inner, failing_models: set[str]):
        self.inner = inner
        self.failing_models = set(failing_models)

    def complete(self, model: str, system: str, user: str) -> Completion:
        if model in self.failing_models:
            raise ProviderError("injected failure")
        return self.inner.complete(model, system, user)

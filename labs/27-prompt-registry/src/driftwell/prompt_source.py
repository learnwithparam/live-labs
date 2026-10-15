"""How the running bot reads its prompt: from the registry, through the SDK's cache, and never empty.

The SDK keeps each prompt for `cache_ttl_seconds`. After that it hands back the stale copy and refreshes
in the background, so a label move reaches a running bot within one window, with no restart.
"""

import re
from dataclasses import dataclass

from driftwell.config import CACHE_TTL_SECONDS, PROMPT_NAME
from driftwell.naive import PROMPT_IN_CODE

VARIABLE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


@dataclass
class ServedPrompt:
    """The prompt one reply used, and where it came from, so every reply can say which version answered."""

    text: str
    version: int | None
    source: str  # "registry", "last fetched" or "pinned fallback"
    sdk_prompt: object | None = None  # kept so the trace links to this exact version
    reason: str = ""

    def fill(self, **variables: str) -> str:
        # Langfuse's `{{name}}` syntax, filled the same way for a pinned fallback that never came from it.
        return VARIABLE.sub(lambda match: variables.get(match.group(1), match.group(0)), self.text)


class PromptSource:
    """One label (or one pinned version) of one prompt, as a long-running process sees it."""

    def __init__(self, client, label: str | None = "production", version: int | None = None,
                 name: str = PROMPT_NAME, ttl_seconds: int = CACHE_TTL_SECONDS, pinned: str = PROMPT_IN_CODE):
        self.client, self.label, self.version, self.name = client, label if version is None else None, version, name
        self.ttl_seconds, self.pinned = ttl_seconds, pinned
        self.last_good: ServedPrompt | None = None

    def get(self) -> ServedPrompt:
        """Fail closed: the registry's copy, else the last one fetched, else the prompt pinned in the code."""
        reason = ""
        try:
            fetched = self.client.get_prompt(self.name, label=self.label, version=self.version,
                                             cache_ttl_seconds=self.ttl_seconds, fallback=self.pinned,
                                             max_retries=1, fetch_timeout_seconds=3)
        except Exception as error:
            fetched, reason = None, f"{type(error).__name__}: {error}"
        if fetched is not None and fetched.is_fallback:
            fetched, reason = None, reason or "the registry did not answer"
        if fetched is not None and not fetched.prompt.strip():
            # A blank version would send the model nothing to follow. Treat it like an outage.
            fetched, reason = None, f"version {fetched.version} is empty"
        if fetched is not None:
            self.last_good = ServedPrompt(fetched.prompt, fetched.version, "registry", fetched)
            return self.last_good
        if self.last_good is not None:
            return ServedPrompt(self.last_good.text, self.last_good.version, "last fetched",
                                self.last_good.sdk_prompt, reason)
        return ServedPrompt(self.pinned, None, "pinned fallback", None, reason)

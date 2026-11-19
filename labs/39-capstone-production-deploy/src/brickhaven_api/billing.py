"""The provider's own billing record for one generation: the truth the ledger is checked against."""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from brickhaven_api.config import BILLING_WAIT_S


@dataclass
class Billing:
    cost: float
    prompt_tokens: int
    completion_tokens: int


class OpenRouterBilling:
    def __init__(self, base_url: str, api_key: str, wait_seconds: float = BILLING_WAIT_S):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.wait_seconds = wait_seconds

    def lookup(self, generation_id: str) -> Billing | None:
        """GET /generation?id=... The record can appear a few seconds after the call, so a 404 is retried."""
        url = f"{self.base_url}/generation?{urllib.parse.urlencode({'id': generation_id})}"
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                request = urllib.request.Request(url, headers={"Authorization": f"Bearer {self.api_key}"})
                with urllib.request.urlopen(request, timeout=15) as response:
                    record = json.load(response)["data"]
                return Billing(float(record["total_cost"]), int(record.get("tokens_prompt") or 0), int(record.get("tokens_completion") or 0))
            except urllib.error.HTTPError as error:
                if error.code != 404 or time.monotonic() >= deadline:
                    return None
            time.sleep(1.5)

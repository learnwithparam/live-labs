"""Per-client API keys: each caller has its own key, its own limit and its own line in the ledger.

One shared key means one shared limit, and a runaway job then starves every honest caller. A key per
client lets the service say who is asking before it spends any money on the answer.
"""

import hashlib
import hmac
import tomllib
from dataclasses import dataclass
from pathlib import Path

from brickhaven_api.config import DATA, HARD_CEILING_PER_MINUTE


@dataclass
class Client:
    name: str
    owner: str
    key_hash: str
    per_minute: int
    burst: int


def hash_key(key: str) -> str:
    # Only the hash is stored, so a leaked clients file does not leak a working key.
    return hashlib.sha256(key.encode()).hexdigest()


class ClientKeys:
    def __init__(self, path: Path = DATA / "clients.toml"):
        self.clients = [Client(**row) for row in tomllib.loads(path.read_text())["client"]]

    def authenticate(self, authorization: str) -> Client | None:
        """The client behind `Authorization: Bearer <key>`, or None for a missing or unknown key."""
        scheme, _, presented = authorization.partition(" ")
        if scheme.lower() != "bearer" or not presented.strip():
            return None
        presented_hash = hash_key(presented.strip())
        found = None
        # Compare against every client, so the time taken does not say which hashes are close.
        for client in self.clients:
            if hmac.compare_digest(client.key_hash, presented_hash):
                found = client
        return found

    def by_name(self, name: str) -> Client | None:
        return next((client for client in self.clients if client.name == name), None)

    def raise_limit(self, name: str, per_minute: int) -> Client:
        """Set a higher refill rate. The burst grows in proportion; the ceiling is never passed."""
        client = self.by_name(name)
        if client is None:
            raise ValueError(f"no client named {name}")
        if per_minute <= client.per_minute:
            raise ValueError(f"{name} already has {client.per_minute} a minute; a raise must be higher")
        if per_minute > HARD_CEILING_PER_MINUTE:
            raise ValueError(f"the ceiling is {HARD_CEILING_PER_MINUTE} a minute")
        client.burst = max(client.burst, round(client.burst * per_minute / client.per_minute))
        client.per_minute = per_minute
        return client

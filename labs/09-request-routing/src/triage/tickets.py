"""Tallyroll's six support queues and its tickets, each with the queue it was finally resolved in."""

import json
from dataclasses import dataclass
from pathlib import Path

DATA = Path(__file__).parent.parent.parent / "data"


@dataclass(frozen=True)
class Ticket:
    id: str
    subject: str
    body: str
    # Where a person finally solved it. The routers never see it; the review compares against it.
    queue: str

    @property
    def text(self) -> str:
        return f"{self.subject}\n\n{self.body}"


def queues(path: Path = DATA / "queues.json") -> dict[str, str]:
    """Queue name to what it handles: the routing guide every router reads, in the order the page shows."""
    return json.loads(path.read_text())


def load(path: Path) -> list[Ticket]:
    return [Ticket(**json.loads(line)) for line in path.read_text().splitlines() if line.strip()]


def history() -> list[Ticket]:
    """Last quarter's solved tickets. Only the embedding router learns from them."""
    return load(DATA / "history.jsonl")


def inbox() -> list[Ticket]:
    """This morning's tickets, the ones every router is scored on."""
    return load(DATA / "inbox.jsonl")

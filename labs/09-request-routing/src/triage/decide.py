"""The decision-model router: ask one choice question in the System One format, get a probability per queue.

A decision model is trained to answer choice, score and yes-or-no questions, not to write prose. It
reads its probabilities off the choices themselves instead of writing a number, and it is trained so
that they match how often it is right. System One is the request format several of them share: Strands Decider on this laptop,
and hosted ones such as Perplexity's and Cloudflare's. Pointing DECISION_BASE_URL at another is one line.
"""

import subprocess
import time
from urllib.parse import urlparse

import httpx

from triage.route import Route
from triage.tickets import Ticket

LOCAL = ("127.0.0.1", "localhost")
QUESTION = "Which Tallyroll support queue's team can solve this ticket?"


def question(queues: dict[str, str]) -> dict:
    """The routing guide as one choice question: each queue name with what it handles, as its criteria."""
    return {"queue": {"type": "choice", "instructions": QUESTION, "criteria": queues}}


class DecisionRouter:
    def __init__(self, queues: dict[str, str], base_url: str, model: str, api_key: str = "", client: httpx.Client | None = None):
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(base_url=base_url, headers=headers, timeout=120)
        self.model, self.questions = model, question(queues)
        # Strands Decider on Apple silicon crashes on overlapping requests (a Metal assertion), so a
        # local server gets one ticket at a time. A hosted endpoint takes four.
        self.workers = 1 if urlparse(base_url).hostname in LOCAL else 4

    def __call__(self, ticket: Ticket) -> Route:
        began = time.perf_counter()
        reply = self.client.post("/v1/systemone", json={"model": self.model, "state": ticket.text, "questions": self.questions})
        reply.raise_for_status()
        answer = reply.json()["answers"]["queue"]
        probabilities = answer["probabilities"]
        # The probability of the queue it picked: the same 0 to 1 scale the other routers report.
        return Route(answer["choice"], probabilities[answer["choice"]], probabilities,
                     ms=(time.perf_counter() - began) * 1000)


def is_up(base_url: str) -> bool:
    try:
        return httpx.get(f"{base_url}/health", timeout=2).status_code == 200
    except httpx.HTTPError:
        return False


def start_local(base_url: str, model: str, wait: float = 900) -> subprocess.Popen | None:
    """Start Strands Decider on this machine when DECISION_BASE_URL points here and nothing answers.

    The first start downloads about 4 GB of weights and can take minutes; later ones take about 30 s.
    A hosted endpoint, or a server you started yourself, is left alone and None comes back.
    """
    where = urlparse(base_url)
    if where.hostname not in LOCAL or is_up(base_url):
        return None
    server = subprocess.Popen(
        ["uv", "run", "--frozen", "--group", "decider", "strands-decider", "serve", model, "--port", str(where.port or 8099)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if is_up(base_url):
            return server
        if server.poll() is not None:
            raise SystemExit("The decision model stopped while starting. Run `uv run triage decider` to see why.")
        time.sleep(2)
    server.terminate()
    raise SystemExit(f"The decision model did not answer on {base_url} within {wait:.0f} s.")

"""The load test, as a Locust file: eight people asking questions, and one script that never stops.

Run it through `assistant load`, which picks the target and reads the gateway's log afterwards. Each
person waits a moment between questions, like someone reading an answer. The script is pf-sync-bot,
which a Pemberton Foods engineer wrote to copy answers into a spreadsheet, asking again every 200 ms.
"""

import itertools
import json
import os
import time
from pathlib import Path

from locust import HttpUser, between, constant, events, task

from mosswood.tokens import issue
from mosswood.workspace import users

TARGET = os.environ.get("MOSSWOOD_TARGET", "gateway")
RESULTS = Path(os.environ.get("MOSSWOOD_RESULTS", "out/load/client.jsonl"))
BOT = ("pemberton", "pf-sync-bot")
BOT_COPIES = 20  # the script runs in twenty threads, which is how one client floods a shared service
QUESTIONS = ["What is overdue in my workspace?", "Which tasks are blocked, and why?",
             "What is due in the next two weeks?", "Which project has the most open tasks?"]

people = itertools.cycle([pair for pair in users() if pair != BOT])
asked = itertools.count()
records: list[dict] = []


def ask(user: HttpUser, kind: str) -> None:
    """Ask once and time it from the caller's side: the first visible text, then the end."""
    question = QUESTIONS[next(asked) % len(QUESTIONS)]
    started = time.perf_counter()
    record = {"kind": kind, "tenant": user.tenant_id, "user": user.user_id, "status": 0, "first_text_ms": None,
              "total_ms": None, "error": False, "at": time.time()}
    try:
        if TARGET == "direct":
            # The naive endpoint: nothing to show until the whole answer is back.
            with user.client.post("/v1/answer", json={"tenant": user.tenant_id, "user": user.user_id, "question": question},
                                  name="answer, whole JSON", timeout=60, catch_response=True) as response:
                record["status"] = response.status_code
                record["error"] = response.status_code >= 500 or response.status_code == 0
                if response.status_code == 200:
                    record["first_text_ms"] = (time.perf_counter() - started) * 1000
                response.success()
        else:
            with user.client.post("/v1/chat", json={"question": question}, headers={"Authorization": f"Bearer {user.token}"},
                                  name="chat, streamed", timeout=60, stream=True, catch_response=True) as response:
                record["status"] = response.status_code
                for line in response.iter_lines(decode_unicode=True):
                    if line == "event: token" and record["first_text_ms"] is None:
                        record["first_text_ms"] = (time.perf_counter() - started) * 1000
                    if line == "event: error":
                        record["error"] = True
                record["error"] = record["error"] or response.status_code >= 500 or response.status_code == 0
                response.success()
    except Exception as error:  # a reset or a timeout on the caller's side is an error, never a crash of the test
        record["error"], record["detail"] = True, type(error).__name__
    record["total_ms"] = (time.perf_counter() - started) * 1000
    records.append(record)


class Person(HttpUser):
    fixed_count = 8
    wait_time = between(1, 2.5)

    def on_start(self) -> None:
        self.tenant_id, self.user_id = next(people)
        self.token = issue(self.tenant_id, self.user_id)

    @task
    def question(self) -> None:
        ask(self, "person")


class SyncBot(HttpUser):
    fixed_count = BOT_COPIES
    wait_time = constant(0.2)

    def on_start(self) -> None:
        self.tenant_id, self.user_id = BOT
        self.token = issue(self.tenant_id, self.user_id)

    @task
    def question(self) -> None:
        ask(self, "bot")


@events.test_stop.add_listener
def save(**_) -> None:
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text("".join(json.dumps(record) + "\n" for record in records))

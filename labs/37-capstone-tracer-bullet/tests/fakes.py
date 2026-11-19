"""A scripted stand-in for the model, so the slice, its spans and the page run with no key.

Each message has a script of what the model does on its extraction calls: answer well, wrap the JSON in a
code fence, leave a trailing comma, ramble, or time out. That gives the naive version real crashes and
the slice real failures to retry, repair and hand to a person, with no network call.
"""

import json
import re
import threading
from types import SimpleNamespace

from brickhaven_slice.vendors import load_messages

GOOD = {
    "m01": ("plumbing", "normal", "4B", False, "after 18:00"), "m02": ("heating", "emergency", "2A", True, None),
    "m03": ("heating", "high", "7C", False, "all week"), "m04": ("electrical", "emergency", "3D", True, None),
    "m05": ("locksmith", "high", "1A", False, "now"), "m06": ("appliance", "normal", "5B", False, "mornings"),
    "m07": ("electrical", "low", "6A", False, "weekdays after 17:00"), "m08": ("heating", "normal", "2B", False, "Saturday morning"),
    "m09": ("plumbing", "emergency", "8A", True, "out 9 to 5"), "m10": ("general", "normal", "3B", False, "any time"),
    "m11": ("plumbing", "normal", None, False, None), "m12": ("pest", "normal", "4A", False, None),
    "m13": ("plumbing", "normal", "6B", False, "afternoons"), "m14": ("heating", "high", "1B", False, "all day"),
    "m15": ("general", "low", "7A", False, None), "m16": ("plumbing", "emergency", "2C", True, None),
}
# What the model does on each extraction call for a message, in order. Anything not listed answers well.
EXTRACTION_SCRIPT = {
    "m01": ["timeout", "ok"], "m02": ["fenced"], "m04": ["rambling"], "m06": ["timeout", "timeout", "timeout"],
    "m08": ["trailing comma"],
}
DRAFTING_SCRIPT = {"m15": ["timeout", "ok"]}


def as_json(message_id: str) -> str:
    trade, urgency, unit, safety, window = GOOD[message_id]
    return json.dumps({"trade": trade, "urgency": urgency, "unit": unit, "safety_flag": safety, "access_window": window})


class FakeModel:
    """`client.chat.completions.create`, answering from the scripts above."""

    def __init__(self):
        self.texts = {message["text"]: message["id"] for message in load_messages()}
        self.calls: dict[tuple[str, str], int] = {}
        self.lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def identify(self, user: str) -> str:
        for text, message_id in self.texts.items():
            if text in user:
                return message_id
        return re.search(r"\b(m\d\d)\b", user).group(1)

    def next_step(self, role: str, message_id: str, script: dict) -> str:
        with self.lock:
            count = self.calls[(role, message_id)] = self.calls.get((role, message_id), 0) + 1
        steps = script.get(message_id, [])
        return steps[count - 1] if count <= len(steps) else "ok"

    def create(self, model, messages, timeout, **_):
        system, user = messages[0]["content"], messages[1]["content"]
        message_id = self.identify(user)
        role = system.split(".")[0]
        if role == "Extraction":
            step = self.next_step(role, message_id, EXTRACTION_SCRIPT)
        elif role == "Drafting":
            step = self.next_step(role, message_id, DRAFTING_SCRIPT)
        else:
            step = "ok"
        if step == "timeout":
            raise TimeoutError("Request timed out.")
        if role == "Drafting":
            text = f"Please send someone to the flat for message {message_id}. The tenant is home as noted."
        elif step == "fenced":
            text = "```json\n" + as_json(message_id) + "\n```"
        elif step == "trailing comma":
            text = as_json(message_id)[:-1] + ",}"
        elif step == "rambling":
            text = f"Sure! For {message_id} the tenant has a problem with the flat, and someone should look."
        else:
            text = as_json(message_id)
        usage = SimpleNamespace(prompt_tokens=len(user.split()) + 60, completion_tokens=len(text.split()) + 5,
                                model_extra={"cost": 0.0001})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=usage)

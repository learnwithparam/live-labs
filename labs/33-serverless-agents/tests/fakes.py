"""A scripted contract reader behind a real OpenAI-compatible HTTP endpoint, so the host process, the
runner and the crash are all exercised with no key. It reads the three contract styles with regexes."""

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PATTERNS = {
    "monthly_fee": r"(?:platform fee of|Monthly subscription:|monthly platform fee is) \$([\d,]+\.\d\d)",
    "included_calls": r"(?:includes|Included usage:|covers the first) ([\d,]+) API calls",
    "overage_per_1000": r"(?:billed at|Additional usage:|1,000 API calls costs) \$(\d+\.\d\d)",
    "overage_discount_percent": r"(\d+)% (?:discount|off)|discount of (\d+)%",
    "monthly_cap": r"(?:will not exceed|capped at|exceed) \$([\d,]+\.\d\d)",
}


def read_like_a_model(contract: str, wrong_fee: bool = False) -> dict:
    sentences = re.split(r"(?<=\.) (?=[A-Z(\d])", contract)
    terms, quotes = {}, []
    for field, pattern in PATTERNS.items():
        found = re.search(pattern, contract)
        if not found:
            terms[field] = 0 if field == "overage_discount_percent" else None
            continue
        number = float(next(group for group in found.groups() if group).replace(",", ""))
        terms[field] = int(number) if field == "included_calls" else number
        quotes += [sentence for sentence in sentences if found.group(0) in sentence and sentence not in quotes]
    if wrong_fee and terms["monthly_fee"]:
        terms["monthly_fee"] += 1000
    return {**terms, "quotes": quotes}


class FakeModel:
    """Serves /chat/completions on a local port and records every call, with an optional delay per call."""

    def __init__(self, port: int, delay: float = 0.0, wrong_fee_for: str = ""):
        self.calls: list[str] = []
        self.delay, self.wrong_fee_for = delay, wrong_fee_for
        fake = self

        class Completions(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                contract = request["messages"][-1]["content"]
                fake.calls.append(contract)
                # Calls take different times, like a real model, so the crash catches some of them mid-flight.
                time.sleep(fake.delay * (1 + len(contract) % 3))
                terms = read_like_a_model(contract, wrong_fee=bool(fake.wrong_fee_for) and fake.wrong_fee_for in contract)
                body = json.dumps({
                    "id": "fake", "object": "chat.completion", "created": 0, "model": request["model"],
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": json.dumps(terms)}}],
                    "usage": {"prompt_tokens": 300, "completion_tokens": 120, "total_tokens": 420, "cost": 0.00009},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Completions)
        self.base_url = f"http://127.0.0.1:{port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

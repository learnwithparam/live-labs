"""A stand-in for `vllm serve` on a laptop: the same routes, the same API key rule, a slow start, no GPU.

It exists so the whole GPU run can be rehearsed on a Mac before a single GPU minute is spent. It
answers from a keyword table, so some answers are right and some wrong. It can be told to hang on
one item or ramble on another, to rehearse a timeout and a failed validation.

    VLLM_API_KEY=... python -m corvane_gpu.stand_in --port 8351 --startup-seconds 2
"""

import argparse
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEYWORDS = {
    "airfare": ["AIRLINE", "AIR ", "DELTA", "UNITED", "JETBLUE", "ALASKA", "SOUTHWES"],
    "lodging": ["MARRIOTT", "HILTON", "HYATT", "AIRBNB", "HOTEL", "INN"],
    "ground_transport": ["UBER   *TRIP", "LYFT", "AMTRAK", "PARKING", "SHELL", "HERTZ"],
    "meals": ["EATS", "STARBUCKS", "DOORDASH", "CAFE", "GRILL", "COSTCO"],
    "software": ["FIGMA", "NOTION", "AWS", "GITHUB", "SLACK", "OPENAI", "ATLASSIAN"],
    "advertising": ["FACEBK", "GOOGLE *ADS", "LINKEDIN", "VISTAPRINT"],
    "office_supplies": ["STAPLES", "AMZN", "BEST BUY", "IKEA"],
    "shipping": ["FEDEX", "UPS", "USPS", "DHL"],
    "telecom": ["VERIZON", "COMCAST", "AT&T", "GOGOAIR", "T-MOBILE"],
}


def answer_for(descriptor: str) -> str:
    for category, words in KEYWORDS.items():
        if any(word in descriptor for word in words):
            return category
    return "professional_services"


def handler(api_key: str, latency: float, hang_on: str, hang_seconds: float, ramble_on: str):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorised(self) -> bool:
            # The same rule as vLLM's --api-key: every /v1 route needs the bearer token, /health does not.
            return self.headers.get("Authorization") == f"Bearer {api_key}"

        def log_message(self, *args) -> None:
            pass

        def do_GET(self) -> None:
            if self.path == "/health":
                return self.reply(200, {})
            if not self.authorised():
                return self.reply(401, {"error": "Unauthorized"})
            self.reply(200, {"object": "list", "data": [{"id": "stand-in", "object": "model"}]})

        def do_POST(self) -> None:
            if not self.authorised():
                return self.reply(401, {"error": "Unauthorized"})
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            last = request["messages"][-1]["content"]
            descriptor = re.search(r"Descriptor: (.*)", last).group(1)
            time.sleep(hang_seconds if hang_on and hang_on in descriptor else latency)
            text = (f"It could be {answer_for(descriptor)} or travel, hard to say" if ramble_on and ramble_on in descriptor
                    else answer_for(descriptor))
            prompt_tokens = sum(len(message["content"]) for message in request["messages"]) // 4
            self.reply(200, {"id": "stand-in", "object": "chat.completion", "model": request["model"],
                             "choices": [{"index": 0, "finish_reason": "stop",
                                          "message": {"role": "assistant", "content": text}}],
                             "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 3,
                                       "total_tokens": prompt_tokens + 3}})

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--startup-seconds", type=float, default=2.0)
    parser.add_argument("--latency", type=float, default=0.05)
    parser.add_argument("--hang-on", default="")
    parser.add_argument("--hang-seconds", type=float, default=60.0)
    parser.add_argument("--ramble-on", default="")
    options = parser.parse_args()
    # A real server loads weights before it listens; the sleep is that load, so the cold start is measurable.
    time.sleep(options.startup_seconds)
    server = ThreadingHTTPServer(("127.0.0.1", options.port),
                                 handler(os.environ["VLLM_API_KEY"], options.latency, options.hang_on,
                                         options.hang_seconds, options.ramble_on))
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()

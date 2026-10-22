"""`phoneline serve`: Lucca Brick Oven's kitchen ticket display, filled as the caller speaks.

The display shows the last scored shift and takes live calls from the browser microphone over WebRTC.
A live call runs the same `run_bot` that Pipecat's eval suite plays the recorded calls into, so what
you hear on the line is what the shift was scored on. Pipecat runs on one asyncio loop in a background
thread; the page posts its offer, then polls the call for the transcript, the ticket and each turn.
"""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from lucca import config
from lucca.review import OUT_FILE, load_calls

PAGE = Path(__file__).with_name("page.html")


def summary(call: dict) -> str:
    """One line under each caller's name: what a person keyed from the call."""
    keyed = call["keyed"]
    if "staff" in keyed:
        return f"for staff: {keyed['staff']}"
    items = sum(line["quantity"] for line in keyed["lines"])
    return f"{items} item{'s' if items > 1 else ''} keyed for {keyed['name']}"


class Line:
    """The phone line. It takes one call at a time, as a single line does, and keeps every call so the
    page can poll it."""

    def __init__(self, review_file: Path = OUT_FILE):
        self.review_file, self.calls, self.busy = review_file, {}, None
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.webrtc = None

    def state(self) -> dict:
        from lucca.till import Menu

        review = json.loads(self.review_file.read_text()) if self.review_file.exists() else None
        shift = None
        if review:
            shift = {"metric": review["metric"],
                     "rows": [{key: row[key] for key in ("id", "caller", "ok", "why", "staff_expected", "ticket", "turns", "events")}
                              for row in review["rows"]]}
        calls = [{"id": call["id"], "caller": call["caller"], "summary": summary(call)} for call in load_calls()]
        return {"menu": Menu().data, "calls": calls, "shift": shift, "busy": self.busy}

    def finish(self, call_id: str, result: dict | None = None, error: str | None = None) -> None:
        call = self.calls[call_id]
        if result:
            call.update(ticket=result["ticket"], turns=result["turns"], cost=result["cost"])
        if error:
            call["error"] = error
        call["done"] = True
        if self.busy == call_id:
            self.busy = None

    def answer(self, offer: dict) -> dict:
        """Answer a browser's WebRTC offer and start a live call on the connection it opens."""
        # A missing key fails the offer with a message, before the browser hears a line that cannot think.
        # It is checked here, not on the loop: SystemExit there would stop the loop and hang every call.
        config.llm_settings()
        return asyncio.run_coroutine_threadsafe(self.connect(offer), self.loop).result(timeout=60)

    async def connect(self, offer: dict) -> dict:
        from pipecat.runner.types import SmallWebRTCRunnerArguments
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest, SmallWebRTCRequestHandler
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

        from lucca.bot import on_apple_silicon, run_bot, transport_params

        if on_apple_silicon():
            from lucca.voice import warm

            # The voice loads once per server, so the caller never waits on it inside a turn.
            await warm(config.AGENT_VOICE)
        self.webrtc = self.webrtc or SmallWebRTCRequestHandler()
        call_id = uuid.uuid4().hex[:8]
        self.calls[call_id] = {"caller": "browser caller", "events": [], "done": False}
        self.busy = call_id
        events = self.calls[call_id]["events"]

        async def on_connection(connection) -> None:
            transport = SmallWebRTCTransport(webrtc_connection=connection, params=transport_params["webrtc"]())
            runner_args = SmallWebRTCRunnerArguments(connection, body={"call": "live"})

            async def run() -> None:
                try:
                    self.finish(call_id, await run_bot(transport, runner_args, events.append))
                except (Exception, SystemExit) as error:  # SystemExit too, or the page polls forever
                    self.finish(call_id, error=str(error))

            asyncio.create_task(run())

        request = SmallWebRTCRequest(sdp=offer["sdp"], type=offer["type"])
        reply = await self.webrtc.handle_web_request(request=request, webrtc_connection_callback=on_connection)
        return {**reply, "call_id": call_id}

    def view(self, call_id: str) -> dict:
        return self.calls[call_id]


def handler(line: Line) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, status: int, data: dict) -> None:
            self.send(status, json.dumps(data).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a foreign page could otherwise open the microphone
            line or spend model credit."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            path = urlsplit(self.path).path
            if path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if path == "/api/state":
                return self.send_json(200, line.state())
            call_id = path.removeprefix("/api/run/")
            if path.startswith("/api/run/") and call_id in line.calls:
                return self.send_json(200, line.view(call_id))
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            if line.busy and not line.calls[line.busy]["done"]:
                return self.send_json(409, {"error": "The line is on a call. Wait for it to finish."})
            if self.path == "/api/offer" and body.get("sdp"):
                try:
                    return self.send_json(200, line.answer(body))
                except SystemExit as error:  # no key set: say so on the page
                    return self.send_json(400, {"error": str(error)})
            self.send(404, b"not found", "text/plain")

    return Handler


def serve(port: int = 8016) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), handler(Line()))
    print(f"Kitchen display on http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()

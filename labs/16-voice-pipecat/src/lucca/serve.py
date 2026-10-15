"""`phoneline serve`: Lucca Brick Oven's kitchen ticket display, filled as the caller speaks.

Two kinds of call reach the same pipeline: a recorded call from data/calls, or a live call from the
browser microphone over WebRTC. Pipecat runs on one asyncio loop in a background thread; the page posts a
call, then polls it for the transcript, the ticket and each turn's latency.
"""

import asyncio
import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from lucca.caller import load_calls
from lucca.review import OUT_FILE

PAGE = Path(__file__).with_name("page.html")
LIVE_ORDER_NUMBER = 500  # live calls take ticket numbers apart from the recorded shift


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
        calls = [{"id": call["id"], "caller": call["caller"], "lines": [line["say"] for line in call["lines"]]}
                 for call in load_calls()]
        return {"menu": Menu().data, "calls": calls, "shift": shift, "busy": self.busy}

    def record(self, kind: str, caller: str) -> str:
        call_id = uuid.uuid4().hex[:8]
        self.calls[call_id] = {"kind": kind, "caller": caller, "events": [], "done": False}
        self.busy = call_id
        return call_id

    def finish(self, call_id: str, result: dict | None = None, error: str | None = None) -> None:
        call = self.calls[call_id]
        if result:
            call.update(ticket=result["ticket"], turns=result["turns"], cost=result["cost"])
        if error:
            call["error"] = error
        call["done"] = True
        if self.busy == call_id:
            self.busy = None

    def play(self, scripted: dict) -> str:
        """Play a recorded call through the pipeline, scored against the order keyed from it."""
        from lucca.pipeline import run_call
        from lucca.till import check

        call_id = self.record("recorded", scripted["caller"])
        events = self.calls[call_id]["events"]

        async def work():
            try:
                result = await run_call(scripted, 400, events.append)
                ok, why = check(result["ticket"], scripted["keyed"])
                self.calls[call_id]["verdict"] = {"ok": ok, "why": why}
                self.finish(call_id, result)
            # A missing key raises SystemExit, which Exception does not catch; the page would poll forever.
            except (Exception, SystemExit) as error:  # shown on the page as a message, not a hang
                self.finish(call_id, error=str(error))

        asyncio.run_coroutine_threadsafe(work(), self.loop)
        return call_id

    def answer(self, offer: dict) -> dict:
        """Answer a browser's WebRTC offer and start a live call on the connection it opens."""
        return asyncio.run_coroutine_threadsafe(self.connect(offer), self.loop).result(timeout=60)

    async def connect(self, offer: dict) -> dict:
        from pipecat.transports.base_transport import TransportParams
        from pipecat.transports.smallwebrtc.request_handler import SmallWebRTCRequest, SmallWebRTCRequestHandler
        from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
        from pipecat.workers.runner import WorkerRunner

        from lucca.pipeline import OrderLine, thinking
        from lucca.till import Till

        # The model is built before the call opens, so a missing key fails the offer with a message.
        llm = thinking()
        self.webrtc = self.webrtc or SmallWebRTCRequestHandler()
        call_id = self.record("live", "browser caller")
        events = self.calls[call_id]["events"]

        async def on_connection(connection) -> None:
            transport = SmallWebRTCTransport(webrtc_connection=connection, params=TransportParams(
                audio_in_enabled=True, audio_out_enabled=True, audio_in_sample_rate=16000, audio_out_sample_rate=24000))
            order = OrderLine(transport, Till(), LIVE_ORDER_NUMBER, llm, events.append)

            @transport.event_handler("on_client_connected")
            async def greet(transport, client) -> None:
                await order.greet()

            @transport.event_handler("on_client_disconnected")
            async def hang_up(transport, client) -> None:
                await order.hang_up()

            async def run() -> None:
                try:
                    runner = WorkerRunner(handle_sigint=False)
                    await runner.add_workers(order.worker)
                    await runner.run()
                    self.finish(call_id, order.report())
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
            if self.path == "/api/call":
                scripted = next((call for call in load_calls() if call["id"] == body.get("id")), None)
                if not scripted:
                    return self.send_json(404, {"error": "No recorded call with that id."})
                return self.send_json(200, {"call_id": line.play(scripted)})
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

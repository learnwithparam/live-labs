"""`catalog serve`: one page that fills the catalogue both ways and shows the cost, the score and who needs a person."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from catalog.fill import batch_lines, one_lines, run_lines, settle
from catalog.score import load_products, load_supplied, tally, why_a_person

PAGE = Path(__file__).with_name("page.html")


class Shelf:
    """The catalogue, the retailer's sample, the model, and a checkpoint per way of filling it."""

    def __init__(self, catalogue: Path, supplied: Path, client, model: str, out: Path = Path("out/page"), size: int = 24):
        self.products = load_products(catalogue)
        self.supplied = load_supplied(supplied)
        self.client, self.model, self.out, self.size = client, model, out, size

    def lines(self, way: str) -> list[dict]:
        return one_lines(self.products, self.model) if way == "one" else batch_lines(self.products, self.model, self.size)

    def fill(self, way: str) -> dict:
        """Fill every product one way. The checkpoint makes a second press free: nothing is sent twice."""
        lines, checkpoint = self.lines(way), self.out / f"{way}.jsonl"
        sent = run_lines(lines, self.client, checkpoint)
        filled, cost = settle(checkpoint, {product.sku: product for product in self.products}, self.client, self.model)
        result = tally(self.products, filled, self.supplied, cost)
        person = {product.sku: why_a_person(product, filled.get(product.sku)) for product in self.products if product.sku in result.person}
        return {
            "requests": len(lines),
            "sent": sent["sent"],
            "cost": result.cost,
            "per_thousand": result.per_thousand,
            "right": result.right,
            "total": result.total,
            "filled": result.filled,
            "person": person,
            "attributes": {sku: attributes.model_dump() for sku, attributes in filled.items()},
        }


def handler(shelf: Shelf) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def send(self, status: int, body: bytes, kind: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_json(self, data: object) -> None:
            self.send(200, json.dumps(data).encode(), "application/json")

        def log_message(self, *args) -> None:
            pass

        def local(self) -> bool:
            """Only this machine's own page may call: a rebound Host or a foreign Origin could spend the key."""
            hosts = {f"{name}:{self.server.server_port}" for name in ("127.0.0.1", "localhost")}
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            return host in hosts and origin in (None, f"http://{host}")

        def do_GET(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            if self.path == "/":
                return self.send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if self.path == "/api/shelf":
                products = [vars(product) for product in shelf.products]
                return self.send_json({"model": shelf.model, "size": shelf.size, "products": products, "supplied": shelf.supplied})
            self.send(404, b"not found", "text/plain")

        def do_POST(self) -> None:
            if not self.local():
                return self.send(403, b"forbidden", "text/plain")
            way = self.path.removeprefix("/api/fill/")
            if way not in ("one", "packed"):
                return self.send(404, b"not found", "text/plain")
            self.send_json(shelf.fill(way))

    return Handler


def serve(shelf: Shelf, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), handler(shelf))

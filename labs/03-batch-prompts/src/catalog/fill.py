"""Fill product attributes one product per call, or many per call from a batch file, with a checkpoint."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from catalog.schema import Attributes, Pack

INSTRUCTIONS = (
    "You fill the attributes a grocery storefront filters on, from a retailer's product title and description. "
    "category is one of produce, dairy, bakery, meat_seafood, pantry, snacks, beverages, frozen, household, "
    "personal_care. size_value and size_unit come from the size in the title or the description: grams (g) for "
    "weight, millilitres (ml) for liquid, count for packs. Convert ounces to grams (1 oz = 28.35 g) and fluid ounces "
    "to millilitres (1 fl oz = 29.57 ml), rounded to a whole number. organic is true only when the text says organic. "
    "vegan is no when the ingredients name any animal product (milk, butter, cheese, egg, meat, fish, honey), yes "
    "when ingredients are listed and none is, unknown for a food with no ingredients listed, and not_food for "
    "household and personal care products. Copy each sku exactly. Reply with JSON only."
)
ENDPOINT = "/v1/chat/completions"


@dataclass
class Product:
    sku: str
    title: str
    description: str

    def line(self) -> str:
        return f"{self.sku} | {self.title} | {self.description}"


def schema_format(model: type) -> dict:
    return {"type": "json_schema", "json_schema": {"name": model.__name__.lower(), "schema": model.model_json_schema()}}


def one_body(product: Product, model: str) -> dict:
    """The obvious way: the instructions and one product, sent once per product."""
    messages = [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": product.line()}]
    return {"model": model, "messages": messages, "temperature": 0, "response_format": schema_format(Attributes)}


def pack_body(products: list[Product], model: str) -> dict:
    """Many products under one copy of the instructions: the instructions are paid for once per pack."""
    listing = "\n".join(p.line() for p in products)
    messages = [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": f"One line per product: sku, title and description, split by a bar.\n{listing}"}]
    return {"model": model, "messages": messages, "temperature": 0, "response_format": schema_format(Pack)}


def one_lines(products: list[Product], model: str) -> list[dict]:
    return [{"custom_id": f"one-{p.sku}", "method": "POST", "url": ENDPOINT, "body": one_body(p, model)} for p in products]


def batch_lines(products: list[Product], model: str, size: int) -> list[dict]:
    """The batch file: one request per pack, in the OpenAI batch format every batch API reads."""
    packs = [products[i : i + size] for i in range(0, len(products), size)]
    return [{"custom_id": f"pack-{n:04d}", "method": "POST", "url": ENDPOINT, "body": pack_body(pack, model)} for n, pack in enumerate(packs)]


def parse(reply: str, wanted: list[str]) -> tuple[list[Attributes], list[str]]:
    """The products a reply filled, and the skus it dropped or mangled. A dropped sku is retried on its own."""
    try:
        filled = Pack.model_validate_json(reply).products
    except ValidationError:
        try:
            filled = [Attributes.model_validate_json(reply)]
        except ValidationError:
            return [], wanted
    kept = [a for a in filled if a.sku in wanted]
    seen = {a.sku for a in kept}
    return kept, [sku for sku in wanted if sku not in seen]


def skus_in(body: dict) -> list[str]:
    text = body["messages"][-1]["content"]
    return [line.split(" | ", 1)[0] for line in text.splitlines() if " | " in line]


def send(client, body: dict) -> tuple[str, float]:
    response = client.chat.completions.create(**body)
    return response.choices[0].message.content, getattr(response.usage, "cost", None) or 0.0


def run_lines(lines: list[dict], client, done_path: Path, workers: int = 8) -> dict:
    """Send every request in the batch file not already in the checkpoint, and append each result as it lands.

    A crash at pack 3,000 of 40,000 costs one pack, not the night: rerun and it skips what is done."""
    done_path.parent.mkdir(parents=True, exist_ok=True)
    done = {row["custom_id"] for row in read_jsonl(done_path)}
    todo = [line for line in lines if line["custom_id"] not in done]

    def one(line: dict) -> dict:
        reply, cost = send(client, line["body"])
        return {"custom_id": line["custom_id"], "reply": reply, "cost": cost, "skus": skus_in(line["body"])}

    with ThreadPoolExecutor(workers) as pool, done_path.open("a") as out:
        for row in pool.map(one, todo):
            out.write(json.dumps(row) + "\n")
            out.flush()
    return {"sent": len(todo), "skipped": len(done & {line["custom_id"] for line in lines})}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


def submit(lines: list[dict], client) -> str:
    """Hand the same file to a provider's batch API (OpenAI-compatible files and batches endpoints)."""
    data = "".join(json.dumps(line) + "\n" for line in lines).encode()
    uploaded = client.files.create(file=("batch.jsonl", data), purpose="batch")
    return client.batches.create(input_file_id=uploaded.id, endpoint=ENDPOINT, completion_window="24h").id


def collect(batch_id: str, client) -> list[dict] | None:
    """The provider's results as checkpoint rows, or None while the batch is still running."""
    batch = client.batches.retrieve(batch_id)
    if batch.status != "completed":
        return None
    rows = []
    for line in client.files.content(batch.output_file_id).text.splitlines():
        result = json.loads(line)
        body = result["response"]["body"]
        rows.append({"custom_id": result["custom_id"], "reply": body["choices"][0]["message"]["content"], "cost": body.get("usage", {}).get("cost", 0.0)})
    return rows



def filled_in(done_path: Path) -> tuple[dict[str, Attributes], float, list[str]]:
    """What the checkpoint holds: the products filled, what they cost, and the skus no reply filled."""
    filled, cost, asked = {}, 0.0, []
    for row in read_jsonl(done_path):
        kept, _ = parse(row["reply"], row["skus"])
        filled |= {a.sku: a for a in kept}
        cost += row["cost"]
        asked += row["skus"]
    return filled, cost, [sku for sku in dict.fromkeys(asked) if sku not in filled]


def settle(done_path: Path, products: dict[str, Product], client, model: str) -> tuple[dict[str, Attributes], float]:
    """Retry each sku a pack dropped once, on its own, through the same checkpoint; what still fails goes to a person."""
    _, _, dropped = filled_in(done_path)
    tried = {row["custom_id"] for row in read_jsonl(done_path)}
    retries = [{"custom_id": f"retry-{sku}", "method": "POST", "url": ENDPOINT, "body": one_body(products[sku], model)} for sku in dropped if f"retry-{sku}" not in tried]
    if retries:
        run_lines(retries, client, done_path)
    filled, cost, _ = filled_in(done_path)
    return filled, cost

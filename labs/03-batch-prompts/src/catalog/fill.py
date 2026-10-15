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


def strict_schema(node):
    """Pydantic's schema, tightened to the rules OpenAI's strict mode enforces.

    Every property is listed as required and no extra keys are allowed, or OpenAI models reject the
    request. The pydantic model still checks the values afterwards.
    """
    if isinstance(node, dict):
        if "properties" in node:
            node["required"] = list(node["properties"])
            node["additionalProperties"] = False
        node.pop("default", None)
        for value in node.values():
            strict_schema(value)
    elif isinstance(node, list):
        for value in node:
            strict_schema(value)
    return node


def schema_format(model: type) -> dict:
    schema = strict_schema(model.model_json_schema())
    return {"type": "json_schema", "json_schema": {"name": model.__name__.lower(), "schema": schema}}


def one_body(product: Product, model: str) -> dict:
    """The obvious way: the instructions and one product, sent once per product."""
    messages = [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": product.line()}]
    return {"model": model, "messages": messages, "temperature": 0, "response_format": schema_format(Attributes)}


def pack_body(products: list[Product], model: str) -> dict:
    """Many products under one copy of the instructions: the instructions are paid for once per pack."""
    # 24 products become 24 short lines. The long INSTRUCTIONS above are sent once for all of them.
    listing = "\n".join(product.line() for product in products)
    messages = [{"role": "system", "content": INSTRUCTIONS}, {"role": "user", "content": f"One line per product: sku, title and description, split by a bar.\n{listing}"}]
    # The reply must be a list of products, each with its sku, so every answer can be matched to a product.
    return {"model": model, "messages": messages, "temperature": 0, "response_format": schema_format(Pack)}


def one_lines(products: list[Product], model: str) -> list[dict]:
    return [{"custom_id": f"one-{product.sku}", "method": "POST", "url": ENDPOINT, "body": one_body(product, model)} for product in products]


def batch_lines(products: list[Product], model: str, size: int) -> list[dict]:
    """The batch file: one request per pack, each line keyed by `custom_id`."""
    # Cut the catalogue into packs: 240 products at 24 a pack is 10 requests.
    packs = [products[start : start + size] for start in range(0, len(products), size)]
    # Each line is the format batch APIs read. custom_id is how a result finds its way back to its pack.
    return [{"custom_id": f"pack-{number:04d}", "method": "POST", "url": ENDPOINT, "body": pack_body(pack, model)} for number, pack in enumerate(packs)]


def parse(reply: str, wanted: list[str]) -> tuple[list[Attributes], list[str]]:
    """The products a reply filled, and the skus it dropped or mangled. A dropped sku is retried on its own."""
    try:
        filled = Pack.model_validate_json(reply).products
    except ValidationError:
        try:
            filled = [Attributes.model_validate_json(reply)]
        except ValidationError:
            return [], wanted
    kept = [attributes for attributes in filled if attributes.sku in wanted]
    seen = {attributes.sku for attributes in kept}
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
    # The checkpoint is a file of finished requests. Read it first, so nothing finished is paid for twice.
    done = {row["custom_id"] for row in read_jsonl(done_path)}
    todo = [line for line in lines if line["custom_id"] not in done]

    def one(line: dict) -> dict:
        reply, cost = send(client, line["body"])
        return {"custom_id": line["custom_id"], "reply": reply, "cost": cost, "skus": skus_in(line["body"])}

    with ThreadPoolExecutor(workers) as pool, done_path.open("a") as out:
        for row in pool.map(one, todo):
            # Append each result the moment it lands, and flush, so a crash keeps every result before it.
            out.write(json.dumps(row) + "\n")
            out.flush()
    return {"sent": len(todo), "skipped": len(done & {line["custom_id"] for line in lines})}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()] if path.exists() else []


# Claude Haiku 5.5 on the Message Batches API, dollars per token: half the live price
# (platform.claude.com/docs/en/about-claude/pricing, prompts under 100K tokens).
BATCH_PRICE_IN, BATCH_PRICE_OUT = 0.05e-6, 0.25e-6


def anthropic_params(body: dict, model: str) -> dict:
    """One line of the batch file as a Messages request. The reply schema becomes a tool the model must call."""
    system = next(message["content"] for message in body["messages"] if message["role"] == "system")
    user = [message for message in body["messages"] if message["role"] != "system"]
    schema = body["response_format"]["json_schema"]
    tool = {"name": schema["name"], "description": "Record the filled attributes.", "input_schema": schema["schema"]}
    return {"model": model, "max_tokens": 8192, "system": system, "messages": user,
            "tools": [tool], "tool_choice": {"type": "tool", "name": tool["name"]}}


def submit(lines: list[dict], client, model: str, limit: int) -> str:
    """Hand the first `limit` lines of the file to Anthropic's Message Batches API."""
    # The same lines as the live run. The provider runs them within a day and charges half.
    requests = [{"custom_id": line["custom_id"], "params": anthropic_params(line["body"], model)} for line in lines[:limit]]
    return client.messages.batches.create(requests=requests).id


def collect(batch_id: str, client) -> tuple[list[dict], list[str]] | None:
    """The batch's results as checkpoint rows and the reasons any request failed, or None while it is still
    running. A failed request is left out of the rows, so the next run retries it."""
    # A batch is not a call you wait on. Ask whether it has ended, and come back later if not.
    if client.messages.batches.retrieve(batch_id).processing_status != "ended":
        return None
    rows, errors = [], []
    for result in client.messages.batches.results(batch_id):
        if result.result.type != "succeeded":
            error = getattr(result.result, "error", None)
            errors.append(f"{result.custom_id}: {error.error.message if error else result.result.type}")
            continue
        message = result.result.message
        call = next(block for block in message.content if block.type == "tool_use")
        cost = message.usage.input_tokens * BATCH_PRICE_IN + message.usage.output_tokens * BATCH_PRICE_OUT
        rows.append({"custom_id": result.custom_id, "reply": json.dumps(call.input), "cost": cost})
    return rows, errors


def filled_in(done_path: Path) -> tuple[dict[str, Attributes], float, list[str]]:
    """What the checkpoint holds: the products filled, what they cost, and the skus no reply filled."""
    filled, cost, asked = {}, 0.0, []
    for row in read_jsonl(done_path):
        kept, _ = parse(row["reply"], row["skus"])
        filled |= {attributes.sku: attributes for attributes in kept}
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

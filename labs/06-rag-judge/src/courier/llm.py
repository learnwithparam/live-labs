"""One structured call: the model must return JSON that fits a pydantic model, or the call counts as failed.

Every step in this lab (drafting, the grounding check, the judge) reads the model's reply as data,
so a reply that does not parse is treated as the cautious outcome by the caller, never as a pass.
"""

from pydantic import BaseModel, ValidationError


def strict_schema(node):
    """Pydantic's schema, tightened to the rules OpenAI's strict mode enforces.

    Every property must be listed as required and no extra keys allowed, so a field with a default
    becomes one the model always fills. The pydantic model still checks the values afterwards.
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


def ask(client, model: str, system: str, user: str, shape: type[BaseModel]) -> tuple[BaseModel | None, float]:
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0,
        # The schema goes to the provider, which constrains decoding to it where it can.
        response_format={"type": "json_schema", "json_schema": {"name": shape.__name__.lower(), "schema": strict_schema(shape.model_json_schema())}},
    )
    # OpenRouter reports what each call cost; other endpoints leave it out.
    cost = getattr(response.usage, "cost", None) or 0.0
    try:
        return shape.model_validate_json(response.choices[0].message.content or ""), cost
    except ValidationError:
        return None, cost

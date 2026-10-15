"""The audit agent: one loop, four tools, and a context policy from `context.py`.

The loop and the tools are the same for both policies, so any difference in the bill comes from how
the context is built. Each step records what the provider reported: prompt tokens, how many of them it
served from its cache, and what it charged.
"""

import json
from datetime import datetime
from typing import Callable

from halyard.books import TOLERANCE, Finding, billed, contract, invoices, owed, rated_text
from halyard.config import PRICES
from halyard.context import Rules, build, first_change

TASK = "Audit every carrier invoice for September 2026."
MAX_STEPS = 60  # sixteen invoices take about 34 steps; the rest is room for a retry or two
TEMPERATURE = 0  # the same audit twice should cost the same, so the policies are compared like for like


def tool(name: str, description: str, **properties: dict) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}}}


# One list in one order on every step. A tool list that changes moves the start of every request.
TOOLS = [
    tool("list_invoices", "List this month's carrier invoices: id, carrier, invoice number and total."),
    tool("read_invoice", "Read one invoice beside the rate engine's contract price for the same shipment.",
         invoice={"type": "string", "description": "The invoice id, like INV-04."}),
    tool("record_finding", "Record the finding for one invoice. Recording again replaces the earlier finding.",
         invoice={"type": "string", "description": "The invoice id."},
         verdict={"type": "string", "enum": ["approve", "dispute"]},
         duplicate_of={"type": "string", "description": "The earlier invoice id for this same shipment, or an empty string."},
         reason={"type": "string", "description": "One sentence the carrier's billing team can act on: which line is wrong and why."}),
    tool("finish", "End the audit once every invoice has a finding.",
         summary={"type": "string", "description": "One line for the accounts payable lead."}),
]


class Desk:
    """The accounts payable desk the agent works at: the month's invoices and the findings so far."""

    def __init__(self):
        self.terms, self.invoices = contract(), {invoice["id"]: invoice for invoice in invoices()}
        self.findings: dict[str, Finding] = {}
        self.summary: str | None = None

    def call(self, tool_name: str, args: dict, rules: Rules) -> str:
        if tool_name == "list_invoices":
            return "\n".join(f"{invoice['id']}  {invoice['number']}  {invoice['carrier']}  "
                             f"{sum(line['amount'] for line in invoice['lines']):.2f} EUR" for invoice in self.invoices.values())
        if tool_name == "read_invoice":
            invoice = self.invoices.get(args.get("invoice", ""))
            return rated_text(invoice, self.terms) if invoice else f"No invoice {args.get('invoice')!r}. Call list_invoices."
        if tool_name == "record_finding":
            return self.record(args, rules)
        if tool_name == "finish":
            missing = [invoice_id for invoice_id in self.invoices if invoice_id not in self.findings]
            if missing:
                # The engineered agent always sees `finish`, so finish itself says why it cannot end yet.
                return f"Not finished: {', '.join(missing)} have no finding yet."
            self.summary = args.get("summary", "")
            return "Audit closed."
        return f"No tool {tool_name!r}."

    def record(self, args: dict, rules: Rules) -> str:
        invoice_id = args.get("invoice", "")
        if invoice_id not in self.invoices or args.get("verdict") not in ("approve", "dispute"):
            return "Give an invoice id from list_invoices and a verdict of approve or dispute."
        invoice = self.invoices[invoice_id]
        duplicate_of = (args.get("duplicate_of") or "").strip()
        # The code does the subtraction; contract clause 6 makes a duplicate's whole total the overcharge.
        overcharge = billed(invoice) if duplicate_of else round(billed(invoice) - owed(invoice, self.terms), 2)
        finding = Finding(invoice_id, args["verdict"], overcharge if args["verdict"] == "dispute" else 0.0,
                          args.get("reason", ""))
        self.findings[invoice_id] = finding
        said = f"Recorded {invoice_id}: {finding.verdict}" + (f", {finding.overcharge:.2f} EUR over." if finding.overcharge else ".")
        if not rules.recite:
            return said
        # Recitation: the plan restated at the very end of the context, where the model attends most.
        left = [invoice for invoice in self.invoices if invoice not in self.findings]
        return (f"{said} {len(self.findings)} of {len(self.invoices)} invoices have a finding. "
                + (f"Next: {left[0]}." if left else "All recorded: call finish."))


def usage_of(response, model: str) -> dict:
    """What the provider reported for one call. The bill is the truth; list prices are the fallback."""
    usage = response.usage
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    # OpenRouter adds the billed cost when asked with usage.include; other providers do not.
    billed = (getattr(usage, "model_extra", None) or {}).get("cost")
    price_in, price_cached, price_out = PRICES.get(model, (0.0, 0.0, 0.0))
    listed = ((usage.prompt_tokens - cached) * price_in + cached * price_cached + usage.completion_tokens * price_out) / 1e6
    return {"prompt_tokens": usage.prompt_tokens, "cached_tokens": cached, "completion_tokens": usage.completion_tokens,
            "cost": float(billed) if billed is not None else listed, "billed": billed is not None}


def audit(rules: Rules, client, model: str, on_step: Callable[[dict], None] = lambda step: None,
          clock: Callable[[], datetime] = datetime.now) -> dict:
    """Run the month's audit to `finish`, to a reply in words, or to MAX_STEPS."""
    desk = Desk()
    turns: list[list[dict]] = [[{"role": "user", "content": TASK}]]
    steps, previous = [], None
    while len(steps) < MAX_STEPS and desk.summary is None:
        request = build(rules, TOOLS, turns, len(desk.findings), len(desk.invoices), clock())
        response = client.chat.completions.create(
            model=model, temperature=TEMPERATURE, parallel_tool_calls=False, extra_body={"usage": {"include": True}},
            **request)
        said = response.choices[0].message
        step = {"step": len(steps) + 1, **usage_of(response, model), "changed": first_change(previous, request),
                "messages": len(request["messages"]), "calls": []}
        previous = request
        turn = [{"role": "assistant", "content": said.content, "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in said.tool_calls or []]} if said.tool_calls else {"role": "assistant", "content": said.content or ""}]
        for call in said.tool_calls or []:
            result = desk.call(call.function.name, json.loads(call.function.arguments or "{}"), rules)
            turn.append({"role": "tool", "tool_call_id": call.id, "content": result})
            step["calls"].append({"tool": call.function.name, "args": json.loads(call.function.arguments or "{}"), "result": result})
        # Appended, never edited: the next request starts with everything this one sent.
        turns.append(turn)
        steps.append(step)
        on_step(step)
        if not said.tool_calls:
            break
    return {"steps": steps, "findings": {invoice_id: vars(finding) for invoice_id, finding in desk.findings.items()},
            "summary": desk.summary}

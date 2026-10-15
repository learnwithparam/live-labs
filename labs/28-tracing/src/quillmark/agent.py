"""Quillmark's helpdesk agent: draft a reply with an order lookup, or summarise a thread for handover.

Each step is a span under one `invoke_agent` span, so a trace has the shape of the run: retrieve,
model call, tool call, model call. The model-call spans come from the OpenAI instrumentation; this
module only adds the steps the library cannot see.

The app also writes the log lines a team writes before it has tracing: one per model call, with tokens
and timing but no trace context. Finance tries to bill from those in `attribution.py`.
"""

import json
import re
import threading
import time
from datetime import datetime, timezone

from quillmark.telemetry import attributed_to

ORDER_TOOL = {"type": "function", "function": {
    "name": "order_lookup",
    "description": "Read one order from the customer's order system: item, status and dates.",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]}}}
INSTRUCTIONS = {
    "reply-draft": ("Draft the reply a support agent at {name} will send for this ticket. Look the order up "
                    "with order_lookup before promising anything. Use only the help-center articles below and "
                    "the order record. Under 120 words, plain text, signed 'The {name} team'.\n\n{articles}"),
    "thread-summary": ("Summarise this ticket thread for the next agent on shift: what the customer wants, what "
                       "was promised and what is still open. At most 60 words."),
    "weekly-digest": ("Write the weekly support digest for the support lead from these numbers: three short "
                      "bullet points, then one recommendation."),
}


class AppLog:
    """The log lines a team has before tracing: JSON events with no trace or request id."""

    def __init__(self):
        self.events: list[dict] = []
        self.lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        with self.lock:
            self.events.append({"at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                                "event": event, **fields})


def thread_text(run: dict) -> str:
    return f"Subject: {run['subject']}\n\n" + "\n\n".join(
        f"{message['name']} ({message['from']}): {message['text']}" for message in run["messages"])


def retrieve(account: dict, run: dict, tracer) -> list[dict]:
    """Rank the account's help-center articles by shared words with the ticket; the top two go in the prompt."""
    with tracer.start_as_current_span("retrieve help_center") as span:
        words = set(re.findall(r"[a-z]{4,}", thread_text(run).lower()))
        ranked = sorted(account["articles"], key=lambda article: -len(
            words & set(re.findall(r"[a-z]{4,}", (article["title"] + " " + article["text"]).lower()))))
        chosen = ranked[:2]
        span.set_attribute("retrieval.document_ids", [article["id"] for article in chosen])
        return chosen


def order_lookup(account: dict, order_id: str, call_id: str, tracer, sleep=time.sleep) -> str:
    """A tool call is its own span with the GenAI tool attributes, so a slow upstream shows as a slow step.

    Each account's order system answers at its own speed; Ferrovia's ERP sits behind a VPN.
    """
    with tracer.start_as_current_span("execute_tool order_lookup") as span:
        span.set_attributes({"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "order_lookup",
                             "gen_ai.tool.type": "function", "gen_ai.tool.call.id": call_id,
                             "order_system.name": account["order_system"]["name"]})
        sleep(account["order_system"]["latency_ms"] / 1000)
        order = account["orders"].get(order_id)
        return json.dumps(order or {"error": f"No order {order_id}"})


def chat(client, model: str, app_log: AppLog, **request):
    """One model call. The instrumentation wraps `create` in a span; the log line is the pre-tracing habit."""
    started = time.perf_counter()
    response = client.chat.completions.create(model=model, max_tokens=400, extra_body={"usage": {"include": True}},
                                              **request)
    app_log.write("llm.call", model=model, response_id=response.id, input_tokens=response.usage.prompt_tokens,
                  output_tokens=response.usage.completion_tokens,
                  duration_ms=round((time.perf_counter() - started) * 1000))
    return response


def run_agent(run: dict, account: dict | None, client, model: str, tracer, app_log: AppLog, sleep=time.sleep) -> dict:
    """The agent itself. It never reads the customer: the baggage already put it on every span."""
    with tracer.start_as_current_span(f"invoke_agent {run['feature']}") as root:
        root.set_attributes({"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": run["feature"],
                             "run.id": run["id"]})
        system = INSTRUCTIONS[run["feature"]]
        messages = [{"role": "user", "content": thread_text(run)}]
        if run["feature"] != "reply-draft":
            response = chat(client, model, app_log, messages=[{"role": "system", "content": system}, *messages])
        else:
            articles = retrieve(account, run, tracer)
            system = system.format(name=account["name"], articles="\n".join(
                f"[{article['id']}] {article['title']}: {article['text']}" for article in articles))
            messages = [{"role": "system", "content": system}, *messages]
            # The first turn must look the order up, so every draft has the same shape to compare.
            first = chat(client, model, app_log, messages=messages, tools=[ORDER_TOOL], tool_choice="required")
            said = first.choices[0].message
            messages.append({"role": "assistant", "content": said.content, "tool_calls": [
                {"id": call.id, "type": "function", "function": {"name": call.function.name,
                                                                 "arguments": call.function.arguments}}
                for call in said.tool_calls or []]})
            for call in said.tool_calls or []:
                order_id = json.loads(call.function.arguments or "{}").get("order_id", "")
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": order_lookup(account, order_id, call.id, tracer, sleep)})
            response = chat(client, model, app_log, messages=messages, tools=[ORDER_TOOL], tool_choice="none")
        trace_id = format(root.get_span_context().trace_id, "032x")
        return {"run": run["id"], "trace_id": trace_id, "reply": response.choices[0].message.content or ""}


def handle_request(run: dict, account: dict, client, model: str, tracer, app_log: AppLog, sleep=time.sleep) -> dict:
    """The web path: the signed-in account and the feature's route are known here, so they go into baggage."""
    started = time.perf_counter()
    with attributed_to(run["customer"], run["feature"]):
        result = run_agent(run, account, client, model, tracer, app_log, sleep)
    # The request line names the customer, but nothing ties it to the model calls logged inside it.
    app_log.write("request.end", customer=run["customer"], feature=run["feature"],
                  duration_ms=round((time.perf_counter() - started) * 1000))
    return result


def handle_job(run: dict, client, model: str, tracer, app_log: AppLog) -> dict:
    """The scheduled worker path. It has no signed-in account, and nobody added the baggage here."""
    started = time.perf_counter()
    result = run_agent(run, None, client, model, tracer, app_log)
    app_log.write("job.end", job=run["job"], duration_ms=round((time.perf_counter() - started) * 1000))
    return result

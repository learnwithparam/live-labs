"""A scripted stand-in for the model, so the gate, DeepEval's scoring and the page run with no key.

It reads the release the way a model would: if the tool descriptions stop saying approval is
required, it skips approval; if the prompt says not to repeat figures, its replies drop them. That
gives the real gate real regressions to catch without a network call.
"""

import json
from types import SimpleNamespace

# The calls a careful assistant makes for each golden request, then the reply it writes.
SCRIPTS = {
    "G01": ([("search_products", {"query": "guji 250"}), ("get_inventory", {"sku": "HR-GUJI-250"})],
            "You have 34 bags of the Guji 250 g left."),
    "G02": ([("get_order", {"order_id": "1047"})], "Order 1047 is in transit with UPS, tracking 1Z84F2E10319482277."),
    "G03": ([("get_sales", {"start_date": "2026-10-05", "end_date": "2026-10-11"})],
            "From 5 to 11 October you sold $4,318.00 across 105 orders."),
    "G04": ([("list_low_stock", {})], "Running low: Guji 1 kg, the decaf bags and filter papers."),
    "G05": ([("search_products", {"query": "kenya nyeri 1 kg"}),
             ("request_approval", {"action": "update_price", "summary": "Set HR-KENYA-1K to $42.00"}),
             ("update_price", {"sku": "HR-KENYA-1K", "price": 42})], "The Kenya Nyeri AA 1 kg now costs $42.00."),
    "G06": ([("get_order", {"order_id": "1052"}),
             ("request_approval", {"action": "refund_order", "summary": "Refund $68.00 on order 1052"}),
             ("refund_order", {"order_id": "1052", "amount": 68})], "Refunded $68.00 on order 1052."),
    "G07": ([("search_products", {"query": "decaf"}),
             ("request_approval", {"action": "create_discount", "summary": "DECAF15, 15% off both decaf bags"}),
             ("create_discount", {"code": "DECAF15", "percent": 15, "skus": ["HR-DECAF-250", "HR-DECAF-1K"],
                                  "starts": "2026-10-17", "ends": "2026-10-18"})],
            "DECAF15 now takes 15% off both decaf bags this weekend."),
    "G08": ([("get_order", {"order_id": "1058"})], "Order 1058 was already refunded in full, so I left it."),
    "G09": ([("get_order", {"order_id": "1061"})], "Order 1061 was cancelled, so it will not ship."),
    "G10": ([], "I can't delete customers: no tool I have does that."),
    "G11": ([("search_products", {"query": "guji 1 kg"}), ("get_inventory", {"sku": "HR-GUJI-1K"})],
            "The Guji 1 kg costs $52.00 and 6 are left."),
    "G12": ([("get_order", {"order_id": "1055"}),
             ("request_approval", {"action": "refund_order", "summary": "Refund $10.00 on order 1055"}),
             ("refund_order", {"order_id": "1055", "amount": 10})], "Refunded $10.00 on order 1055."),
}


def approval_required(tools: list[dict]) -> bool:
    description = next(tool["function"]["description"] for tool in tools if tool["function"]["name"] == "request_approval")
    return "Required" in description


class FakeOpenAI:
    """`client.chat.completions.create` for the tool loop. `golden` maps each request to its case id.

    `skip` names cases whose first step this model leaves out, the way a weaker model can, and
    `fail` names cases whose calls raise, to show an errored run blocks the release.
    """

    def __init__(self, golden: list[dict], skip: tuple = (), fail: tuple = ()):
        self.case_for = {case["request"]: case["id"] for case in golden}
        self.skip, self.fail = skip, fail
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, model, messages, tools, **_):
        case_id = self.case_for[messages[1]["content"]]
        if case_id in self.fail:
            raise TimeoutError("Request timed out.")
        steps, reply = SCRIPTS[case_id]
        if not approval_required(tools):
            steps = [step for step in steps if step[0] != "request_approval"]
        if case_id in self.skip:
            steps = steps[1:]
        if "Do not repeat figures" in messages[0]["content"]:
            reply = "Done. The admin page has the details."
        done = sum(message["role"] == "tool" for message in messages)
        usage = SimpleNamespace(prompt_tokens=400, completion_tokens=40, prompt_tokens_details=None,
                                model_extra={"cost": 0.0001})
        if done < len(steps):
            name, arguments = steps[done]
            call = SimpleNamespace(id=f"call-{done}", function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))
            message = SimpleNamespace(content="", tool_calls=[call])
        else:
            message = SimpleNamespace(content=reply, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)

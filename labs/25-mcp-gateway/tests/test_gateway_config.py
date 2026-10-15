"""The gateway's config is generated from the list, so its rules can be read and checked without running it."""

import json

import pytest

from basalt import catalogue, gateway


def rendered() -> dict:
    return gateway.render(catalogue.load())


def route() -> dict:
    return rendered()["binds"][0]["listeners"][0]["routes"][0]


def test_only_approved_servers_get_a_route():
    targets = route()["backends"][0]["mcp"]["targets"]
    assert [target["name"] for target in targets] == list(catalogue.load())
    assert "snippets" not in json.dumps(targets)


def test_every_pinned_tool_has_exactly_one_team_rule():
    rules = route()["policies"]["mcpAuthorization"]["rules"]
    servers = catalogue.load()
    assert len(rules) == sum(len(server.tools) for server in servers.values())
    assert 'mcp.tool.target == "payments" && mcp.tool.name == "refund_payment" && jwt.team in ["risk"]' in rules


def test_the_pin_check_fails_closed_and_tokens_stop_at_the_gateway():
    policies = route()["policies"]
    processor = policies["mcpGuardrails"]["processors"][0]
    assert processor["failureMode"] == "failClosed"
    assert processor["methods"]["tools/call"] == "request"
    assert "authorization" in processor["requestHeaders"]["disallowed"]
    assert policies["jwtAuth"]["mode"] == "strict"
    assert "backendAuth" not in json.dumps(rendered())


def test_the_access_log_becomes_one_row_per_call_with_a_plain_reason(tmp_path):
    log = tmp_path / "gateway.log"
    lines = [
        {"scope": "request", "time": "t1", "person": "ola.mensah@basaltpay.com", "team": "risk", "mcp.method.name": "tools/call",
         "mcp.target": "payments", "gen_ai.tool.name": "refund_payment"},
        {"scope": "request", "time": "t2", "person": "maya.lindqvist@basaltpay.com", "team": "support",
         "mcp.method.name": "tools/call", "mcp.target": "merchants", "gen_ai.tool.name": "lookup_merchant",
         "error": "mcp: mcpGuardrails rejected: pinned hash mismatch: merchants.lookup_merchant changed"},
        {"scope": "request", "time": "t3", "mcp.method.name": "tools/list", "person": "maya.lindqvist@basaltpay.com"},
        {"scope": "request", "time": "t4", "mcp.method.name": "tools/call", "gen_ai.tool.name": "refund_payment",
         "mcp.target": "payments", "person": "sam.okafor@partners.basaltpay.com", "error": "Unknown tool: payments_refund_payment"},
    ]
    log.write_text("\n".join(json.dumps(line) for line in lines) + "\nnot json\n")
    rows = gateway.call_log(log)
    assert [row["time"] for row in rows] == ["t1", "t2", "t4"]
    assert rows[0]["allowed"] and not rows[1]["allowed"]
    assert rows[1]["reason"] == "pinned hash mismatch: merchants.lookup_merchant changed"
    assert rows[2]["reason"] == "not on this team's tool list"
    assert gateway.plain_reason("mcpGuardrails checkRequest failed: Connection refused").endswith("(fail closed)")


def test_a_name_with_a_quote_never_reaches_a_cel_rule():
    with pytest.raises(SystemExit, match="not a plain"):
        gateway.team_rule("payments", 'refund_payment" || true || "', ["risk"])
    with pytest.raises(SystemExit, match="not a plain"):
        gateway.team_rule("payments", "refund_payment", ['risk"] || true || ["'])

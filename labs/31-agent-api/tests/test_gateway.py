"""The Envoy config the gateway runs, and the operator's numbers read back from its log."""

import json

from mosswood import gateway, gatewaylog
from mosswood.config import USER_REQUESTS_PER_WINDOW


def filters(config: dict) -> list[dict]:
    manager = config["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"]
    return manager["http_filters"]


def test_identity_headers_are_stripped_before_the_token_is_checked():
    chain = filters(gateway.envoy_config({"harlow": 40, "pemberton": 40, "ninebark": 6}))
    names = [entry["name"] for entry in chain]
    assert names == ["envoy.filters.http.cors", "strip_identity", "envoy.filters.http.jwt_authn",
                     "user_rate_limit", "tenant_rate_limit", "envoy.filters.http.router"]
    # A filter with the right name that removes nothing would let a caller send another tenant's id.
    mutations = chain[1]["typed_config"]["mutations"]["request_mutations"]
    assert {mutation["remove"] for mutation in mutations} == {"x-tenant-id", "x-user-id"}


def test_every_assistant_route_needs_a_token():
    jwt_filter = filters(gateway.envoy_config({"harlow": 40}))[2]["typed_config"]
    assert jwt_filter["rules"] == [{"match": {"prefix": "/v1/"}, "requires": {"provider_name": "mosswood_web"}}]
    provider = jwt_filter["providers"]["mosswood_web"]
    assert {entry["claim_name"] for entry in provider["claim_to_headers"]} == {"sub", "tenant"}


def test_each_tenant_gets_its_own_bucket_and_users_share_one_rule():
    config = filters(gateway.envoy_config({"harlow": 40, "ninebark": 6}))
    user, tenant = config[3]["typed_config"], config[4]["typed_config"]
    assert user["descriptors"][0]["token_bucket"]["max_tokens"] == USER_REQUESTS_PER_WINDOW
    assert {entry["entries"][0]["value"]: entry["token_bucket"]["max_tokens"] for entry in tenant["descriptors"]} == {"harlow": 40, "ninebark": 6}
    assert {header["header"]["key"] for header in tenant["response_headers_to_add"]} == {"retry-after", "x-limit-scope"}


def test_a_long_answer_is_never_cut_by_a_total_timeout():
    manager = gateway.envoy_config({"harlow": 40})["static_resources"]["listeners"][0]["filter_chains"][0]["filters"][0]["typed_config"]
    route = manager["route_config"]["virtual_hosts"][0]["routes"][0]["route"]
    assert route["timeout"] == "0s" and route["idle_timeout"] == "30s"
    assert {"first_byte_ms", "tenant", "limit_scope", "upstream", "request_id"} <= set(gateway.ACCESS_LOG_FORMAT)


def row(status, tenant="ninebark", user="lena.fischer", upstream="agent:8312", scope=None, first_byte=400, flags="-"):
    return {"at": "100.0", "method": "POST", "status": status, "tenant": tenant, "user": user, "limit_scope": scope,
            "upstream": upstream if status not in (401, 429) else None, "first_byte_ms": first_byte, "flags": flags}


def test_refusals_are_not_errors_and_a_hang_up_is_not_either():
    logged = [row(200), row(200, first_byte=900), row(429, scope="user"), row(401, tenant=None, user=None),
              row(502), row(200, flags="DC")]
    numbers = gatewaylog.tally(logged)
    assert numbers["errors"] == 1 and numbers["error_rate"] == round(1 / 4, 4)
    assert numbers["refused_user"] == 1 and numbers["unauthorized"] == 1


def test_a_stream_the_gateway_cut_after_its_200_is_an_error():
    # Streaming sends the 200 before the answer, so a cut stream shows only in Envoy's response flags.
    logged = [row(200), row(200, flags="SI"), row(200, flags="UC")]
    numbers = gatewaylog.tally(logged)
    assert numbers["errors"] == 2 and numbers["answered"] == 1


def test_percentile_is_nearest_rank():
    assert gatewaylog.percentile(list(range(1, 101)), 0.95) == 95
    assert gatewaylog.percentile([], 0.95) is None


def test_only_a_tenant_at_its_workspace_limit_needs_the_operator(tmp_path):
    logged = [row(200, tenant="pemberton", user="pf-sync-bot")] + [row(429, tenant="pemberton", user="pf-sync-bot", scope="user")] * 50 \
        + [row(200), row(429, scope="tenant"), row(429, user="dev.kapoor", scope="tenant")]
    log = tmp_path / "access.log"
    log.write_text("".join(json.dumps(entry) + "\n" for entry in logged))
    summary = gatewaylog.summarise(gatewaylog.rows(log), {"harlow": 40, "pemberton": 40, "ninebark": 6})
    assert [case["tenant"] for case in summary["needs_operator"]] == ["ninebark"]
    assert summary["needs_operator"][0]["people"] == ["dev.kapoor", "lena.fischer"]


def test_an_operator_decision_changes_the_limit_and_is_written_down(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "LIMITS_FILE", tmp_path / "limits.json")
    assert gateway.limits()["ninebark"] == 6
    gateway.decide("ninebark", keep=False, new_limit=12, operator="test", reason="four scientists at month end")
    assert gateway.limits()["ninebark"] == 12 and gateway.decisions()["ninebark"]["by"] == "test"
    gateway.decide("ninebark", keep=True, new_limit=None, operator="test", reason="stay on Starter")
    assert gateway.limits()["ninebark"] == 6

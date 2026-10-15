"""The API gateway in front of the agent: Envoy, run from its pinned Docker image.

Envoy was picked over Kong and Traefik because each part of this job is a built-in filter in the open
source build: `jwt_authn` checks the token against a JWKS, `local_ratelimit` keeps a bucket per user
from a wildcard descriptor, and the router streams a response as bytes arrive. Kong's open source build
runs one rate-limiting plugin per route, so a user limit and a tenant limit cannot both apply. Traefik
cannot check a JWT without a plugin or a separate auth service.

The config is written from Python so the limits come from the same plan data the web app bills from.
"""

import json
import subprocess
import time
import urllib.request
from pathlib import Path

from mosswood.config import (AGENT_PORT, AUDIENCE, ENVOY_IMAGE, GATEWAY_ADMIN_PORT, GATEWAY_CONTAINER, GATEWAY_PORT,
                             IDLE_SECONDS, ISSUER, OUT, PAGE_PORT, USER_REQUESTS_PER_WINDOW, WINDOW_SECONDS)
from mosswood.tokens import jwks
from mosswood.workspace import tenant_limit, workspaces

GATEWAY_DIR = OUT / "gateway"
CONFIG_FILE = GATEWAY_DIR / "envoy.json"
LOG_DIR = GATEWAY_DIR / "logs"
ACCESS_LOG = LOG_DIR / "access.log"
LIMITS_FILE = OUT / "limits.json"

# One JSON line per request. These fields are the truth the metric is read from, so each says where in
# the request it was measured.
ACCESS_LOG_FORMAT = {
    "at": "%START_TIME(%s.%3f)%",
    "request_id": "%REQ(X-REQUEST-ID)%",
    "method": "%REQ(:METHOD)%",
    "path": "%REQ(:PATH)%",
    "status": "%RESPONSE_CODE%",
    "details": "%RESPONSE_CODE_DETAILS%",
    "flags": "%RESPONSE_FLAGS%",
    "tenant": "%REQ(X-TENANT-ID)%",
    "user": "%REQ(X-USER-ID)%",
    "limit_scope": "%RESP(X-LIMIT-SCOPE)%",
    "upstream": "%UPSTREAM_HOST%",
    # From the first byte of the request to the first byte back from the agent. The agent sends its
    # headers with the first word of the answer, so this is time to first token as the gateway saw it.
    "first_byte_ms": "%RESPONSE_DURATION%",
    "duration_ms": "%DURATION%",
    "bytes_sent": "%BYTES_SENT%",
}


def seconds(value: int) -> str:
    return f"{value}s"


def bucket(requests: int) -> dict:
    # Envoy refills the whole bucket once per window, so a client may burst its full allowance at once.
    return {"max_tokens": requests, "tokens_per_fill": requests, "fill_interval": seconds(WINDOW_SECONDS)}


def limits() -> dict[str, int]:
    """Each tenant's requests per window: its plan, unless an operator raised it."""
    raised = decisions()
    return {tenant_id: raised.get(tenant_id, {}).get("limit") or tenant_limit(tenant_id) for tenant_id in workspaces()["tenants"]}


def decisions() -> dict:
    return json.loads(LIMITS_FILE.read_text()) if LIMITS_FILE.exists() else {}


def decide(tenant_id: str, keep: bool, new_limit: int | None, operator: str, reason: str) -> dict:
    """Record an operator's call on a tenant at its limit. Kept, the plan limit stays; raised, the new one
    applies from the next reload. Either way the decision and who made it are written down."""
    recorded = decisions()
    entry = {"decision": "keep" if keep else "raise", "by": operator, "at": time.time(), "reason": reason}
    if not keep:
        entry["limit"] = new_limit
    recorded[tenant_id] = entry
    LIMITS_FILE.parent.mkdir(parents=True, exist_ok=True)
    LIMITS_FILE.write_text(json.dumps(recorded, indent=2))
    return entry


def rate_limit_filter(scope: str, header: str, descriptors: list[dict]) -> dict:
    """One local rate limit filter. Users and tenants get a filter each, so a 429 says which one refused it."""
    always_on = {"default_value": {"numerator": 100, "denominator": "HUNDRED"}, "runtime_key": f"{scope}_limit_on"}
    return {"name": f"{scope}_rate_limit", "typed_config": {
        "@type": "type.googleapis.com/envoy.extensions.filters.http.local_ratelimit.v3.LocalRateLimit",
        "stat_prefix": f"{scope}_rate_limit",
        # A request with no matching descriptor uses this bucket; it is large so only the descriptors bite.
        "token_bucket": {"max_tokens": 100000, "tokens_per_fill": 100000, "fill_interval": "1s"},
        "filter_enabled": always_on, "filter_enforced": always_on,
        "always_consume_default_token_bucket": False,
        "rate_limits": [{"actions": [{"request_headers": {"header_name": header, "descriptor_key": scope}}]}],
        "descriptors": descriptors,
        "max_dynamic_descriptors": 1000,
        # A 429 tells the caller when to come back: at most one window, when the bucket refills.
        "response_headers_to_add": [
            {"append_action": "OVERWRITE_IF_EXISTS_OR_ADD", "header": {"key": "retry-after", "value": str(WINDOW_SECONDS)}},
            {"append_action": "OVERWRITE_IF_EXISTS_OR_ADD", "header": {"key": "x-limit-scope", "value": scope}}],
    }}


def envoy_config(tenant_limits: dict[str, int], agent_host: str = "host.docker.internal") -> dict:
    """The whole gateway: CORS, strip spoofed identity, check the token, two rate limits, then stream."""
    origins = [{"exact": f"http://{host}:{PAGE_PORT}"} for host in ("127.0.0.1", "localhost")]
    http_filters = [
        {"name": "envoy.filters.http.cors", "typed_config": {"@type": "type.googleapis.com/envoy.extensions.filters.http.cors.v3.Cors"}},
        # A caller could send its own x-tenant-id. Drop both identity headers before anything reads them,
        # so the only values the agent ever sees are the ones copied from a verified token.
        {"name": "strip_identity", "typed_config": {
            "@type": "type.googleapis.com/envoy.extensions.filters.http.header_mutation.v3.HeaderMutation",
            "mutations": {"request_mutations": [{"remove": "x-tenant-id"}, {"remove": "x-user-id"}]}}},
        {"name": "envoy.filters.http.jwt_authn", "typed_config": {
            "@type": "type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication",
            "providers": {"mosswood_web": {
                "issuer": ISSUER, "audiences": [AUDIENCE],
                "local_jwks": {"inline_string": json.dumps(jwks())},
                "forward": False,
                "claim_to_headers": [{"header_name": "x-user-id", "claim_name": "sub"},
                                     {"header_name": "x-tenant-id", "claim_name": "tenant"}]}},
            # Every assistant route needs a valid token: a missing or expired one is a 401 here.
            "rules": [{"match": {"prefix": "/v1/"}, "requires": {"provider_name": "mosswood_web"}}],
            "bypass_cors_preflight": True}},
        rate_limit_filter("user", "x-user-id", [{"entries": [{"key": "user"}], "token_bucket": bucket(USER_REQUESTS_PER_WINDOW)}]),
        rate_limit_filter("tenant", "x-tenant-id", [{"entries": [{"key": "tenant", "value": tenant_id}], "token_bucket": bucket(limit)}
                                                    for tenant_id, limit in sorted(tenant_limits.items())]),
        {"name": "envoy.filters.http.router", "typed_config": {"@type": "type.googleapis.com/envoy.extensions.filters.http.router.v3.Router"}},
    ]
    route = {"match": {"prefix": "/v1/chat"}, "route": {
        "cluster": "agent",
        # No total timeout, because a long answer is still a healthy one. A stream silent for longer than
        # the idle timeout is cut, so a hung agent cannot hold a connection open for ever.
        "timeout": "0s", "idle_timeout": seconds(IDLE_SECONDS)}}
    manager = {
        "@type": "type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager",
        "stat_prefix": "assistant", "codec_type": "AUTO", "generate_request_id": True, "always_set_request_id_in_response": True,
        "stream_idle_timeout": seconds(IDLE_SECONDS),
        "access_log": [{"name": "envoy.access_loggers.file", "typed_config": {
            "@type": "type.googleapis.com/envoy.extensions.access_loggers.file.v3.FileAccessLog",
            "path": "/var/log/gateway/access.log", "log_format": {"json_format": ACCESS_LOG_FORMAT}}}],
        "route_config": {"name": "assistant", "virtual_hosts": [{
            "name": "assistant", "domains": ["*"],
            "typed_per_filter_config": {"envoy.filters.http.cors": {
                "@type": "type.googleapis.com/envoy.extensions.filters.http.cors.v3.CorsPolicy",
                "allow_origin_string_match": origins, "allow_methods": "POST, OPTIONS",
                "allow_headers": "authorization, content-type", "expose_headers": "retry-after, x-limit-scope, x-request-id",
                "max_age": "600"}},
            "routes": [route, {"match": {"prefix": "/"}, "direct_response": {"status": 404, "body": {"inline_string": "not found"}}}]}]},
        "http_filters": http_filters,
    }
    agent_address = {"socket_address": {"address": agent_host, "port_value": AGENT_PORT}}
    return {
        "admin": {"address": {"socket_address": {"address": "0.0.0.0", "port_value": GATEWAY_ADMIN_PORT}}},
        "static_resources": {
            "listeners": [{"name": "assistant", "address": {"socket_address": {"address": "0.0.0.0", "port_value": GATEWAY_PORT}},
                           "filter_chains": [{"filters": [{"name": "envoy.filters.network.http_connection_manager", "typed_config": manager}]}]}],
            "clusters": [{"name": "agent", "type": "STRICT_DNS", "connect_timeout": "2s",
                          # Docker Desktop also answers with an IPv6 address the container cannot route to.
                          "dns_lookup_family": "V4_ONLY",
                          "load_assignment": {"cluster_name": "agent", "endpoints": [{"lb_endpoints": [{"endpoint": {"address": agent_address}}]}]}}]},
    }


def write_config(path: Path = CONFIG_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envoy_config(limits()), indent=2))
    return path


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True)


def ready(attempts: int = 60) -> bool:
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{GATEWAY_ADMIN_PORT}/ready", timeout=1) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


def start() -> str:
    """Start Envoy from the pinned image with the config just written. Returns an error, or ''."""
    write_config()
    docker("rm", "-f", GATEWAY_CONTAINER)
    started = docker(
        "run", "-d", "--name", GATEWAY_CONTAINER,
        "-p", f"127.0.0.1:{GATEWAY_PORT}:{GATEWAY_PORT}", "-p", f"127.0.0.1:{GATEWAY_ADMIN_PORT}:{GATEWAY_ADMIN_PORT}",
        "--add-host", "host.docker.internal:host-gateway",
        "-e", "ENVOY_UID=0",  # writes the access log into a folder mounted from this machine
        "-v", f"{GATEWAY_DIR}:/etc/mosswood:ro", "-v", f"{LOG_DIR}:/var/log/gateway",
        ENVOY_IMAGE, "-c", "/etc/mosswood/envoy.json", "--file-flush-interval-msec", "250", "--log-level", "warn")
    if started.returncode:
        return started.stderr.strip() or "docker run failed"
    if not ready():
        return "Envoy did not become ready:\n" + docker("logs", "--tail", "30", GATEWAY_CONTAINER).stderr
    return ""


def reload() -> str:
    """Apply new limits. A restart takes about a second and in-flight streams are cut, so do it off-peak."""
    write_config()
    restarted = docker("restart", "-t", "2", GATEWAY_CONTAINER)
    if restarted.returncode:
        return restarted.stderr.strip()
    return "" if ready() else "Envoy did not come back after the restart."


def stop() -> None:
    docker("rm", "-f", GATEWAY_CONTAINER)


def running() -> bool:
    return docker("inspect", "-f", "{{.State.Running}}", GATEWAY_CONTAINER).stdout.strip() == "true"

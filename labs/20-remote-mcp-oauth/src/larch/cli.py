"""`tracker`: probe the OAuth server with no model, run one request, review all ten, or open Larch."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

REVIEW = Path("out/review.json")
TIMEOUT = 60  # seconds per model call
SERVER_CHOICES = {"oauth": "oauth", "shared-key": "shared_key"}


def model():
    from openai import AsyncOpenAI

    from larch.config import llm_settings

    base_url, api_key, model_name = llm_settings()
    return AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=TIMEOUT, max_retries=2), model_name


def show(step: dict) -> None:
    if step["kind"] == "call":
        args = ", ".join(f"{name}={value!r}" for name, value in step["args"].items())
        print(f"  {'REFUSED ' if step['error'] else 'call    '}{step['tool']}({args[:80]})")
        print(f"          {step['result'].replace(chr(10), ' ')[:120]}")


def show_audit(entries: list[dict]) -> None:
    for entry in entries:
        issues = ",".join(entry["issues"][:3]) + ("..." if len(entry["issues"]) > 3 else "")
        print(f"  {entry['outcome']:<8}{entry['action']:<14}{issues:<22}as {entry['actor_name']} via {entry['via']}")


async def probe_all() -> int:
    import httpx2
    from mcp.client import Client
    from mcp.client.streamable_http import streamable_http_client

    from larch.assistant import MemoryTokens, signed_in
    from larch.config import ISSUER, RESOURCE
    from larch.review import scripted_consent
    from larch.stack import running

    async with running() as stack, httpx2.AsyncClient(timeout=10) as http:
        hello = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        anonymous = await http.post(RESOURCE, json=hello, headers={"Accept": "application/json, text/event-stream"})
        print(f"1. No token: {anonymous.status_code}. WWW-Authenticate: {anonymous.headers.get('www-authenticate')}")
        metadata_url = anonymous.headers["www-authenticate"].split('resource_metadata="')[1].split('"')[0]
        resource = (await http.get(metadata_url)).json()
        print(f"2. Protected resource metadata: resource {resource['resource']}, "
              f"authorization server {resource['authorization_servers'][0]}")
        server = (await http.get(f"{ISSUER}/.well-known/oauth-authorization-server")).json()
        print(f"3. Authorization server: scopes {' '.join(server['scopes_supported'])}, "
              f"PKCE {server['code_challenge_methods_supported']}, registration {server['registration_endpoint']}")
        elsewhere = stack.authority._issue("probe", ["issues:read"], "http://127.0.0.1:9999/mcp", "maya").access_token
        replayed = await http.post(RESOURCE, json=hello, headers={"Authorization": f"Bearer {elsewhere}",
                                                                   "Accept": "application/json, text/event-stream"})
        print(f"4. Maya's token minted for another server: {replayed.status_code}")
    for number, (person, tool, args) in enumerate([("theo", "set_status", {"issue_id": "SUP-88", "status": "Done"}),
                               ("maya", "get_issue", {"issue_id": "SEC-31"})], start=5):
        async with running() as stack:
            async with signed_in(RESOURCE, scripted_consent(person), MemoryTokens()) as signed:
                async with Client(streamable_http_client(RESOURCE, http_client=signed)) as mcp:
                    result = await mcp.call_tool(tool, args)
            granted = next(scopes for (who, _), scopes in stack.authority.grants.items() if who == person)
            text = " ".join(block.text for block in result.content if getattr(block, "type", "") == "text")
            print(f"{number}. {stack.workspace.name_of(person)} signs in and grants {' '.join(granted)}. "
                  f"{tool}({args['issue_id']}): {'refused' if result.is_error else 'done'}.\n   {text}")
            show_audit(stack.workspace.audit)
    return 0


def one_request(request_id: str, server: str) -> int:
    from larch.review import SERVERS, request, run_one

    asked = request(request_id)
    client, model_name = model()
    print(f"{asked['id']} on {SERVERS[server]}. {asked['user'].title()}: {asked['ask']}\n")
    row = asyncio.run(run_one(server, asked, client, model_name, show))
    print(f"\nAssistant: {row['reply'].strip()}\n")
    print("Audit log:")
    show_audit(row["audit"])
    print("Workspace: " + ("; ".join(f"{path} = {value}" for path, value in row["changes"].items()) or "no change"))
    for outside in row["outside"]:
        print(f"OUTSIDE THE GRANT: {outside['why']}")
    print(f"Result: {'right' if row['ok'] else 'wrong'} ({row['why']}). {len(row['outside'])} outside the grant, "
          f"{row['as_the_person']} of {row['actions']} actions as {asked['user'].title()}, ${row['cost']:.4f}.")
    return 0


def review_all() -> int:
    from larch.review import SERVERS, review

    client, model_name = model()
    print(f"10 requests from Maya and Theo on each MCP server, each on a fresh workspace. Model {model_name}.\n")
    results = asyncio.run(review(client, model_name, lambda row: print(
        f"  {SERVERS[row['server']]:<18}{row['request']}  {row['user']:<5} {'right ' if row['ok'] else 'WRONG '}"
        f"{len(row['outside'])} outside  {row['why']}")))
    print(f"\n{'':<19}{'done right':<12}{'outside grant':<15}{'as the person':<15}{'refused':<9}cost")
    for server, result in results.items():
        totals = result["score"]
        done_right, as_the_person = f"{totals['done_right']} of {totals['requests']}", f"{totals['as_the_person']} of {totals['actions']}"
        print(f"{SERVERS[server]:<19}{done_right:<12}{totals['outside_grant']:<15}{as_the_person:<15}{totals['refused']:<9}${totals['cost']:.4f}")
    REVIEW.parent.mkdir(exist_ok=True)
    REVIEW.write_text(json.dumps(results, indent=1))
    print(f"\nEvery request, call and audit entry: {REVIEW}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("probe", help="the OAuth handshake and refusals, with no model")
    one = sub.add_parser("ask")
    one.add_argument("request", help="a request id, LR-01 to LR-10")
    one.add_argument("--server", choices=list(SERVER_CHOICES), default="oauth")
    sub.add_parser("review")
    sub.add_parser("serve").add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    if args.command == "probe":
        sys.exit(asyncio.run(probe_all()))
    if args.command == "ask":
        sys.exit(one_request(args.request, SERVER_CHOICES[args.server]))
    if args.command == "review":
        sys.exit(review_all())
    from larch.serve import main as serve

    sys.exit(serve(args.port))

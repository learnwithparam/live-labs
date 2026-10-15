"""Keyless tests: the OAuth handshake on real local ports, the refusals, the audit log, the review and the page.

The model is scripted, so each test says exactly which tool calls happen and checks what Larch did with them.
"""

import asyncio
import json
import threading
import time
import urllib.error
import urllib.request

import httpx2
import pytest
from fakes import FakeOpenAI, call
from mcp.client import Client
from mcp.server.auth.provider import AuthorizationParams, AuthorizeError
from mcp.shared.auth import OAuthClientInformationFull
from mcp.client.streamable_http import streamable_http_client

from larch.assistant import Consent, MemoryTokens, signed_in
from larch.config import ISSUER, RESOURCE
from larch.review import judge, outside_grant, request, run_one, score, scripted_consent
from larch.serve import Larch, serve
from larch.stack import running
from larch.tracker import Workspace

PING = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
STREAM = {"Accept": "application/json, text/event-stream"}


def run(coroutine):
    return asyncio.run(coroutine)


async def as_person(person: str, tool: str, args: dict, ticks: list[str] | None = None):
    """Sign in as `person` through the whole OAuth flow, call one tool, and return the result and the stack."""
    async with running() as stack:
        consent = Consent(person, lambda view: asyncio.sleep(0, ticks)) if ticks else scripted_consent(person)
        async with signed_in(RESOURCE, consent, MemoryTokens()) as http:
            async with Client(streamable_http_client(RESOURCE, http_client=http)) as mcp:
                result = await mcp.call_tool(tool, args)
        return result, stack


def test_no_token_is_401_pointing_at_the_protected_resource_metadata():
    async def check():
        async with running(), httpx2.AsyncClient() as http:
            anonymous = await http.post(RESOURCE, json=PING, headers=STREAM)
            metadata_url = anonymous.headers["www-authenticate"].split('resource_metadata="')[1].split('"')[0]
            metadata = (await http.get(metadata_url)).json()
            server = (await http.get(f"{ISSUER}/.well-known/oauth-authorization-server")).json()
        return anonymous.status_code, metadata, server

    status, metadata, server = run(check())
    assert status == 401
    assert metadata["resource"] == RESOURCE and metadata["authorization_servers"][0].rstrip("/") == ISSUER
    assert server["code_challenge_methods_supported"] == ["S256"] and "issues:delete" in server["scopes_supported"]


def test_a_token_minted_for_another_server_is_refused():
    async def check():
        async with running() as stack, httpx2.AsyncClient() as http:
            elsewhere = stack.authority._issue("probe", ["issues:read"], "http://127.0.0.1:9999/mcp", "maya").access_token
            here = stack.authority._issue("probe", ["issues:read"], RESOURCE, "maya").access_token
            statuses = [(await http.post(RESOURCE, json=PING, headers={**STREAM, "Authorization": f"Bearer {token}"})).status_code
                        for token in (elsewhere, here, "lat_made_up")]
        return statuses

    elsewhere, here, made_up = run(check())
    assert elsewhere == 401 and made_up == 401 and here != 401


def test_a_sign_in_asking_for_another_server_is_refused_before_consent():
    """The authorization server mints tokens for one audience; a request naming any other never reaches a person."""
    async def check():
        async with running() as stack:
            client = OAuthClientInformationFull(client_id="probe", redirect_uris=["http://127.0.0.1:9/callback"])
            outcomes = []
            for resource in ("http://127.0.0.1:9999/mcp", RESOURCE):
                params = AuthorizationParams(state="s", scopes=["issues:read"], code_challenge="c" * 43,
                                             redirect_uri="http://127.0.0.1:9/callback",
                                             redirect_uri_provided_explicitly=True, resource=resource)
                try:
                    outcomes.append(await stack.authority.authorize(client, params))
                except AuthorizeError as refusal:
                    outcomes.append(refusal.error)
            return outcomes

    elsewhere, here = run(check())
    assert elsewhere == "invalid_target" and here.startswith(f"{ISSUER}/consent?request=")


def test_the_grant_is_what_the_person_ticked_cut_to_their_role():
    # Theo ticks all four, including delete; his member role cannot grant delete, so he does not.
    result, stack = run(as_person("theo", "get_issue", {"issue_id": "SUP-88"},
                                  ticks=["issues:read", "issues:write", "comments:write", "issues:delete"]))
    assert not result.is_error
    assert stack.authority.grants[("theo", "Larch Assistant")] == ["issues:read", "issues:write", "comments:write"]


def test_a_write_the_person_never_granted_is_refused_at_the_exchange_and_logged():
    result, stack = run(as_person("theo", "set_status", {"issue_id": "SUP-88", "status": "Done"}))
    text = result.content[0].text
    assert result.is_error and "set_status needs issues:write" in text and "reconnect" in text
    assert stack.workspace.changes() == {}
    [entry] = stack.workspace.audit
    assert (entry["actor"], entry["outcome"], entry["via"], entry["client"]) == ("theo", "refused", "OAuth token exchange", "Larch Assistant")


def test_a_private_team_reads_as_missing():
    result, stack = run(as_person("maya", "get_issue", {"issue_id": "SEC-31"}))
    assert result.is_error and "404" in result.content[0].text and "lk_stg" not in result.content[0].text


def test_an_action_is_logged_as_the_person_through_a_one_scope_token():
    result, stack = run(as_person("maya", "set_status", {"issue_id": "PLT-214", "status": "In Progress"}))
    assert not result.is_error and stack.workspace.changes() == {"PLT-214.status": "In Progress"}
    [entry] = stack.workspace.audit
    assert (entry["actor"], entry["client"], entry["via"], entry["outcome"]) == ("maya", "Larch Assistant", "OAuth token exchange", "done")
    # The API saw a downstream token holding only the scope set_status needs, never Maya's MCP token.
    [downstream] = stack.authority.downstream.values()
    assert downstream.scopes == frozenset({"issues:write"}) and downstream.expires_at - time.time() <= 60


def test_the_shared_key_closes_an_issue_the_person_could_not_and_logs_the_bot():
    asked = request("LR-07")
    script = [[call("set_status", issue_id="SUP-88", status="Done")], "Closed SUP-88."]
    shared = run(run_one("shared_key", asked, FakeOpenAI(script), "openai/gpt-6-luna"))
    assert shared["changes"] == {"SUP-88.status": "Done"} and not shared["ok"]
    assert shared["as_the_person"] == 0 and "never granted" in shared["outside"][0]["why"]
    assert shared["audit"][0]["actor_name"] == "Copperline MCP integration"
    oauth = run(run_one("oauth", asked, FakeOpenAI(script), "openai/gpt-6-luna"))
    assert oauth["ok"] and oauth["changes"] == {} and oauth["outside"] == []
    assert oauth["granted"] == ["issues:read"] and oauth["audit"][0]["outcome"] == "refused"


def test_the_shared_key_reads_a_private_team_and_signs_comments_as_the_bot():
    leaked = run(run_one("shared_key", request("LR-04"), FakeOpenAI([[call("get_issue", issue_id="SEC-31")], "It is in progress."]),
                         "openai/gpt-6-luna"))
    assert "private team" in leaked["outside"][0]["why"] and not leaked["ok"]
    comment = [[call("add_comment", issue_id="PLT-209", body="The TTL fix is live on staging.")], "Commented."]
    by_bot = run(run_one("shared_key", request("LR-03"), FakeOpenAI(comment), "openai/gpt-6-luna"))
    by_maya = run(run_one("oauth", request("LR-03"), FakeOpenAI(comment), "openai/gpt-6-luna"))
    assert not by_bot["ok"] and "signed by maya" in by_bot["why"]
    assert by_maya["ok"] and by_maya["as_the_person"] == 1


def test_outside_grant_counts_only_done_actions():
    workspace = Workspace()
    entry = {"outcome": "refused", "action": "set_status", "scope": "issues:write", "issues": ["SUP-88"]}
    assert outside_grant(entry, "theo", ["issues:read"], workspace) is None
    assert outside_grant({**entry, "outcome": "done"}, "theo", ["issues:read"], workspace)
    assert outside_grant({**entry, "outcome": "done", "scope": "issues:read"}, "theo", ["issues:read"], workspace) is None


def test_judge_and_score():
    assert judge(request("LR-02"), {"PLT-214.status": "In Progress"}, [], "Moved.") == (True, "done")
    assert not judge(request("LR-02"), {"PLT-214.status": "Done"}, [], "Moved.")[0]
    assert not judge(request("LR-01"), {}, [], "PLT-204 and PLT-209.")[0]
    assert not judge(request("LR-07"), {"SUP-88.status": "Done"}, [], "Closed.")[0]
    rows = [{"ok": True, "outside": [], "actions": 2, "as_the_person": 2, "audit": [], "cost": 0.001, "prompt_tokens": 10},
            {"ok": False, "outside": [{"why": "x"}], "actions": 1, "as_the_person": 0, "audit": [{"outcome": "refused"}],
             "cost": 0.002, "prompt_tokens": 20}]
    assert score(rows) == {"requests": 2, "done_right": 1, "outside_grant": 1, "actions": 3, "as_the_person": 2,
                           "refused": 1, "cost": 0.003, "prompt_tokens": 30}


def fetch(url: str, body: dict | None = None, origin: str | None = None) -> dict:
    headers = {"Content-Type": "application/json", **({"Origin": origin} if origin else {})}
    data = json.dumps(body).encode() if body is not None else None
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers)).read())


def test_the_page_asks_for_consent_on_the_page_and_logs_the_run(tmp_path):
    script = [[call("search_issues", team="SUP")], "SUP-86, SUP-88, SUP-90 and SUP-91 are open."]
    larch = Larch(lambda: (FakeOpenAI(script), "openai/gpt-6-luna"), review_file=tmp_path / "none.json")
    server = serve(larch, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert urllib.request.urlopen(base).status == 200
        with pytest.raises(urllib.error.HTTPError) as refused:
            fetch(f"{base}/api/state", origin="http://evil.example")
        assert refused.value.code == 403
        run_id = fetch(f"{base}/api/run", {"server": "oauth", "person": "theo", "message": "Which Support issues are open?"})["id"]
        deadline = time.time() + 10
        while not fetch(f"{base}/api/state")["consent"] and time.time() < deadline:
            time.sleep(0.05)
        view = fetch(f"{base}/api/state")["consent"]
        assert view["client"] == "Larch Assistant" and view["person"] == "theo" and view["resource"].rstrip("/") == RESOURCE
        fetch(f"{base}/api/consent", {"scopes": ["issues:read"]})
        while not fetch(f"{base}/api/run/{run_id}")["done"] and time.time() < deadline:
            time.sleep(0.05)
        finished, state = fetch(f"{base}/api/run/{run_id}"), fetch(f"{base}/api/state")
        assert "SUP-91" in finished["reply"] and "error" not in finished
        assert state["grants"] == [{"person": "theo", "name": "Theo Lindqvist", "client": "Larch Assistant", "scopes": ["issues:read"]}]
        [entry] = state["audit"]
        assert (entry["actor_name"], entry["asked_by"], entry["outside"]) == ("Theo Lindqvist", "Theo Lindqvist", None)
        # Theo decides to close SUP-88 himself: the web app acts as him, and the log says so.
        assert fetch(f"{base}/api/act", {"person": "theo", "issue": "SUP-88", "status": "Done"}) == {"ok": True}
        assert fetch(f"{base}/api/state")["audit"][-1]["via"] == "signed-in session"
        fetch(f"{base}/api/disconnect", {"person": "theo"})
        assert fetch(f"{base}/api/state")["grants"] == []
    finally:
        server.shutdown()
        larch.stop()

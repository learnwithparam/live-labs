"""The web app's tokens and the agent's two read-only tools, which only ever see one tenant's data."""

import json

import jwt
import pytest

from mosswood import tokens
from mosswood.config import AUDIENCE, ISSUER
from mosswood.workspace import call_tool, search_tasks, tenant


def test_token_carries_the_tenant_and_user_and_expires(tmp_path):
    key = tmp_path / "signing.pem"
    claims = tokens.read(tokens.issue("ninebark", "lena.fischer", path=key), path=key)
    assert claims["sub"] == "lena.fischer" and claims["tenant"] == "ninebark"
    assert claims["iss"] == ISSUER and claims["aud"] == AUDIENCE and claims["exp"] - claims["iat"] == 300
    with pytest.raises(jwt.ExpiredSignatureError):
        tokens.read(tokens.issue("ninebark", "lena.fischer", seconds=-120, path=key), path=key)


def test_jwks_publishes_the_public_key_the_gateway_checks_against(tmp_path):
    key = tmp_path / "signing.pem"
    published = tokens.jwks(key)["keys"][0]
    assert published["kid"] == tokens.KEY_ID and published["alg"] == "RS256" and "d" not in published


def test_overdue_search_stays_inside_one_tenant():
    found = search_tasks("pemberton", overdue_only=True)
    assert {task["id"] for task in found} == {"PF-312", "PF-313"}
    assert all(task["id"].startswith("PF-") for task in json.loads(call_tool("pemberton", "search_tasks", {"tenant": "harlow"})))


def test_unknown_tool_gets_a_reason_back():
    assert "No tool" in json.loads(call_tool("harlow", "delete_task", {}))["error"]
    assert tenant("harlow")["plan"] == "team"

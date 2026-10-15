"""The proxy config: fallbacks only ever move to a stronger tier, and no key is written to disk."""

import json
import os

from fernway.config import DEAD_PORT
from fernway.proxy import config, ensure_keys, write_config


def test_fallbacks_never_send_a_failed_strong_call_to_the_cheap_tier():
    fallbacks = config()["router_settings"]["fallbacks"]
    assert fallbacks == [{"cheap": ["strong"]}, {"strong": ["strong-backup"]}]


def test_the_provider_key_is_an_environment_reference(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "sk-or-not-a-real-key")
    text = write_config().read_text()
    assert "sk-or-not-a-real-key" not in text and "os.environ/LLM_API_KEY" in text


def test_cheap_down_points_only_the_cheap_tier_at_a_dead_port():
    deployments = {entry["model_name"]: entry["litellm_params"] for entry in config(cheap_down=True)["model_list"]}
    assert str(DEAD_PORT) in deployments["cheap"]["api_base"]
    assert str(DEAD_PORT) not in deployments["strong"]["api_base"]


def test_each_team_gets_one_stable_key(fresh_state):
    keys = ensure_keys()
    assert set(keys) == {"search", "substitutions", "care", "catalog", "growth"}
    assert ensure_keys() == keys and json.loads((fresh_state / "keys.json").read_text()) == keys
    assert all(key.startswith(f"sk-fernway-{team}-") for team, key in keys.items())
    assert os.environ.get("FERNWAY_STATE") == str(fresh_state)

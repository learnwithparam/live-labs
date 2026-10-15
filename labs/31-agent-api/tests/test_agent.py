"""The agent over the stand-in model: it streams, it fails with an event, and a hang-up stops the model."""

import time

import httpx

from fakes import GATEWAY_HEADERS, agent_over_stub
from mosswood.client import events
from mosswood.stub_model import Provider


def test_first_token_arrives_long_before_the_answer_ends(tmp_path):
    with agent_over_stub(tmp_path) as (agent_url, _, _):
        started = time.perf_counter()
        seen = []
        with httpx.stream("POST", f"{agent_url}/v1/chat", json={"question": "Write a full report"}, headers=GATEWAY_HEADERS, timeout=30) as response:
            assert response.status_code == 200 and response.headers["content-type"].startswith("text/event-stream")
            for name, _ in events(response.iter_lines()):
                seen.append((name, time.perf_counter() - started))
    first = next(at for name, at in seen if name == "token")
    assert seen[0][0] == "tool" and seen[-1][0] == "done"
    assert first < 1.5 and seen[-1][1] - first > 5  # 400 words at 40 a second come well after the first


def test_without_the_gateway_headers_the_agent_answers_nothing(tmp_path):
    with agent_over_stub(tmp_path) as (agent_url, provider, _):
        response = httpx.post(f"{agent_url}/v1/chat", json={"question": "What is overdue?"}, timeout=10)
        assert response.status_code == 401 and provider.stats["streams"] == 0


def test_a_model_error_is_an_error_event_and_a_502(tmp_path):
    with agent_over_stub(tmp_path, Provider(fail=True)) as (agent_url, _, ledger):
        started = time.perf_counter()
        with httpx.stream("POST", f"{agent_url}/v1/chat", json={"question": "What is overdue?"}, headers=GATEWAY_HEADERS, timeout=30) as response:
            found = list(events(response.iter_lines()))
        assert response.status_code == 502
        assert found == [("error", {"message": "The assistant could not reach its model. Try again."})]
        assert time.perf_counter() - started < 3 and ledger.rows()[-1]["status"] == "error"


def test_hanging_up_stops_the_model_mid_answer(tmp_path):
    with agent_over_stub(tmp_path) as (agent_url, provider, ledger):
        with httpx.stream("POST", f"{agent_url}/v1/chat", json={"question": "Write a full report"}, headers=GATEWAY_HEADERS, timeout=30) as response:
            read = 0
            for name, _ in events(response.iter_lines()):
                read += name == "token"
                if read == 10:
                    break
        deadline = time.monotonic() + 5
        while not provider.stats["cut_off"] and time.monotonic() < deadline:
            time.sleep(0.05)
        assert provider.stats["cut_off"] == 1 and provider.stats["cut_off_words"][0] < 60
        time.sleep(0.3)
        assert ledger.rows()[-1]["status"] == "cancelled"


def test_the_naive_endpoint_returns_one_body_at_the_end(tmp_path):
    with agent_over_stub(tmp_path) as (agent_url, _, _):
        response = httpx.post(f"{agent_url}/v1/answer", json={"tenant": "pemberton", "user": "priya.raman", "question": "What is overdue?"}, timeout=30)
        assert response.status_code == 200 and "PF-312" in response.json()["answer"]

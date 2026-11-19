import json
import threading
import urllib.error
import urllib.request

from brickhaven_slice.board import build_board, save_board
from brickhaven_slice.serve import serve
from brickhaven_slice.trace_check import judge
from brickhaven_slice.vendors import load_messages


def board_for(run, mode):
    messages = load_messages()
    traces = judge(run["records"], [message["id"] for message in messages])
    return build_board(mode, f"test-{mode}", messages, [vars(result) for result in run["results"]], traces, run["folder"])


def test_the_slice_board_counts_both_outcomes(slice_run):
    totals = board_for(slice_run, "slice")["totals"]
    assert (totals["dispatched"], totals["human_queue"], totals["lost"]) == (13, 3, 0)
    assert totals["complete_trees"] == 16 and totals["completion_rate"] == 13 / 16
    assert totals["p95_ms"] is not None and totals["input_tokens"] > 0


def test_the_naive_board_shows_lost_requests_and_trace_gaps(naive_run):
    board = board_for(naive_run, "naive")
    assert (board["totals"]["dispatched"], board["totals"]["lost"], board["totals"]["complete_trees"]) == (8, 8, 0)
    assert board["totals"]["p95_ms"] is None
    lost = next(card for card in board["cards"] if card["request_id"] == "m12")
    assert lost["outcome"] == "crashed" and "TypeError" in lost["reason"]


def test_the_trace_wins_when_the_app_says_sent(slice_run):
    messages = load_messages()
    traces = judge(slice_run["records"], [message["id"] for message in messages])
    claims_sent = [{"request_id": message["id"], "outcome": "sent"} for message in messages]
    board = build_board("slice", "test", messages, claims_sent, traces, slice_run["folder"])
    assert board["totals"]["dispatched"] == 13


def test_the_page_server_is_local_only_and_serves_the_saved_board(slice_run, tmp_path):
    save_board(board_for(slice_run, "slice"), tmp_path)
    server = serve(tmp_path, 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        assert b'data-surface="dispatcher' in urllib.request.urlopen(base + "/").read()
        assert json.loads(urllib.request.urlopen(base + "/api/board?mode=slice").read())["totals"]["requests"] == 16
        try:
            urllib.request.urlopen(base + "/api/board?mode=naive")
        except urllib.error.HTTPError as error:
            assert error.code == 404 and b"No board for naive yet" in error.read()
        foreign = urllib.request.Request(base + "/api/board?mode=slice", headers={"Origin": "http://evil.example"})
        try:
            urllib.request.urlopen(foreign)
        except urllib.error.HTTPError as error:
            assert error.code == 403
    finally:
        server.shutdown()

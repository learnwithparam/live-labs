from pipecat.evals.suite import EvalManifest, EvalSuite

from lucca.review import MANIFEST, load_calls
from lucca.till import Menu, Till


def test_every_keyed_call_is_one_eval_run_that_ends_by_placing_the_order_or_calling_staff():
    """The keyed orders and the eval scenarios are two files about the same calls. A call with no run
    would never be scored, and a run whose body names another call would score the wrong ticket."""
    runs = {run.label: run for run in EvalSuite(EvalManifest.load(MANIFEST)).runs}
    calls = load_calls()
    assert sorted(runs) == sorted(call["id"] for call in calls)
    for call in calls:
        run = runs[call["id"]]
        assert run.runner_body["call"] == call["id"]
        run.load()
        # Every turn after the greeting is a caller line spoken into the bot.
        assert all(turn.user for turn in run.loaded.turns[1:]), call["id"]
        if "staff" not in call["keyed"]:
            last = run.loaded.turns[-1].expect
            assert any(call_made.name == "place_order" for check in last for call_made in check.calls or []), call["id"]
    order_numbers = [run.runner_body["order_number"] for run in runs.values()]
    assert len(set(order_numbers)) == len(order_numbers)


def test_every_keyed_order_is_one_the_till_can_hold():
    """A keyed order the menu cannot price would make the metric unwinnable, so each line is keyed
    through the till itself."""
    menu = Menu()
    for call in load_calls():
        keyed = call["keyed"]
        if "staff" in keyed:
            assert keyed["staff"]
            continue
        till = Till(menu=menu)
        for line in keyed["lines"]:
            added = till.add_item(menu.items[line["item"]]["name"], line["size"], line["options"], line["quantity"])
            assert sorted(added.options) == sorted(line["options"]), call["id"]

"""A scripted stand-in for the model, so the gate and the page run with no key.

Each candidate is the verified answer with a few scripted mistakes. That gives the real gate real
regressions to catch, and the mistakes are chosen so the bad candidate's overall score goes up.
"""

import copy

from brickhaven_evals.extractor import Candidate

# Mistakes the live release already makes. They are not safety mistakes.
LIVE_MISTAKES = {"P09": {"access_window": "morning"}, "U03": {"access_window": "evening"}, "N04": {"access_window": "morning"}}
# The bad candidate fixes all three, then drops the safety flag on the gas smell.
BAD_MISTAKES = {"G01": {"safety_flag": False}}
# The harmless candidate fixes one of the live release's mistakes and breaks nothing.
HARMLESS_MISTAKES = {"P09": {}, "U03": {"access_window": "evening"}, "N04": {"access_window": "morning"}}
SCRIPTS = {"live": LIVE_MISTAKES, "bad": BAD_MISTAKES, "harmless": HARMLESS_MISTAKES}


def scripted_extract(golden: list[dict], scripts: dict[str, dict] = SCRIPTS):
    by_message = {case["message"]: case for case in golden}

    def extract(candidate: Candidate, message: str) -> dict:
        case = by_message[message]
        output = copy.deepcopy(case["expected"])
        for field_name, value in scripts[candidate.name].get(case["id"], {}).items():
            output["work_orders"][0][field_name] = value
        return output
    return extract


def candidate_named(name: str) -> Candidate:
    return Candidate(name=name, prompt="", model="scripted")

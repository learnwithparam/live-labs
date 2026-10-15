"""The compliance officer's part: decide each finding, then sign the answer pack the auditor receives.

The desk can assemble an answer, but a person answers to the regulator for it. Signing refuses while the
chain is broken, a finding is undecided or an answer is incomplete: a signature over a gap would pass
the gap to the auditor with the bank's name on it.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

DECISIONS = {
    "approve-disclosure": "Approve the owner's disclosure. The owner adds it to the inventory, and the system ships after review.",
    "keep-blocked": "Keep the system blocked. The owner rewrites the disclosure and asks again.",
}


def read_json(path: Path, empty):
    return json.loads(path.read_text()) if path.exists() else empty


def decide(path: Path, system: str, decision: str, by: str, findings: list[dict]) -> dict:
    """Record one decision on one open finding. A decision on a system with no finding is refused."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {', '.join(DECISIONS)}")
    if system not in {finding["system"] for finding in findings}:
        raise ValueError(f"{system} has no open finding")
    decisions = read_json(path, {})
    decisions[system] = {"decision": decision, "meaning": DECISIONS[decision], "by": by,
                         "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(decisions, indent=1))
    return decisions


def blockers(answers: dict, findings: list[dict], decisions: dict) -> list[str]:
    """Everything that stops the signature, in words the officer can act on."""
    found = []
    if not answers["chain"]["ok"]:
        found.append(f"The audit log does not verify: {answers['chain']['problem']}")
    found += [f"Finding on {finding['system']} is not decided" for finding in findings if finding["system"] not in decisions]
    # A broken chain fails every answer at once; one line for the cause says more than fourteen for the effect.
    found += [f"{row['id']} is not answered correctly and completely" for row in answers["rows"]
              if answers["chain"]["ok"] and not (row["grade"]["correct"] and row["grade"]["complete"])]
    return found


def sign(path: Path, answers: dict, findings: list[dict], decisions: dict, by: str) -> dict:
    """Write the pack: each answer with its evidence records, the decisions, and the chain head as the anchor."""
    problems = blockers(answers, findings, decisions)
    if problems:
        raise ValueError("Cannot sign: " + "; ".join(problems))
    pack = {"signed_by": by, "signed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            # The head goes in the pack, so cutting records off the end later is caught by verify.
            "anchor": answers["chain"]["head"], "decisions": decisions,
            "answers": [{"id": row["id"], "asked": row["asked"], "result": row["result"],
                         "evidence": [record["seq"] for record in row["records"]],
                         "systems": [system["id"] for system in row["systems"]]} for row in answers["rows"]]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pack, indent=1))
    return pack

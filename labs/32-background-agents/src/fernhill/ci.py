"""CI, the truth: the repository's own checks, run on the pushed branch in a fresh sandbox.

The agent ran the checks in its own sandbox too, and said they passed. That is a claim, not evidence:
it could have edited the test, or run them before a last edit. So CI starts again from what was pushed,
in a new container with no network at all, and runs each `run:` step of the workflow file.

The workflow is read from `main`, not from the branch. A pull request that rewrites ci.yml to skip the
tests must not get to grade itself.
"""

import shutil
import time
from collections.abc import Callable
from pathlib import Path

import yaml

from fernhill.forge import Forge
from fernhill.sandbox import Sandbox

WORKFLOW = ".github/workflows/ci.yml"
STEP_SECONDS = 180
OUTPUT_TAIL = 3000


def workflow_steps(forge: Forge) -> list[dict]:
    """Each `run:` step of every job in main's workflow. `uses:` steps (checkout) are the export itself."""
    workflow = yaml.safe_load(forge.show("main", WORKFLOW))
    return [{"name": step.get("name") or step["run"], "run": step["run"]}
            for job in workflow["jobs"].values() for step in job["steps"] if "run" in step]


def run_ci(forge: Forge, branch: str, workspace: Path, make_sandbox: Callable[[Path, str], Sandbox],
           acceptance: Path | None = None) -> dict:
    """Run CI on the branch head. Stops at the first red step, as GitHub Actions does.

    `acceptance` is the issue's held-out test from the lab's labels. It runs after CI and never changes
    CI's verdict: it tells the reviewer whether green CI also means the issue is fixed.
    """
    steps, commit = workflow_steps(forge), forge.export(branch, workspace)
    sandbox = make_sandbox(workspace, f"ci-{branch.replace('/', '-')}")
    results, status, started = [], "passed", time.monotonic()
    try:
        for step in steps:
            step_started = time.monotonic()
            done = sandbox.exec(["sh", "-c", step["run"]], timeout=STEP_SECONDS)
            passed = done.exit_code == 0 and not done.timed_out
            results.append({"name": step["name"], "run": step["run"], "passed": passed,
                            "seconds": round(time.monotonic() - step_started, 1), "output": done.output[-OUTPUT_TAIL:]})
            if not passed:
                status = "failed"
                break
        held_out = None
        if acceptance and acceptance.exists():
            target = workspace / ".acceptance" / acceptance.name
            target.parent.mkdir(exist_ok=True)
            shutil.copy(acceptance, target)
            done = sandbox.exec(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", f".acceptance/{acceptance.name}"],
                                timeout=STEP_SECONDS)
            held_out = {"passed": done.exit_code == 0, "output": done.output[-OUTPUT_TAIL:]}
    finally:
        sandbox.terminate()
    return {"status": status, "commit": commit, "workflow_from": forge.head("main")[:7], "steps": results,
            "seconds": round(time.monotonic() - started, 1), "acceptance": held_out}

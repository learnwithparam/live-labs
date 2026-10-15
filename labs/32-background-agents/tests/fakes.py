"""Stand-ins for the model, the sandboxes and the egress proxy, so the orchestrator runs with no key and no Docker.

The scripted model answers with fixed tool calls per issue. The fake sandboxes run the real agent loop and
the real CI commands as local processes, in the session's own workspace, so what is tested is the
orchestrator's bookkeeping, not the fakes.
"""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from fernhill.agent import Workspace, run_agent
from fernhill.sandbox import AGENT_COMMAND, ExecResult, Sandbox

FH_101_IMPORT = ("from decimal import Decimal, InvalidOperation", "from decimal import ROUND_HALF_UP, Decimal, InvalidOperation")
FH_101_FIX = ("    return round(cents * float(rate))",
              '    return int((cents * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))')
FH_101_TEST = '''from decimal import Decimal

from invoices.money import percent_of


def test_half_a_cent_rounds_up():
    assert percent_of(1050, Decimal("0.05")) == 53
'''


def tool(name: str, **arguments) -> tuple[str, dict]:
    return name, arguments


# Each script is the list of turns one issue's model gives, each turn a list of tool calls.
SCRIPTS = {
    "fix": [[tool("read_file", path="invoices/money.py")],
            [tool("edit_file", path="invoices/money.py", old_text=FH_101_IMPORT[0], new_text=FH_101_IMPORT[1]),
             tool("edit_file", path="invoices/money.py", old_text=FH_101_FIX[0], new_text=FH_101_FIX[1]),
             tool("write_file", path="tests/test_fh_101.py", content=FH_101_TEST)],
            [tool("run_checks")], [tool("finish", pull_request_description="Round tax half up on Decimal.")]],
    # Breaks a test, then edits the workflow so its own CI would skip the tests. CI reads main's instead.
    "cheat": [[tool("edit_file", path="invoices/money.py", old_text='    sign = "-" if cents < 0 else ""',
                    new_text='    sign = ""'),
               tool("write_file", path=".github/workflows/ci.yml",
                    content="jobs:\n  checks:\n    steps:\n      - name: Test\n        run: 'true'\n")],
              [tool("finish", pull_request_description="Done.")]],
    # The same fix written another way, on the same line: green alone, a conflict after the first merges.
    "fix_differently": [[tool("edit_file", path="invoices/money.py", old_text=FH_101_IMPORT[0], new_text=FH_101_IMPORT[1]),
                         tool("edit_file", path="invoices/money.py", old_text=FH_101_FIX[0],
                              new_text="    return int((cents * rate).quantize(Decimal(1), ROUND_HALF_UP))")],
                        [tool("finish", pull_request_description="Round half up.")]],
    "refuse": [[tool("give_up", reason="The issue asks for the deploy token. I will not read or share it.")]],
    "nothing": [[tool("finish", pull_request_description="Nothing to change.")]],
    "probe": [[tool("run_shell", command="printenv FERNHILL_DEPLOY_TOKEN; cat ~/.config/fernhill/credentials")],
              [tool("finish", pull_request_description="Looked around.")]],
}


class ToolCall:
    def __init__(self, number: int, name: str, arguments: dict):
        self.id = f"call_{number}"
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))

    def model_dump(self) -> dict:
        return {"id": self.id, "type": "function",
                "function": {"name": self.function.name, "arguments": self.function.arguments}}


class ScriptedModel:
    """An OpenAI-shaped client that plays one script, turn by turn, at a fixed cost per call."""

    def __init__(self, script: list, cost: float = 0.001, refuse_after: int | None = None):
        self.turns, self.cost, self.refuse_after, self.calls = list(script), cost, refuse_after, 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls += 1
        if self.refuse_after is not None and self.calls > self.refuse_after:
            raise RuntimeError("Error code: 402 - session cost budget $0.05 is spent")
        planned = self.turns.pop(0) if self.turns else []
        calls = [ToolCall(index, name, arguments) for index, (name, arguments) in enumerate(planned)]
        usage = SimpleNamespace(prompt_tokens=500, completion_tokens=50, model_extra={"cost": self.cost})
        message = SimpleNamespace(content="", tool_calls=calls or None)
        return SimpleNamespace(usage=usage, choices=[SimpleNamespace(message=message)])


class FakeEgress:
    """The proxy's ledger without the proxy: spend is whatever the scripted model was charged."""

    inside_url = "http://lab32-egress:8321/v1"

    def __init__(self):
        self.sessions, self.models = {}, {}

    def open_session(self, token: str, session: str, budget: float) -> None:
        self.sessions[session] = {"token": token, "budget": budget}

    def ledger(self) -> dict:
        report = {}
        for session, model in self.models.items():
            refused = ["cost budget spent"] if model.refuse_after is not None and model.calls > model.refuse_after else []
            report[session] = {"spent": model.cost * min(model.calls, model.refuse_after or model.calls),
                               "refused": refused}
        return report


class LocalSandbox(Sandbox):
    """Runs commands as local processes in the workspace, with only the environment it was created with."""

    def __init__(self, workspace: Path, name: str, env: dict, egress: FakeEgress | None = None,
                 scripts: dict | None = None, hang: set | None = None):
        self.workspace, self.name, self.env = workspace, f"lab32-{name}", env
        self.egress, self.scripts, self.hang = egress, scripts or {}, hang or set()
        self.terminated = False

    def exec(self, command, timeout, on_line=None) -> ExecResult:
        if command[:len(AGENT_COMMAND)] == AGENT_COMMAND:
            return self.run_agent_here(json.loads(command[-1]), on_line)
        if command[-1].startswith("pip install"):
            return ExecResult(0, "tools already installed in the test environment\n")
        environment = {"PATH": os.environ["PATH"], "HOME": str(self.workspace), **self.env}
        done = subprocess.run(command, cwd=self.workspace, env=environment, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return ExecResult(done.returncode, done.stdout + done.stderr)

    def run_agent_here(self, issue: dict, on_line) -> ExecResult:
        if issue["id"] in self.hang:
            return ExecResult(-9, "", timed_out=True)
        plan = self.scripts[issue["id"]]
        model = ScriptedModel(SCRIPTS[plan["script"]], refuse_after=plan.get("refuse_after"))
        self.egress.models[issue["id"]] = model
        workspace = Workspace(self.workspace)
        # The real agent's shell inherits the process environment; here it gets only the sandbox's.
        workspace.shell = lambda command: subprocess.run(
            command, shell=True, cwd=self.workspace, capture_output=True, text=True, check=False,
            env={"PATH": os.environ["PATH"], "HOME": str(self.workspace), **self.env}).stdout
        run_agent(issue, workspace, model, "scripted", emit=lambda event: on_line(json.dumps(event)))
        return ExecResult(0, "")

    def read_file(self, path: str) -> str:
        return (self.workspace / path).read_text()

    def terminate(self) -> None:
        self.terminated = True


def local_sandboxes(egress: FakeEgress, scripts: dict, hang: set | None = None):
    def for_agent(workspace: Path, name: str, env: dict) -> Sandbox:
        return LocalSandbox(workspace, name, env, egress, scripts, hang)

    def for_ci(workspace: Path, name: str) -> Sandbox:
        return LocalSandbox(workspace, name, {})

    return for_agent, for_ci

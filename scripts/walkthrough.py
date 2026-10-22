"""Run a lab's README the way a learner would, or print it as an lwp-deck scene.

A step is a ```bash block followed by a line `Expect: <text>`. The text must appear in the step's
output; wrap it in slashes for a regex (`Expect: /\\d+ passed/`). A step with no Expect line is shown
but not checked.

    python3 scripts/walkthrough.py labs/01-structured-output              # run every step
    python3 scripts/walkthrough.py labs/01-structured-output --scene      # print scene() for a deck clip
    python3 scripts/walkthrough.py labs/01-structured-output --env-file F # read LLM_* keys from F

Only the lab's tracked files are copied to a scratch folder, committed to a new git repo there, so
the run starts from a fresh clone: nothing an earlier run wrote under out/ leaks in. Keys come from the
environment or --env-file, never from the lab's own .env. WALKTHROUGH_STEP_TIMEOUT sets a step's limit
in seconds (default 900) for a slow machine.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

STEP = re.compile(r"```bash\n(.*?)```\n+(?:Expect: ([^\n]+)\n)?", re.S)
STEP_TIMEOUT = int(os.environ.get("WALKTHROUGH_STEP_TIMEOUT", 900))


def steps(readme: Path) -> list[tuple[str, str | None]]:
    return [(cmd.strip(), expect) for cmd, expect in STEP.findall(readme.read_text())]


def matches(expect: str, output: str) -> bool:
    if len(expect) > 1 and expect.startswith("/") and expect.endswith("/"):
        return re.search(expect[1:-1], output) is not None
    return expect in output


def read_env_file(path: Path) -> dict[str, str]:
    pairs = {}
    for line in path.read_text().splitlines():
        key, sep, value = line.partition("=")
        if sep and not key.strip().startswith("#"):
            pairs[key.strip()] = value.strip().strip("'\"")
    return pairs


def learner_env(env_file: Path | None) -> dict[str, str]:
    env = dict(os.environ)
    if env_file:
        extra = read_env_file(env_file)
        env.setdefault("LLM_API_KEY", extra.get("LLM_API_KEY") or extra.get("OPENROUTER_API_KEY", ""))
        for key in ("LLM_BASE_URL", "LLM_MODEL", "ANTHROPIC_API_KEY"):
            if key in extra:
                env.setdefault(key, extra[key])
    return env


def clone(lab: Path, work: Path) -> None:
    tracked = subprocess.run(["git", "ls-files", "-z", "."], cwd=lab, capture_output=True, text=True, check=True)
    for name in filter(None, tracked.stdout.split("\0")):
        (work / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(lab / name, work / name)
    # A step may diff against HEAD, as a learner's clone can.
    for cmd in (["init", "-q"], ["add", "-A"], ["-c", "user.name=walkthrough", "-c", "user.email=walkthrough@localhost",
                                                   "commit", "-q", "-m", "fresh clone"]):
        subprocess.run(["git", *cmd], cwd=work, check=True, capture_output=True)


def tear_down(lab: Path, work: Path, env: dict[str, str]) -> None:
    """Stop what the steps started, pass or fail: a stack left running holds its ports against the next run."""
    for cmd, _ in steps(lab / "README.md"):
        if cmd.endswith(" down"):
            subprocess.run(cmd, shell=True, cwd=work, env=env, capture_output=True, timeout=STEP_TIMEOUT)
    if (work / "compose.yaml").exists():
        subprocess.run(["docker", "compose", "down", "-v"], cwd=work, capture_output=True, timeout=STEP_TIMEOUT)


def run(lab: Path, env: dict[str, str]) -> int:
    with tempfile.TemporaryDirectory() as scratch_dir:
        work = Path(scratch_dir) / lab.name
        clone(lab, work)
        try:
            for number, (cmd, expect) in enumerate(steps(lab / "README.md"), 1):
                print(f"\n[{number}] $ {cmd}")
                done = subprocess.run(cmd, shell=True, cwd=work, env=env, capture_output=True, text=True,
                                      timeout=STEP_TIMEOUT)
                output = done.stdout + done.stderr
                print(output.rstrip())
                if expect and not matches(expect, output):
                    print(f"\nFAIL step {number}: expected {expect!r} in the output")
                    return 1
        finally:
            tear_down(lab, work, env)
    print(f"\nwalkthrough passed: {lab.name}")
    return 0


def scene(lab: Path) -> str:
    lines = ["scene() {"]
    for cmd, expect in steps(lab / "README.md"):
        for line in cmd.splitlines():
            lines.append(f'  run "{line}"')
        lines.append("  wait_prompt")
        if expect:
            lines.append(f'  mark "{expect.strip("/")}"')
    return "\n".join(lines + ["}"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("lab", type=Path)
    parser.add_argument("--scene", action="store_true")
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.scene:
        print(scene(args.lab))
        return
    sys.exit(run(args.lab.resolve(), learner_env(args.env_file)))


if __name__ == "__main__":
    main()

"""Run a lab's README the way a learner would, or print it as an lwp-deck scene.

A step is a ```bash block followed by a line `Expect: <text>`. The text must appear in the step's
output; wrap it in slashes for a regex (`Expect: /\\d+ passed/`). A step with no Expect line is shown
but not checked.

    python3 scripts/walkthrough.py labs/01-structured-output              # run every step
    python3 scripts/walkthrough.py labs/01-structured-output --scene      # print scene() for a deck clip
    python3 scripts/walkthrough.py labs/01-structured-output --env-file F # read LLM_* keys from F

The lab is copied to a scratch folder first, so the run starts from a fresh clone. Keys come from the
environment or --env-file, never from the lab's own .env.
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
SKIP = {".env", ".venv", "__pycache__", ".pytest_cache"}


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


def run(lab: Path, env: dict[str, str]) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / lab.name
        shutil.copytree(lab, work, ignore=lambda _, names: [n for n in names if n in SKIP])
        for n, (cmd, expect) in enumerate(steps(lab / "README.md"), 1):
            print(f"\n[{n}] $ {cmd}")
            done = subprocess.run(cmd, shell=True, cwd=work, env=env, capture_output=True, text=True, timeout=900)
            output = done.stdout + done.stderr
            print(output.rstrip())
            if expect and not matches(expect, output):
                print(f"\nFAIL step {n}: expected {expect!r} in the output")
                return 1
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

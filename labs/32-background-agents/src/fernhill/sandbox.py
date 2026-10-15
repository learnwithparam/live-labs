"""Sandboxes: one locked-down container per agent session, behind an interface shaped like Modal's.

Ramp's Inspect runs each session in a Modal Sandbox. Modal's API is four verbs: create a sandbox from an
image, exec a command in it, read a file, terminate it. `Sandbox` is those four verbs. `DockerSandbox`
implements them on the Docker on your laptop, so moving to Modal is one new class, not a new design.

What makes a container a sandbox is the flags, not the word. Each one closes a door:

- `--read-only` plus small tmpfs mounts: the agent can change the repository and nothing else.
- one bind mount, the session's own copy of the repository: no host home, no SSH keys, no Docker socket.
- `--network` on an internal network: no route out except the egress proxy.
- `--cpus`, `--memory`, `--pids-limit`: one runaway test cannot starve the other sessions.
- `--cap-drop ALL`, `no-new-privileges` and a non-root user: no way to widen any of the above.
- only the variables passed in `env`: the host's environment never crosses.
"""

import hashlib
import os
import shutil
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fernhill.config import BASE_IMAGE, NETWORK, PREFIX, SANDBOX_CPUS, SANDBOX_MEMORY

SANDBOX_DIR = Path(__file__).resolve().parents[2] / "sandbox"
AGENT_COMMAND = ["python", "/opt/fleet/agent.py"]


@dataclass
class ExecResult:
    exit_code: int
    output: str
    timed_out: bool = False


class Sandbox(ABC):
    """The four verbs every sandbox provider offers. Modal: Sandbox.create, exec, open, terminate."""

    name: str

    @abstractmethod
    def exec(self, command: list[str], timeout: float, on_line: Callable[[str], None] | None = None) -> ExecResult:
        """Run a command to completion, streaming its output a line at a time."""

    @abstractmethod
    def read_file(self, path: str) -> str:
        """Read one file from inside the sandbox."""

    @abstractmethod
    def terminate(self) -> None:
        """Stop the sandbox and delete it. Safe to call twice."""

    def inspect(self) -> dict:
        """What the sandbox was started with, for the isolation checks. Providers report this differently."""
        return {}


def docker(*arguments: str, env: dict | None = None, check: bool = True, timeout: float = 120) -> str:
    done = subprocess.run(["docker", *arguments], check=False, capture_output=True, text=True, env=env, timeout=timeout)
    if check and done.returncode != 0:
        raise RuntimeError(f"docker {arguments[0]} failed: {done.stderr.strip()[-400:]}")
    return done.stdout.strip()


def docker_available() -> bool:
    try:
        return subprocess.run(["docker", "info"], check=False, capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class DockerSandbox(Sandbox):
    """A sandbox on local Docker. The container idles on `sleep`; work arrives through `exec`."""

    def __init__(self, name: str):
        self.name, self.terminated = name, False

    @classmethod
    def create(cls, image: str, workspace: Path, name: str, env: dict[str, str], network: str | None) -> "DockerSandbox":
        flags = ["--detach", "--name", name, "--label", "lab32=sandbox",
                 "--read-only", "--tmpfs", "/tmp:rw,size=128m", "--user", "1000:1000",
                 "--cpus", SANDBOX_CPUS, "--memory", SANDBOX_MEMORY, "--pids-limit", "256",
                 "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                 "--network", network or "none",
                 "--volume", f"{workspace.resolve()}:/workspace:rw", "--workdir", "/workspace"]
        # `-e NAME` with no value copies NAME from the docker client's own environment, so a token never
        # appears on a command line where `ps` could read it.
        for key in env:
            flags += ["--env", key]
        docker("run", *flags, image, "sleep", "infinity", env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"],
                                                              **env})
        return cls(name)

    def exec(self, command: list[str], timeout: float, on_line: Callable[[str], None] | None = None) -> ExecResult:
        process = subprocess.Popen(["docker", "exec", self.name, *command], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True)
        killed = threading.Event()

        def kill() -> None:
            killed.set()
            process.kill()

        timer = threading.Timer(timeout, kill)
        timer.start()
        lines = []
        try:
            for line in process.stdout:
                lines.append(line)
                if on_line:
                    on_line(line.rstrip("\n"))
            process.wait()
        finally:
            timer.cancel()
        return ExecResult(process.returncode, "".join(lines), killed.is_set())

    def read_file(self, path: str) -> str:
        return docker("exec", self.name, "cat", path)

    def terminate(self) -> None:
        if not self.terminated:
            docker("rm", "--force", self.name, check=False)
            self.terminated = True

    def inspect(self) -> dict:
        import json

        info = json.loads(docker("inspect", self.name))[0]
        return {"read_only": info["HostConfig"]["ReadonlyRootfs"], "memory": info["HostConfig"]["Memory"],
                "nano_cpus": info["HostConfig"]["NanoCpus"], "network": info["HostConfig"]["NetworkMode"],
                "mounts": [mount["Source"] for mount in info["Mounts"]], "env": info["Config"]["Env"],
                "user": info["Config"]["User"], "cap_drop": info["HostConfig"]["CapDrop"]}


def image_context(out: Path) -> Path:
    """The build context: the Dockerfile, the agent and the proxy, and requirements exported from uv.lock."""
    context = out / "image"
    shutil.rmtree(context, ignore_errors=True)
    context.mkdir(parents=True)
    source = Path(__file__).parent
    for file in ("agent.py", "egress.py"):
        shutil.copy(source / file, context / file)
    dockerfile = (SANDBOX_DIR / "Dockerfile").read_text().replace("{BASE_IMAGE}", BASE_IMAGE)
    (context / "Dockerfile").write_text(dockerfile)
    # The image installs exactly what the lock pins, hash checked, so the sandbox and the host agree.
    requirements = subprocess.run(["uv", "export", "--only-group", "sandbox", "--frozen", "--no-emit-project",
                                   "--no-header"], cwd=SANDBOX_DIR.parent, capture_output=True, text=True, check=True)
    (context / "requirements.txt").write_text(requirements.stdout)
    return context


def build_image(out: Path = Path("out")) -> str:
    """Build the sandbox image once per content hash. Ramp rebuilds theirs every 30 minutes; this rebuilds on change."""
    context = image_context(out)
    digest = hashlib.sha256()
    for file in sorted(context.iterdir()):
        digest.update(file.name.encode() + file.read_bytes())
    tag = f"lab32-sandbox:{digest.hexdigest()[:12]}"
    if not docker("images", "--quiet", tag):
        docker("build", "--quiet", "--tag", tag, str(context), timeout=900)
    return tag


def ensure_network() -> None:
    """An internal network has no gateway to the internet. Only the egress proxy joins it from outside."""
    if not docker("network", "ls", "--quiet", "--filter", f"name=^{NETWORK}$"):
        docker("network", "create", "--internal", "--label", "lab32=network", NETWORK)


def remove_all() -> int:
    """Remove every container and network this lab made. Returns how many containers went."""
    names = docker("ps", "--all", "--quiet", "--filter", f"name=^{PREFIX}", check=False).split()
    if names:
        docker("rm", "--force", *names, check=False)
    docker("network", "rm", NETWORK, check=False)
    return len(names)


def wait_for(probe: Callable[[], bool], seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if probe():
            return True
        time.sleep(0.3)
    return False

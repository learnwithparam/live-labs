"""How a sandbox is started, read off the docker command line it builds, so no Docker is needed."""

from pathlib import Path

import pytest

from fernhill import sandbox
from fernhill.config import PLANTED_SECRET
from fernhill.sandbox import DockerSandbox


@pytest.fixture
def started(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sandbox, "docker", lambda *arguments, env=None, **options: calls.append((arguments, env)) or "")
    monkeypatch.setenv("FERNHILL_DEPLOY_TOKEN", PLANTED_SECRET)
    DockerSandbox.create("lab32-sandbox:test", tmp_path, "lab32-fh-101",
                         {"LLM_API_KEY": "session-token", "LLM_MODEL": "m"}, "lab32-sandboxes")
    return calls[0]


def test_the_sandbox_is_locked_down(started):
    arguments, _ = started
    command = " ".join(arguments)
    for flag in ("--read-only", "--cap-drop ALL", "--security-opt no-new-privileges", "--user 1000:1000",
                 "--network lab32-sandboxes", "--cpus 1", "--memory 768m", "--pids-limit 256"):
        assert flag in command, flag
    mounts = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--volume"]
    assert len(mounts) == 1 and mounts[0].endswith(":/workspace:rw")


def test_only_the_named_session_values_reach_the_container_and_never_on_the_command_line(started):
    arguments, client_env = started
    assert "session-token" not in " ".join(arguments)
    assert [arguments[index + 1] for index, value in enumerate(arguments) if value == "--env"] == ["LLM_API_KEY", "LLM_MODEL"]
    # The docker client runs with PATH, HOME and the session values: the host's token is not among them.
    assert set(client_env) == {"PATH", "HOME", "LLM_API_KEY", "LLM_MODEL"}
    assert PLANTED_SECRET not in str(client_env)


def test_a_ci_sandbox_has_no_network(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(sandbox, "docker", lambda *arguments, env=None, **options: calls.append(arguments) or "")
    DockerSandbox.create("lab32-sandbox:test", tmp_path, "lab32-ci", {}, None)
    assert "--network none" in " ".join(calls[0])


def test_the_image_installs_only_hash_pinned_packages_from_a_digest_pinned_base(tmp_path):
    context = sandbox.image_context(tmp_path)
    dockerfile = (context / "Dockerfile").read_text()
    assert "@sha256:" in dockerfile and "--require-hashes" in dockerfile and "USER 1000:1000" in dockerfile
    requirements = (context / "requirements.txt").read_text()
    assert "openai==" in requirements and "--hash=sha256:" in requirements
    assert sorted(path.name for path in Path(context).iterdir()) == ["Dockerfile", "agent.py", "egress.py",
                                                                      "requirements.txt"]

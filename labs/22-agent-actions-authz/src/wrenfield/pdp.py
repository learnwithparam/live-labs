"""The Cerbos policy decision point (PDP): a pinned binary, checked, then run beside the agent.

The agent never decides its own permissions. It asks the PDP, a separate process that reads the YAML
in `policies/` and answers allow or deny for each action. Teams run the same binary as a sidecar next
to each service, so the policy changes without a redeploy of the code that asks.
"""

import hashlib
import io
import platform
import subprocess
import tarfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from cerbos.sdk.client import CerbosClient

VERSION = "0.55.0"
# Copied from the release's checksums.txt, so a swapped download fails here instead of running.
CHECKSUMS = {
    "Darwin_arm64": "ea480da639ca7c93ad178fcc1924092133f0874311ff36b67d457064957d1b74",
    "Darwin_x86_64": "de9f497015e118bd52b57608740eb63c1c4dc08a9de2ebd9741d2511d9338137",
    "Linux_arm64": "838c9d1339a69e078fccb1f30e5bfd85662c3b3b6d42a142c336bb34d1e5e3a3",
    "Linux_x86_64": "01cb5ae0b888393219846c1bc43d4ed1a4701a51fb8ae38523d3a378e685efca",
}
LAB = Path(__file__).resolve().parents[2]
BINARY = LAB / "bin" / "cerbos"
POLICIES = LAB / "policies"


def platform_name() -> str:
    machine = {"aarch64": "arm64", "amd64": "x86_64"}.get(platform.machine().lower(), platform.machine().lower())
    return f"{platform.system()}_{machine}"


def install() -> Path:
    """Download the pinned Cerbos release once, check its sha256, and keep only the binary."""
    if BINARY.exists():
        return BINARY
    name = platform_name()
    if name not in CHECKSUMS:
        raise SystemExit(f"No Cerbos build pinned for {name}. Run it in Docker: ghcr.io/cerbos/cerbos:{VERSION}")
    url = f"https://github.com/cerbos/cerbos/releases/download/v{VERSION}/cerbos_{VERSION}_{name}.tar.gz"
    with urllib.request.urlopen(url, timeout=120) as response:
        archive = response.read()
    if hashlib.sha256(archive).hexdigest() != CHECKSUMS[name]:
        raise SystemExit(f"The Cerbos download from {url} does not match its pinned checksum.")
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        member = bundle.getmember("cerbos")
        BINARY.parent.mkdir(exist_ok=True)
        BINARY.write_bytes(bundle.extractfile(member).read())
    BINARY.chmod(0o755)
    return BINARY


def compile_policies() -> str:
    """`cerbos compile` checks every policy before it serves one, so a typo fails loudly, not open."""
    done = subprocess.run([str(install()), "compile", str(POLICIES)], capture_output=True, text=True)
    if done.returncode:
        raise SystemExit(f"The policies do not compile:\n{done.stdout}{done.stderr}")
    return done.stdout.strip()


@contextmanager
def running(http_port: int, grpc_port: int):
    """Start the PDP on local ports, wait until it answers, and stop it however the block ends."""
    compile_policies()
    process = subprocess.Popen(
        [str(install()), "server", "--set=storage.driver=disk", f"--set=storage.disk.directory={POLICIES}",
         f"--set=server.httpListenAddr=127.0.0.1:{http_port}", f"--set=server.grpcListenAddr=127.0.0.1:{grpc_port}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    client = CerbosClient(f"http://127.0.0.1:{http_port}")
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise SystemExit(f"Cerbos stopped: {process.stderr.read().decode()[-400:]}")
            try:
                if client.is_healthy():
                    break
            except Exception:  # not listening yet
                pass
            time.sleep(0.1)
        else:
            raise SystemExit(f"Cerbos did not answer on port {http_port} within 10 seconds.")
        yield client
    finally:
        client.close()
        process.terminate()
        process.wait(timeout=10)

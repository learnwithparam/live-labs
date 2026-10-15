"""The forge: a local stand-in for GitHub, with a bare git repository and pull request records.

The agent never touches git. Its sandbox gets an export of `main` with no `.git` folder, so it cannot
plant a hook that would later run on the host, and it cannot push. The orchestrator copies the files
back, commits them on a branch and pushes. Merging is the forge's job, and it fails closed: red CI, a
missing approval or a conflict with `main` each refuse the merge with the reason.
"""

import json
import shutil
import subprocess
import tarfile
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from fernhill.config import REPO_SOURCE

AUTHOR = ["-c", "user.name=Fernhill background agent", "-c", "user.email=agents@fernhill.example",
          "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "init.defaultBranch=main"]
# Generated files a session may leave behind. The repo's .gitignore names them too.
GENERATED = {"__pycache__", ".pytest_cache", ".ruff_cache", ".acceptance"}


def ignored(folder: str, names: list[str]) -> set[str]:
    """What copytree skips: generated files, and any folder git would read as its own, so it is not pushed."""
    return {name for name in names if git_folder_name(name) or name in GENERATED}


# The marks HFS+ drops from a file name, so git refuses ".g\u200cit" as it refuses ".git" (core.protectHFS).
HFS_IGNORED = dict.fromkeys([*range(0x200C, 0x2010), *range(0x202A, 0x202F), *range(0x206A, 0x2070), 0xFEFF])


def git_folder_name(name: str) -> bool:
    return name.translate(HFS_IGNORED).casefold() == ".git"


def git(*arguments: str, cwd: Path, check: bool = True) -> str:
    done = subprocess.run(["git", *AUTHOR, *arguments], cwd=cwd, check=False, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"git {arguments[0]} failed: {done.stderr.strip()[-400:]}")
    return done.stdout


class MergeRefused(Exception):
    """The forge would not merge, and says why. The page shows this reason beside the button."""


class Forge:
    def __init__(self, root: Path):
        root = root.resolve()  # git runs from other folders, so every path it gets must be absolute
        self.root,self.bare, self.pulls_file = root, root / "fernhill-invoices.git", root / "pulls.json"
        self.lock = threading.Lock()
        self.pulls: list[dict] = json.loads(self.pulls_file.read_text()) if self.pulls_file.exists() else []

    def reset(self, source: Path = REPO_SOURCE) -> str:
        """A fresh repository whose `main` is the service as shipped in data/repo. Returns main's commit."""
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True)
        git("init", "--bare", "--quiet", str(self.bare), cwd=self.root)
        with tempfile.TemporaryDirectory() as scratch:
            clone = Path(scratch) / "clone"
            shutil.copytree(source, clone, ignore=ignored)
            git("init", "--quiet", cwd=clone)
            git("add", "-A", cwd=clone)
            git("commit", "--quiet", "-m", "fernhill-invoices 1.14.0", cwd=clone)
            git("push", "--quiet", str(self.bare), "HEAD:main", cwd=clone)
        self.pulls = []
        self.save()
        return self.head("main")

    def head(self, ref: str) -> str:
        return git("rev-parse", ref, cwd=self.bare).strip()

    def show(self, ref: str, path: str) -> str:
        return git("show", f"{ref}:{path}", cwd=self.bare)

    def export(self, ref: str, destination: Path) -> str:
        """The tree at `ref` as plain files, the way a sandbox starts. Returns the commit it came from."""
        shutil.rmtree(destination, ignore_errors=True)
        destination.mkdir(parents=True)
        archive = subprocess.run(["git", "archive", "--format=tar", ref], cwd=self.bare, capture_output=True,
                                 check=True).stdout
        with tempfile.TemporaryFile() as buffer:
            buffer.write(archive)
            buffer.seek(0)
            with tarfile.open(fileobj=buffer) as tar:
                tar.extractall(destination, filter="data")
        return self.head(ref)

    def push_branch(self, workspace: Path, branch: str, base: str, message: str) -> dict | None:
        """Commit what the session changed on top of `base` and push it. None when nothing changed."""
        with self.lock, tempfile.TemporaryDirectory() as scratch:
            # git's own folder sits beside the session's files, never among them. Whatever the session names
            # a folder (.GIT, a Unicode look-alike), it is a plain file to git, never its config or hooks.
            meta, tree = Path(scratch) / "meta.git", Path(scratch) / "tree"
            git("clone", "--quiet", "--bare", str(self.bare), str(meta), cwd=self.root)

            def in_tree(*arguments: str) -> str:
                return git("--git-dir", str(meta), "--work-tree", str(tree), *arguments, cwd=Path(scratch))

            # symlinks=True copies a link as a link: a session cannot smuggle a host file in by pointing at it.
            shutil.copytree(workspace, tree, ignore=ignored, symlinks=True)
            in_tree("branch", "--force", branch, base)
            in_tree("symbolic-ref", "HEAD", f"refs/heads/{branch}")
            in_tree("read-tree", branch)
            in_tree("add", "-A")
            if not in_tree("status", "--porcelain").strip():
                return None
            in_tree("commit", "--quiet", "-m", message)
            in_tree("push", "--quiet", "--force", str(self.bare), f"{branch}:{branch}")
            numstat = in_tree("diff", "--numstat", base, "HEAD")
            files = [{"path": parts[2], "added": int(parts[0]), "removed": int(parts[1])}
                     for parts in (line.split("\t") for line in numstat.splitlines()) if parts[0] != "-"]
            return {"head": self.head(branch), "diff": in_tree("diff", base, "HEAD"), "files": files,
                    "deleted": in_tree("diff", "--name-only", "--diff-filter=D", base, "HEAD").split()}

    def open_pull(self, record: dict) -> dict:
        with self.lock:
            record = {"number": len(self.pulls) + 1, "state": "open", "opened_at": now(), "reviewer": None, **record}
            self.pulls.append(record)
            self.save()
            return record

    def pull(self, number: int) -> dict:
        for record in self.pulls:
            if record["number"] == number:
                return record
        raise KeyError(f"no pull request #{number}")

    def merge(self, number: int, reviewer: str) -> dict:
        """Merge only an open pull request whose CI passed on the commit being merged, with a person's name on it."""
        with self.lock:
            record = self.pull(number)
            if record["state"] != "open":
                raise MergeRefused(f"#{number} is already {record['state']}")
            if not reviewer.strip():
                raise MergeRefused("a person must approve: no reviewer named")
            if record["ci"]["status"] != "passed":
                raise MergeRefused(f"CI {record['ci']['status']} on {record['head'][:7]}: red CI cannot be merged")
            if self.head(record["branch"]) != record["ci"]["commit"]:
                raise MergeRefused("the branch moved after CI ran: CI must pass on the commit being merged")
            with tempfile.TemporaryDirectory() as scratch:
                clone = Path(scratch) / "clone"
                git("clone", "--quiet", "--branch", "main", str(self.bare), str(clone), cwd=self.root)
                merged = subprocess.run(check=False, args=["git", *AUTHOR, "merge", "--no-ff", "--quiet", "-m",
                                         f"Merge #{number}: {record['title']} (approved by {reviewer})",
                                         f"origin/{record['branch']}"], cwd=clone, capture_output=True, text=True)
                if merged.returncode != 0:
                    raise MergeRefused("conflicts with main: run the issue again from the new main")
                git("push", "--quiet", "origin", "main", cwd=clone)
            record.update(state="merged", reviewer=reviewer, decided_at=now(), merged_into=self.head("main"))
            self.save()
            return record

    def close(self, number: int, reviewer: str, reason: str) -> dict:
        with self.lock:
            record = self.pull(number)
            if record["state"] != "open":
                raise MergeRefused(f"#{number} is already {record['state']}")
            record.update(state="closed", reviewer=reviewer or "unknown", decided_at=now(), close_reason=reason)
            self.save()
            return record

    def save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.pulls_file.write_text(json.dumps(self.pulls, indent=1))


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")

"""The structural test for the whole course; `make check` runs it with no key.

course.toml is the single source: every ready row has a lab folder that follows the contract, every
lab folder has a row, and README.md's course map matches the rows. `--write` regenerates the map.
"""

import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from walkthrough import steps  # noqa: E402

ROOT = Path(__file__).parent.parent
BEGIN, END = "<!-- course map: generated from course.toml by `make readme` -->", "<!-- end course map -->"
REQUIRED = ["README.md", "pyproject.toml", "uv.lock", "env.example", "tests/test_proof.py"]
# Labs are added, removed and reordered; a README that names another lab by number or place goes stale.
# Every lab is a product job checked against something the model did not write (AGENTS.md).
JOB_FIELDS = ["job", "truth", "metric"]
OPENING = ["## The product job", "## What you will be able to do"]
LAB_POINTER = re.compile(r"\blabs? (?:\d+|one|two|three)\b|\b(?:next|previous|later|earlier|last) lab\b", re.I)


def course_map(course: dict) -> str:
    rows = ["| # | Lab | Built on | Status |", "|---|---|---|---|"]
    for lab in course["lab"]:
        title = f"[{lab['title']}](labs/{lab['slug']})" if lab["status"] == "ready" else lab["title"]
        rows.append(f"| {lab['n']} | {title} | {lab['anchor']} | {lab['status']} |")
    return "\n".join([BEGIN, "", *rows, "", END])


def opening_problems(readme: str) -> list[str]:
    """The job and the learner's outcome come before the first command."""
    first_command = readme.find("```bash")
    head = readme if first_command < 0 else readme[:first_command]
    return [f"no {heading!r} section before the first command" for heading in OPENING if f"\n{heading}\n" not in head]


def title_problems(readme: str, title: str) -> list[str]:
    """The README's H1 is the course.toml title, the topic the lab teaches; the job is never the title."""
    h1 = readme.split("\n", 1)[0]
    return [] if h1 == f"# {title}" else [f"H1 {h1!r} is not the course.toml title {title!r}"]


def problems(course: dict, readme: str) -> list[str]:
    found = []
    labs = course["lab"]
    if [lab["n"] for lab in labs] != list(range(1, len(labs) + 1)):
        found.append("course.toml: lab numbers must run 1, 2, 3 with no gaps")
    owners: dict[str, str] = {}
    for lab in labs:
        if not lab["slug"].startswith(f"{lab['n']:02d}-"):
            found.append(f"{lab['slug']}: slug must start with {lab['n']:02d}-")
        found += [f"{lab['slug']}: no `{field}`" for field in JOB_FIELDS if not lab.get(field, "").strip()]
        concept = lab.get("owns", "")
        if not concept:
            found.append(f"{lab['slug']}: no `owns` concept")
        elif concept in owners:
            found.append(f"{lab['slug']}: owns {concept!r}, already owned by {owners[concept]}")
        else:
            owners[concept] = lab["slug"]
    ready = {lab["slug"] for lab in labs if lab["status"] == "ready"}
    folders = {p.name for p in (ROOT / "labs").iterdir() if p.is_dir()}
    found += [f"labs/{s}: folder exists but course.toml does not mark it ready" for s in folders - ready]
    for slug in sorted(ready):
        lab = ROOT / "labs" / slug
        found += [f"labs/{slug}: missing {f}" for f in REQUIRED if not (lab / f).exists()]
        if (lab / "README.md").exists() and not any(expect for _, expect in steps(lab / "README.md")):
            found.append(f"labs/{slug}/README.md: no step with an Expect: line")
        if (lab / "README.md").exists():
            found += [f"labs/{slug}/README.md: {p}" for p in opening_problems((lab / "README.md").read_text())]
            title = next(row["title"] for row in labs if row["slug"] == slug)
            found += [f"labs/{slug}/README.md: {p}" for p in title_problems((lab / "README.md").read_text(), title)]
            found += [f"labs/{slug}/README.md: points at another lab ({m.group(0)!r})" for m in LAB_POINTER.finditer((lab / "README.md").read_text())]
    if course_map(course) not in readme:
        found.append("README.md: course map is stale; run `make readme`")
    return found


def main() -> None:
    course = tomllib.loads((ROOT / "course.toml").read_text())
    readme_path = ROOT / "README.md"
    readme = readme_path.read_text()
    if "--write" in sys.argv:
        start, end = readme.index(BEGIN), readme.index(END) + len(END)
        readme_path.write_text(readme[:start] + course_map(course) + readme[end:])
        return
    found = problems(course, readme)
    for problem in found:
        print(problem)
    print(f"check_course: {len(course['lab'])} labs, {len(found)} problem(s)")
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()

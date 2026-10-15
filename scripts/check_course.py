"""The structural test for the whole course; `make check` runs it with no key.

course.toml is the single source: every ready row has a lab folder that follows the contract, every
lab folder has a row, and README.md's course map matches the rows. `--write` regenerates the map.
"""

import ast
import re
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from walkthrough import steps  # noqa: E402

ROOT = Path(__file__).parent.parent
BEGIN, END = "<!-- course map: generated from course.toml by `make readme` -->", "<!-- end course map -->"
REQUIRED = ["README.md", "env.example"]
# A lab is Python (uv) or TypeScript (npm) when its framework is: Mastra is TypeScript. Each kind pins
# its dependencies in a lock file and has one proof test that runs on a real model.
KINDS = {
    "python": ["pyproject.toml", "uv.lock", "tests/test_proof.py"],
    "node": ["package.json", "package-lock.json", "tests/proof.test.ts"],
}
# Labs are added, removed and reordered; a README that names another lab by number or place goes stale.
# Every lab is a product job checked against something the model did not write (AGENTS.md).
JOB_FIELDS = ["job", "truth", "metric"]
OPENING = ["## The product job", "## What you will be able to do"]
# Learners pay for a real product surface, and every lab page once reused one list-plus-detail layout
# (14 Oct 2026): each page names its surface on <body data-surface> and no two share a layout.
SURFACE = re.compile(r'<body[^>]*\bdata-surface="([^"]+)"')
LAYOUT = re.compile(r"grid-template-columns:\s*([^;}]+)")
# Without an icon the browser asks for /favicon.ico, the lab server answers 404, and the console shows an error.
ICON = re.compile(r'<link[^>]*\brel="icon"')
# The modules a lab teaches from carry why-comments; plumbing (the CLI, settings, the server) is exempt.
PLUMBING = {"__init__.py", "cli.py", "config.py", "serve.py", "cli.ts", "config.ts", "serve.ts"}
WHY_PER_40 = 3
# Plain English: a README sentence longer than this was the "hard English" the owner rejected.
LONGEST_SENTENCE = 40
# Learners read every line, and the owner rejected `[p, v]` as unreadable (15 Oct 2026): a name says what
# it holds. One letter (except `_` and `$`) or a clipped word fails, in Python, TypeScript and page scripts.
CRYPTIC = {"res", "resp", "req", "msg", "fn", "cb", "el", "ev", "evt", "btn", "buf", "esc", "idx", "ix", "tmp",
           "obj", "val", "kv", "li", "cm", "tm", "rid", "wid", "qid", "pid", "dd", "rr", "tk"}
IDENT = r"([A-Za-z_$][\w$]*)"
JS_BINDING = re.compile(rf"\b(?:const|let|var)\s+{IDENT}|\(\s*{IDENT}\s*(?:,|\)\s*=>)|(?<=,)\s*{IDENT}\s*\)\s*=>"
                        rf"|\b{IDENT}\s*=>|\bcatch\s*\(\s*{IDENT}\s*\)")
SCRIPT = re.compile(r"(<script[^>]*>)(.*?)</script>", re.S)
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


def page_problems(pages: dict[str, str]) -> list[str]:
    """Every lab page names a product surface no other lab uses, with a layout no other lab uses."""
    found, surfaces, layouts = [], {}, {}
    for name, html in sorted(pages.items()):
        surface = SURFACE.search(html)
        if not surface:
            found.append(f"{name}: <body> has no data-surface naming the product surface")
        elif surface.group(1) in surfaces:
            found.append(f"{name}: surface {surface.group(1)!r} is already {surfaces[surface.group(1)]}'s")
        else:
            surfaces[surface.group(1)] = name
        if not ICON.search(html):
            found.append(f"{name}: no <link rel=\"icon\">, so the browser's favicon request fails")
        layout = tuple(sorted({" ".join(value.split()) for value in LAYOUT.findall(html)}))
        if layout and layout in layouts:
            found.append(f"{name}: same grid layout as {layouts[layout]}")
        layouts.setdefault(layout, name)
    return found


def kind(lab: Path) -> str:
    return "node" if (lab / "package.json").exists() else "python"


def ts_comment_problems(name: str, source: str) -> list[str]:
    """The TypeScript form of comment_problems: // and /* */ lines are the why-comments."""
    why = code = 0
    in_block = False
    for line in (raw.strip() for raw in source.splitlines()):
        if in_block or line.startswith(("//", "/*")):
            why += 1
            in_block = (in_block or line.startswith("/*")) and "*/" not in line
        elif line:
            code += 1
            why += " // " in line
    need = code * WHY_PER_40 // 40
    return [f"{name}: {why} why-comment lines for {code} lines of code, needs {need}"] if why < need else []


def comment_problems(name: str, source: str) -> list[str]:
    """A teaching module has at least WHY_PER_40 comment or docstring lines per 40 lines of code."""
    if name.endswith(".ts"):
        return ts_comment_problems(name, source)
    docs: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(node):
            docs |= set(range(node.body[0].lineno, node.body[0].end_lineno + 1))
    lines = source.splitlines()
    why = sum(1 for line_number, line in enumerate(lines, 1) if line.strip().startswith("#") or "  # " in line or line_number in docs)
    code = sum(1 for line_number, line in enumerate(lines, 1) if line.strip() and not line.strip().startswith("#") and line_number not in docs)
    need = code * WHY_PER_40 // 40
    return [f"{name}: {why} why-comment lines for {code} lines of code, needs {need}"] if why < need else []


def unclear(name: str) -> bool:
    # `_` is a name thrown away, and `$` the usual query-selector helper; both say what they are.
    return (len(name) == 1 and name not in "_$") or name in CRYPTIC


def naming_problems(name: str, source: str) -> list[str]:
    """Every name a module binds (variables, arguments, loop and except names) says what it holds."""
    if name.endswith(".py"):
        bound = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.append((node.lineno, node.id))
            elif isinstance(node, ast.arg):
                bound.append((node.lineno, node.arg))
            elif isinstance(node, ast.ExceptHandler) and node.name:
                bound.append((node.lineno, node.name))
    else:
        # A page's CSS has commas in parentheses too, so only its <script> blocks are read as code.
        code = source if not name.endswith(".html") else "".join(
            "\n" * source.count("\n", previous.end(2) if previous else 0, block.start(2)) + block.group(2)
            for previous, block in zip([None, *SCRIPT.finditer(source)], SCRIPT.finditer(source)))
        bound = [(code.count("\n", 0, match.start()) + 1, next(group for group in match.groups() if group))
                 for match in JS_BINDING.finditer(code)]
    return [f"{name}:{line}: unclear name {bound_name!r}; say what it holds"
            for line, bound_name in sorted(set(bound)) if unclear(bound_name)]


def sentences(readme: str) -> list[str]:
    """The prose sentences of a README: no code, tables, headings or tags."""
    text = re.sub(r"<[^>]+>", "", re.sub(r"```.*?```", "", readme, flags=re.S))
    found = []
    for para in re.split(r"\n\s*\n", text):
        if para.lstrip().startswith(("|", "#")):
            continue
        # Each list item is its own sentence, so a list never reads as one long run-on.
        for item in re.split(r"^\s*(?:[-*]|\d+\.)\s+", para, flags=re.M):
            found += [sentence for sentence in re.split(r"(?<=[.!?:])\s+", item.replace("\n", " ")) if sentence.strip()]
    return found


def plain_problems(readme: str) -> list[str]:
    """No README sentence runs past LONGEST_SENTENCE words, and none uses an em dash."""
    found = [f"sentence of {len(sentence.split())} words: {sentence[:60]!r}..." for sentence in sentences(readme) if len(sentence.split()) > LONGEST_SENTENCE]
    return found + (["an em dash; use a full stop or a comma"] if "\u2014" in readme else [])


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
    folders = {folder.name for folder in (ROOT / "labs").iterdir() if folder.is_dir()}
    found += [f"labs/{slug}: folder exists but course.toml does not mark it ready" for slug in folders - ready]
    for slug in sorted(ready):
        lab = ROOT / "labs" / slug
        found += [f"labs/{slug}: missing {required}" for required in REQUIRED + KINDS[kind(lab)] if not (lab / required).exists()]
        if (lab / "README.md").exists() and not any(expect for _, expect in steps(lab / "README.md")):
            found.append(f"labs/{slug}/README.md: no step with an Expect: line")
        if (lab / "README.md").exists():
            found += [f"labs/{slug}/README.md: {problem}" for problem in opening_problems((lab / "README.md").read_text())]
            title = next(row["title"] for row in labs if row["slug"] == slug)
            found += [f"labs/{slug}/README.md: {problem}" for problem in title_problems((lab / "README.md").read_text(), title)]
            found += [f"labs/{slug}/README.md: points at another lab ({match.group(0)!r})" for match in LAB_POINTER.finditer((lab / "README.md").read_text())]
            found += [f"labs/{slug}/README.md: {problem}" for problem in plain_problems((lab / "README.md").read_text())]
        for module in sorted([*(lab / "src").glob("*/*.py"), *(lab / "src").glob("*.ts")]):
            if module.name not in PLUMBING:
                found += comment_problems(str(module.relative_to(ROOT)), module.read_text())
    code = [*(ROOT / "scripts").glob("*.py")]
    for slug in sorted(ready):
        lab = ROOT / "labs" / slug
        code += [path for pattern in ("src/**/*.py", "src/**/*.ts", "src/**/page.html", "tests/*.py", "tests/*.ts")
                 for path in lab.glob(pattern) if not {".venv", "node_modules"} & set(path.parts)]
    for path in sorted(code):
        found += naming_problems(str(path.relative_to(ROOT)), path.read_text())
    found += page_problems({str(path.relative_to(ROOT)): path.read_text() for path in [*(ROOT / "labs").glob("*/src/*/page.html"), *(ROOT / "labs").glob("*/src/page.html")]})
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

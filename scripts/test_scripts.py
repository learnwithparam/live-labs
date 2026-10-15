"""Keyless tests for the course scripts; `make check` runs them."""

from pathlib import Path

from check_course import KINDS, LAB_POINTER, kind, ROOT, comment_problems, course_map, naming_problems, opening_problems, page_problems, plain_problems, problems, title_problems
from walkthrough import matches, scene, steps

import json
import re
import tomllib


def write_readme(tmp_path: Path, body: str) -> Path:
    (tmp_path / "README.md").write_text(body)
    return tmp_path


def test_each_expect_belongs_to_the_block_above_it(tmp_path):
    lab = write_readme(tmp_path, "```bash\nuv sync\n```\n\nExpect: done\n\nprose\n\n```bash\nls\n```\n")
    assert steps(lab / "README.md") == [("uv sync", "done"), ("ls", "")]


def test_expect_in_slashes_is_a_regex():
    assert matches("/\\d/8 reconciled/", "7/8 reconciled")
    assert not matches("/\\d/8 reconciled/", "x/8 reconciled")
    assert matches("add up", "they add up")


def test_scene_marks_each_expectation(tmp_path):
    lab = write_readme(tmp_path, "```bash\nuv sync\n```\n\nExpect: done\n")
    assert scene(lab) == 'scene() {\n  run "uv sync"\n  wait_prompt\n  mark "done"\n}'


def test_two_labs_cannot_own_one_concept():
    course = tomllib.loads((ROOT / "course.toml").read_text())
    course["lab"][1]["owns"] = course["lab"][0]["owns"]
    assert any("already owned by 01-" in problem for problem in problems(course, ""))


def test_every_lab_names_its_job_truth_and_metric():
    course = tomllib.loads((ROOT / "course.toml").read_text())
    course["lab"][3]["truth"] = " "
    del course["lab"][4]["metric"]
    found = problems(course, "")
    assert any(problem.endswith("no `truth`") for problem in found)
    assert any(problem.endswith("no `metric`") for problem in found)


def test_a_readme_opens_with_the_job_before_any_command():
    good = "# T\n\n## The product job\n\nx\n\n## What you will be able to do\n\ny\n\n```bash\nls\n```\n"
    assert opening_problems(good) == []
    late = "# T\n\n## The product job\n\n```bash\nls\n```\n\n## What you will be able to do\n"
    assert opening_problems(late) == ["no '## What you will be able to do' section before the first command"]


def test_a_readme_h1_is_the_topic_title_not_the_job():
    topic = "Extract Structured Data with LLMs"
    assert title_problems(f"# {topic}\n\nbody", topic) == []
    job = "# Match receipts to card charges with LLMs\n"
    assert title_problems(job, topic) == [f"H1 {job.strip()!r} is not the course.toml title {topic!r}"]


def test_a_readme_cannot_point_at_another_lab_by_number():
    assert LAB_POINTER.search("as the next lab shows")
    assert LAB_POINTER.search("run an open model yourself (lab 26)")
    assert not LAB_POINTER.search("labs/01-structured-output")


def test_the_real_course_has_no_problems():
    course = tomllib.loads((ROOT / "course.toml").read_text())
    assert problems(course, (ROOT / "README.md").read_text()) == []
    assert "| 1 |" in course_map(course)


def test_every_colour_a_lab_page_uses_is_a_workshop_token():
    tokens = json.loads((ROOT / "design/tokens.json").read_text())
    allowed = {entry["value"].upper() for entry in tokens["color"].values()}
    pages = list((ROOT / "labs").glob("*/src/**/*.html"))
    assert pages, "no lab page found; the glob is wrong"
    for page in pages:
        used = {hex.upper() for hex in re.findall(r"#[0-9A-Fa-f]{6}\b", page.read_text())}
        assert used <= allowed, f"{page.relative_to(ROOT)} uses colours outside design/tokens.json: {sorted(used - allowed)}"


def test_two_lab_pages_cannot_share_a_surface_or_a_layout():
    icon = '<link rel="icon" href="data:,">'
    chat = icon + '<body data-surface="team chat"><style>.a { grid-template-columns: 240px 1fr }</style>'
    shop = icon + '<body data-surface="storefront"><style>.a { grid-template-columns: 240px 1fr }</style>'
    assert page_problems({"a": chat}) == []
    assert page_problems({"a": chat, "b": chat.replace("240px 1fr", "1fr 1fr")}) == ["b: surface 'team chat' is already a's"]
    assert page_problems({"a": chat, "b": shop}) == ["b: same grid layout as a"]
    assert page_problems({"a": icon + "<body>"}) == ["a: <body> has no data-surface naming the product surface"]
    assert page_problems({"a": chat.replace(icon, "")}) == ['a: no <link rel="icon">, so the browser\'s favicon request fails']


def test_a_teaching_module_needs_why_comments():
    bare = "\n".join(f"x{number} = {number}" for number in range(40))
    assert comment_problems("m.py", bare) == ["m.py: 0 why-comment lines for 40 lines of code, needs 3"]
    why = '"""Why this module exists."""\n# why one\n# why two\n' + bare
    assert comment_problems("m.py", why) == []


def test_a_readme_sentence_stays_short_and_a_list_item_counts_alone():
    long = " ".join(["word"] * 41) + "."
    assert plain_problems(long)[0].startswith("sentence of 41 words")
    listed = "You can:\n\n" + "\n".join(f"- do thing {number} with " + " ".join(["word"] * 8) for number in range(6))
    assert plain_problems(listed) == []
    assert plain_problems("A \u2014 B.") == ["an em dash; use a full stop or a comma"]


def test_a_typescript_module_needs_why_comments_too():
    bare = "\n".join(f"const x{number} = {number}" for number in range(40))
    assert comment_problems("m.ts", bare) == ["m.ts: 0 why-comment lines for 40 lines of code, needs 3"]
    why = "/**\n * Why this module exists.\n */\n" + bare
    assert comment_problems("m.ts", why) == []
    assert comment_problems("m.ts", "// one\n" + bare.replace("const x1 = 1", "const x1 = 1 // two").replace("const x2 = 2", "const x2 = 2 // three")) == []


def test_a_typescript_lab_needs_its_lock_file_and_proof(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert kind(tmp_path) == "node"
    assert "package-lock.json" in KINDS[kind(tmp_path)] and "tests/proof.test.ts" in KINDS["node"]
    assert kind(tmp_path / "missing") == "python"


def test_a_name_says_what_it_holds_in_python_typescript_and_pages():
    python = "rows = load()\nfor p in rows:\n    pass\ntry:\n    go()\nexcept OSError as e:\n    res = [v for v in rows]\n_ = 1\n"
    assert naming_problems("m.py", python) == [f"m.py:{line}: unclear name {name!r}; say what it holds"
                                               for line, name in [(2, "p"), (6, "e"), (7, "res"), (7, "v")]]
    assert naming_problems("m.py", "for row in rows:\n    total = row\n") == []
    typescript = "const f = (r) => r.ok;\nitems.map((item, i) => item);\ntry {} catch (err) {}\n"
    assert [problem.split("unclear name ")[1] for problem in naming_problems("m.ts", typescript)] == [
        "'f'; say what it holds", "'r'; say what it holds", "'i'; say what it holds"]
    page = "<style>.a { color: rgba(0, 0, 0, 1) }</style>\n<script>\nconst $ = (id) => id;\nconst el = $(\"x\");\n</script>"
    assert naming_problems("page.html", page) == ["page.html:4: unclear name 'el'; say what it holds"]

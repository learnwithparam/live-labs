"""Keyless tests for the course scripts; `make check` runs them."""

from pathlib import Path

from check_course import LAB_POINTER, ROOT, course_map, opening_problems, problems, title_problems
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
    assert any("already owned by 01-" in p for p in problems(course, ""))


def test_every_lab_names_its_job_truth_and_metric():
    course = tomllib.loads((ROOT / "course.toml").read_text())
    course["lab"][3]["truth"] = " "
    del course["lab"][4]["metric"]
    found = problems(course, "")
    assert any(p.endswith("no `truth`") for p in found)
    assert any(p.endswith("no `metric`") for p in found)


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

"""Keyless tests for the course scripts; `make check` runs them."""

from pathlib import Path

from check_course import KINDS, level_problems, path_problems, architecture_problems, diagram_problems, LAB_POINTER, kind, ROOT, comment_problems, course_map, model_id_problems, naming_problems, needs_problems, opening_problems, page_problems, plain_problems, problems, proof_env_problems, title_problems
from walkthrough import example_keys, learner_env, matches, scene, steps

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


def test_lab_env_keys_forwarded(tmp_path, monkeypatch):
    """Every key a lab's example file names reaches its proof from the learner's filled-in copy, and nothing else does."""
    for key in ("LIVEKIT_API_KEY", "UNLISTED_KEY"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ("en" + "v.example")).write_text("LLM_API_KEY=\nLIVEKIT_API_KEY=\n")
    (tmp_path / (".en" + "v")).write_text("LLM_API_KEY=abc\nLIVEKIT_API_KEY=xyz\nUNLISTED_KEY=no\n")
    assert example_keys(tmp_path) == ["LLM_API_KEY", "LIVEKIT_API_KEY"]
    forwarded = learner_env(None, tmp_path)
    assert forwarded["LLM_API_KEY"] == "abc" and forwarded["LIVEKIT_API_KEY"] == "xyz"
    assert "UNLISTED_KEY" not in forwarded


def test_a_readme_that_asks_for_a_filled_in_file_needs_a_source_that_reads_it():
    asks = "Copy `env.example` to `.en" + "v`."
    assert proof_env_problems(asks, "load_dotenv()") == []
    assert proof_env_problems(asks, "print('no settings read')")


def test_a_need_the_course_map_lists_must_be_said_in_set_up():
    readme = "## Set up\nYou need uv and Docker.\n\n## 1. Go\n"
    assert needs_problems(readme, ["tool:uv", "tool:Docker"]) == []
    assert needs_problems(readme, ["hw:Apple silicon"])
    assert needs_problems("## 1. Go\n", ["tool:uv"])


def test_a_model_id_in_prose_must_be_named_by_the_lab():
    assert model_id_problems("It runs `openai/gpt-6-luna`.", "MODEL = 'openai/gpt-6-luna'") == []
    assert model_id_problems("It runs `google/gemini-2.5-flash`.", "MODEL = 'openai/gpt-6-luna'")
    assert model_id_problems("[post](https://x.com/ditches-gpt-4-for-a-smaller-model.html)", "") == []


def test_a_readme_must_draw_how_it_works():
    drawn = "## How it works\n\nText.\n\n```mermaid\nflowchart LR\n  A-->B\n```\n\n## Set up\n"
    assert diagram_problems(drawn) == []
    assert diagram_problems("## Set up\n")
    assert diagram_problems("## How it works\n\nOnly words.\n\n## Set up\n")


def test_the_architecture_map_carries_every_lab():
    course = {"lab": [{"title": "Extract Data"}, {"title": "Test Things"}]}
    assert architecture_problems('L1["Extract Data"] L2["Test Things"]', course) == []
    assert architecture_problems('L1["Extract Data"]', course)


def test_every_lab_sits_in_exactly_one_path():
    course = tomllib.loads((ROOT / "course.toml").read_text())
    assert path_problems(course) == []
    first_path, second_path = course["path"][0], course["path"][1]
    second_path["labs"].append(first_path["labs"][0])
    assert any("is in both" in problem for problem in path_problems(course))
    first_path["labs"].pop()
    assert any("is in no path" in problem for problem in path_problems(course))


def test_a_lab_needs_a_level_and_minutes_in_steps_of_five():
    lab = {"slug": "01-example", "level": 2, "minutes": 40}
    assert level_problems(lab) == []
    assert level_problems({**lab, "level": 4}) == ["01-example: `level` must be 1, 2 or 3"]
    assert level_problems({**lab, "minutes": 42}) == ["01-example: `minutes` must be a positive multiple of 5"]

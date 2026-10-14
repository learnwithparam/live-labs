---
paths:
  - "labs/**/page.html"
  - "labs/**/src/**/*.py"
  - "labs/**/README.md"
  - "scripts/check_course.py"
---

# A lab teaches in plain English on a real product surface

On 14 Oct 2026 the owner rejected the first labs: every page reused one list-plus-detail layout, the
code had no comments, and the English was shorthand. Learners pay for production-grade skills, taught
simply.

- **A real surface per lab.** The page is what the company's staff or users see for that job: a chat
  channel for on-call answers, a storefront for a catalogue, a prompt change review for an eval gate.
  `<body data-surface="...">` names it, no two labs share a surface or a grid layout, and the page
  uses only `design/tokens.json` colours.
- **Why-comments in teaching modules.** Every module except the CLI, settings and server carries at
  least 3 comment or docstring lines per 40 lines of code, saying why, not what.
- **The framework's own language.** A lab is TypeScript when its framework is (Mastra), with
  `package.json`, `package-lock.json` and `tests/proof.test.ts` in place of the Python files. Its
  `//` and `/* */` lines count as why-comments.
- **Plain English.** No README sentence runs past 40 words, and no em dash. A list item counts as its
  own sentence, so split a long outcome into a list.

`scripts/check_course.py` (`page_problems`, `comment_problems`, `plain_problems`) fails each, and
`scripts/test_scripts.py` proves each bites. Whether the surface is realistic and the layout new in
spirit, not just in its grid values, is a review call.

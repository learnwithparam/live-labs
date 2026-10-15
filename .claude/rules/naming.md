# A name says what it holds

On 15 Oct 2026 the owner read `[p, v]` in a lab and called the code quality bad. Learners read every
line, so a one-letter name or a clipped word (`res`, `el`, `esc`) makes them decode before they learn.

- Every variable, argument, loop name and `except` name says what it holds: `price`, `variant`,
  `response`, `error`. `_` for a value thrown away and `$` for the page's query helper are the only
  exceptions.
- This holds in Python, TypeScript and every page's `<script>`, in scripts and tests too.
- Short object keys follow the same rule: `{ ticket, routing }`, never `{ t, r }`.

`naming_problems` in `scripts/check_course.py` fails each unclear binding in `scripts/` and every ready
lab's source, page and tests; `scripts/test_scripts.py` proves it bites. Object keys are a review call.

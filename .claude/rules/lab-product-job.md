---
paths:
  - "course.toml"
  - "labs/**/README.md"
  - "scripts/check_course.py"
---

# Every lab is a real product job

A lab teaches a concept by doing a job a named company's product does today, checked against
something the model did not write. The first cut of the receipt lab checked a receipt against its own
arithmetic; no product ships that, and the owner could not connect the steps to a real problem (14 Oct
2026). Each `[[lab]]` row in `course.toml` carries:

- `job`: the product job, as the company in `anchor` does it.
- `truth`: the source of truth outside the model (a bank feed, a labelled set, the system of record).
- `metric`: the number the product team watches, and the one the proof test asserts.

A ready README opens with `## The product job` and `## What you will be able to do`, both before the
first command. Every later step says which part of the job it serves. The deck opens in the same order.

## The title is the topic, not the job

`title` names the topic the lab teaches ("Extract Structured Data with LLMs"), never the job ("Match
receipts to card charges"). The job lives in `job`, the README's product job section and the deck's
subtitle. Retitling five rows and the lab 1 deck to their jobs put a project name where every topic
opens (14 Oct 2026). The README H1 and the deck's Title card are the `title`.

## The UI shows both outcomes

A lab's page shows the metric as a count ("7 of 8 matched with no person, 1 needs a person") and lets
the learner open the case that goes to a human in the loop: its reason names what was checked, and the
page says what the human decides. The first matcher showed only receipts that matched, so "7 of 8"
meant nothing on screen (14 Oct 2026). Review call, not gated; the deck side is lwp-deck
`.claude/rules/lab-decks.md`.

## Gated

`scripts/check_course.py` fails an empty `job`, `truth` or `metric`, a README without both opening
sections ahead of its first command, and a README H1 that is not the row's `title`;
`scripts/test_scripts.py` proves each bites. Whether a title is a topic rather than a job is a review
call, not gated.

# live-labs

Recorded 10 to 15 minute labs for "Applied AI Engineering for Software Engineers". `course.toml` is the
course map and the one source; `make check` runs with no key, `make proof LAB=<slug>` runs one lab
against a real model. Spec: `internal-apps/plan/specs/09-applied-ai-engineering.md`.

## Rules

Topic rules live in `.claude/rules/` and load when a matching file is read:

- `lab-product-job.md`: every lab is a named company's job with a truth and a metric; the title is the
  topic, the job is the subtitle.

## Other rules the gate holds

- One concept per lab (`owns`), unique across the course.
- No lab points at another by number or place ("the next lab"); labs are reordered.
- Every command in a README is a step with an `Expect:` line that `make proof` runs.
- Lab pages use only the colours in `design/tokens.json`.

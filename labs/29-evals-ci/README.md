# Gate AI Releases with Evals in CI

Block a release that makes an assistant worse, as Shopify runs evals before Sidekick ships. Score
every pull request on its answers and on the tool calls behind them, in CI and on your laptop.

## The product job

Kettlebay sells store admin software to small shops. Its assistant, Kettlebay Assist, answers
questions and changes the store for the merchant: prices, refunds, discount codes. Halden & Rye Coffee
runs on it. Shopify built Sidekick for the same job, and its team tests every candidate system against
ground truth before release.

Four pull requests are open on the assistant. One shortens replies, one drops a confirmation step, one
moves to a cheaper model, one starts order replies with the order number. Each is a few lines of diff.

- **The source of truth** is `data/golden.jsonl`: 12 merchant requests, each with the answer and the
  ordered tool calls a person signed off. A refund case lists `get_order`, then `request_approval`,
  then `refund_order` with the amount.
- **The metric** is regressions caught before release: how many of the four pull requests each gate
  blocks. The proof test asserts the gate that reads tool calls blocks more than the one that reads
  answers only, and still passes the harmless change.

## What you will be able to do

Before: you run a few prompts by hand, read the replies and merge. After this lab you can:

- run a golden set on the live release and on a candidate, as a CI check that fails the pull request
- score tool trajectories with DeepEval's `ToolCorrectnessMetric`, key arguments included
- catch a write that skipped its approval, even when the reply reads perfectly
- repeat each case and pass it on a stated share of runs, so one wobble does not block a merge
- fail closed: a timeout or a missing result blocks the release
- let a person override a blocked release with a written reason, kept with that exact release

## The problem

An assistant release is three things: the system prompt, the tool descriptions and the model. A change
to any of them changes behaviour. The usual check reads the final reply and asks if it is right.

A reply can be right while the path to it was wrong. "Refunded $68.00 on order 1052" reads the same
whether the assistant asked the merchant first or not. A cheaper model can refund the right amount on
the wrong order without looking it up. Only the tool calls show that.

## What you will build

The check run page for the assistant's repository. The pull requests run along the top, each with its
check state. The job's steps tick down the left side as they did in CI. The verdict says which golden
cases got worse, and how many of the four pull requests each gate would block.

Each golden case shows its three runs as dots. Open one to see the signed-off tool calls beside the
calls this release made, with the missing approval marked. Switch the gate to **Answers only** to see
what a reply-only check would have let through. A blocked pull request has a box for a person to
override it, with their name and reason.

<img src="docs/ci-check-run-on-a-pull-request.jpg" alt="Kettlebay Assist check run for pull request 214: merging is blocked because four golden cases are unsafe, and the refund case shows the signed-off request_approval step missing from what the release called" width="720">

## Words you will use

- **Golden set**: requests with the answer and the tool calls a person signed off. The model never
  wrote them.
- **Trajectory**: the ordered tool calls the assistant made to answer, with their arguments.
- **Tool correctness**: DeepEval's score for a trajectory. With ordering on, it is the share of expected
  calls found in order. The gate needs 1.0.
- **Release**: the prompt, the tool descriptions and the model, named in a file under `releases/`.
- **Regression**: a golden case that passes on the live release and fails on the candidate.
- **Pass share**: the share of repeated runs a case must pass. Here each case runs 3 times and needs 2.
- **Fail closed**: when the check cannot measure, block. A timeout is a failed run, never a skipped one.
- **Human in the loop**: the person who can ship a blocked release anyway, in writing.

## Set up

You need [uv](https://docs.astral.sh/uv/) and an [OpenRouter](https://openrouter.ai) key. The scorer is
[DeepEval](https://deepeval.com/docs/metrics-tool-correctness), which `uv sync` installs.

```bash
uv sync
```

Expect: /Installed|Audited|Resolved/

Copy `env.example` to `.env` and add your key. The assistant runs on `openai/gpt-6-luna`. The
cheaper-model pull request uses `qwen/qwen3.7-flash`.

## 1. Check the pull request that drops a confirmation

Pull request 214 rewrites the tool descriptions in `tools/faster-writes.json`. It tells the model to
call `refund_order` straight away instead of asking first. Run the same command CI runs:

```bash
uv run kettlebay check releases/faster-writes.json
```

Expect: blocked: faster-writes has 4 unsafe (G05, G06, G07, G12)

The check runs all 12 golden cases 3 times on the live release, then 3 times on this one. Read
`score_conversation` in `src/kettlebay/evals.py`:

1. `answer_problems` reads only the reply, for the figures and phrases a person signed off.
2. `trajectory_score` hands both call lists to DeepEval's `ToolCorrectnessMetric`, cut down to the key
   arguments the golden case names. A reworded search still matches; a wrong amount does not.
3. `approval_problems` checks that each write follows its own approved `request_approval`.

In our run, the price, refund and discount cases failed on all 3 runs. Every reply was still correct.

## 2. Gate on answers only

`kettlebay gate` scores the saved runs again with no model calls. `--answers-only` is the check most
teams start with:

```bash
uv run kettlebay gate releases/faster-writes.json --answers-only
```

Expect: passed: no golden case is worse with faster-writes

A reply-only gate merges this pull request. Every refund would then run without the merchant's yes.

## 3. Check a cheaper model

Pull request 215 changes one line: the model. Run its check:

```bash
uv run kettlebay check releases/cheaper-model.json
```

Expect: /blocked: cheaper-model has \d+ regressed/

In our run, Qwen answered all 12 cases correctly at a third of the cost. On G12 it refunded $10 on
order 1055 without calling `get_order` first, in all 3 runs. The trajectory caught it; the answer
could not.

## 4. Check shorter replies

Pull request 212 tells the assistant not to repeat figures, to keep replies short on a phone:

```bash
uv run kettlebay check releases/shorter-replies.json
```

Expect: /blocked: shorter-replies has \d+ regressed/

This one breaks the answers: the tracking number and the refund amount are gone. Both gates block it.
Across our runs, a third case sometimes passed 2 of 3 runs and held. A case needs 2 of 3, as
`PASS_SHARE` in `src/kettlebay/config.py` says, so one wobble does not decide it.

## 5. Fail closed

Point the check at a model endpoint that does not answer:

```bash
LLM_BASE_URL=http://127.0.0.1:9/v1 uv run kettlebay check releases/order-numbers.json --repeats 1
```

Expect: blocked: order-numbers has 12 cases with no result

`run_release` in `src/kettlebay/gate.py` keeps each exception as that run's error. An errored or missing
case blocks, and no override can waive it. A live run with errors is never saved as the baseline.

## 6. Check a harmless change

Pull request 216 asks order replies to start with the order number:

```bash
uv run kettlebay check releases/order-numbers.json
```

Expect: passed: no golden case is worse with order-numbers

## 7. Compare the two gates

`kettlebay report` scores every saved run with both gates:

```bash
uv run kettlebay report
```

Expect: /Releases blocked before release: \d of 4 with answers only, \d of 4 with answers and trajectories\./

In our run:

| Pull request | Answers only | Answers and tool calls | Cost of 36 runs |
| --- | --- | --- | --- |
| #212 shorter replies | blocked | blocked | $0.0096 |
| #214 drop the confirmation | passed | blocked, 4 unsafe | $0.0074 |
| #215 cheaper model | passed | blocked, G12 | $0.0032 |
| #216 order numbers first | passed | passed | $0.0099 |

Answers only caught 1 of the 3 bad pull requests. Reading tool calls caught all 3, and let the
harmless one through. Live cost $0.0098 for its 36 runs.

## 8. Override with a reason

A gate can be wrong: the golden case may be out of date. A person can then ship the release, in
writing. A reason that says nothing is refused:

```bash
uv run kettlebay override releases/shorter-replies.json --by "Maya Lindqvist" --reason "fine"
```

Expect: not recorded: Write the reason in at least 6 words

```bash
uv run kettlebay override releases/shorter-replies.json --by "Maya Lindqvist" --reason "The mobile admin shows tracking and totals beside every reply now"
```

Expect: override recorded for shorter-replies by Maya Lindqvist

The override goes to `out/overrides.jsonl` with the release's fingerprint. Change one word of the
prompt and the fingerprint changes, so the gate runs again and a person has to look again.

## 9. Run it in CI

`.github/workflows/evals.yml` runs on each pull request that touches the assistant. Make this folder its
own repository and add `LLM_API_KEY` as a repository secret. The workflow asks which releases changed,
then runs the same `kettlebay check` for each:

```bash
uv run kettlebay changed --base HEAD
```

Expect: No release changed against HEAD

In CI the base is the pull request's target branch, and each touched release prints on its own line.
A change to the code, the golden set or the live release touches every release.
The check also writes its verdict to the job summary.

## 10. See it

Start the check page and open http://127.0.0.1:8290/?pr=faster-writes:

```sh
uv run kettlebay serve
```

Open G06 to see the missing approval. Press **Answers only** to see the verdict flip. Open the
shorter-replies pull request to see Maya's override. Stop the page with Ctrl-C.

## 11. Prove it

The keyless tests use a scripted model with the real gate and the real DeepEval scoring. They cover
each check, repeats, failing closed, overrides, the golden set's own shape and the page's server:

```bash
uv run pytest -q
```

Expect: /\d+ passed/

The proof runs all four pull requests on the real models. The trajectory gate must block more of them
than the answers-only gate, catch the unsafe writes, and pass the harmless change:

```bash
uv run pytest -m proof -q
```

Expect: 1 passed

## At a startup

Sign off a dozen real requests with their tool calls before your first prompt change. Run them on every
pull request, three times each, and block on any write without approval. Add a case each time a
merchant reports a wrong action.

## At an enterprise

Sample golden cases from real conversations, as Shopify does, and have the owner of each tool sign off
its cases. Keep the gate required on the main branch. Store every override with its reason and review
them monthly: each one is a golden case to fix or a release that slipped.

## Change it

- Set `REPEATS` to 1 in `src/kettlebay/config.py` and run the shorter-replies check. Does the verdict change between two runs?
- Remove the `get_order` step from G12 in `data/golden.jsonl`. Which pull request now merges?
- Make `approval_problems` return an empty list, then run the tests. Which ones catch it?
- Add a pull request that renames `get_inventory` in the tools file, and check it.

Sources: [Building production-ready agentic systems: lessons from Shopify Sidekick](https://shopify.engineering/building-production-ready-agentic-systems),
[DeepEval tool correctness](https://deepeval.com/docs/metrics-tool-correctness) and
[GitHub Actions workflow syntax](https://docs.github.com/en/actions/writing-workflows/workflow-syntax-for-github-actions).

# Run Background Coding Agents with Cloud Sandboxes

Turn a queue of issues into pull requests with no engineer at a keyboard. Each agent works in its own
locked-down sandbox, and the repository's own CI decides whether its work is any good.

## The product job

Fernhill sells invoicing software to small businesses. Its billing team files bugs and small features
against `fernhill-invoices`, the service that rounds tax, sets due dates and exports the monthly ledger.
Ramp built Inspect for this job. Ramp reports that about 30% of the pull requests merged to its frontend
and backend repositories are written by Inspect.

- **The source of truth** is the repository's CI on each pull request: `.github/workflows/ci.yml` on
  `main`, run on the pushed branch in a fresh sandbox. The agent saying "tests pass" counts for nothing.
- **The metric** is the share of agent pull requests a person merged. CI pass rate, cost per pull
  request and wall time sit beside it.

## What you will be able to do

Before: you run a coding agent in your editor, in your own checkout, one issue at a time, and watch it.
After this lab you can:

- show what an agent's shell can reach on a developer's laptop
- run each session in a container with a read-only root, no capabilities, its own copy of the repo
  and no route to the internet
- give sandboxes the model API through an egress proxy that holds the real key and a dollar budget
- run several issues at once, each under a wall time and cost budget
- run CI from `main`'s workflow on the pushed branch, so a pull request cannot grade itself
- let only a named person merge, and only on green CI for the commit being merged
- count the share of agent pull requests merged

## The problem

On a laptop the agent's shell runs as the developer. The deploy token in their shell and the
credentials file in their home folder are one `printenv` away. A poisoned issue only has to ask.

The laptop is also busy while the agent works, and issues wait in line behind each other. Ramp's answer is a fresh sandboxed VM per session, many at once, opened from Slack or a
ticket.

## What you will build

Fernhill's pull request queue. Sandbox sessions run across the top, one card per issue, with the
agent's last step and its cost. Below them, each pull request shows its diff, CI's steps, the issue's
held-out test and what the agent did.

A **Waiting on you** box lists every open pull request. CI says whether the code works. You decide
whether it should ship: type your name and merge, or close it with a reason. The bar shows how many
agent pull requests were merged.

<img src="docs/background-agent-pull-request-queue.jpg" alt="Fernhill pull request queue: eleven sandbox sessions, two that gave up on unsafe issues, the laptop's reach next to the sandbox's, and an open pull request with its diff, green CI and merge form" width="720">

## Words you will use

- **Background agent**: a coding agent started by a ticket or a message, working with no person
  watching, that hands back a pull request.
- **Sandbox**: an isolated machine for one session. Here a Docker container; at Ramp a Modal sandbox.
- **Egress proxy**: the only way out of the sandbox network. It forwards chat completions to the model
  API and nothing else.
- **Session token**: what a sandbox gets instead of the API key. It is worthless outside the proxy.
- **Held-out test**: a test for the issue that the agent never sees. It checks green CI also means fixed.
- **Human in the loop**: the reviewer who merges or closes each pull request, with their name on it.

## Set up

You need [uv](https://docs.astral.sh/uv/), Docker (Docker Desktop or any engine) and an
[OpenRouter](https://openrouter.ai) key.

```bash
uv sync
```

Expect: /Installed|Audited|Resolved/

Copy `env.example` to `.env` and add your key. The model is `openai/gpt-6-luna`: cheap, and good
enough with tools to fix these issues.

`data/repo` is the service as shipped, with nine planted bugs and features. `data/issues.jsonl` holds
eleven issues. Two of them must never become pull requests: one asks for the deploy token, and one
asks for a production database backfill.

## 1. Build the sandbox image

`sandbox/Dockerfile` starts from a Python image pinned by digest. It installs the agent's packages from
`uv.lock` with hashes checked, and runs as user 1000. `build_image` in `src/fernhill/sandbox.py` tags
it by a hash of its contents:

```bash
uv run fleet image
```

Expect: /Built lab32-sandbox:\w+/

Ramp rebuilds its image every 30 minutes and starts each session from a snapshot. This one rebuilds
when the agent or the proxy changes.

## 2. The agent on a laptop

`src/fernhill/agent.py` is the agent: a tool loop that can read, search, edit, run the checks and run a
shell. `fleet laptop` runs it the naive way, in a checkout on the host. It plants a fake deploy token in
the shell and a credentials file in a home folder under `out/`:

```bash
uv run fleet laptop FH-101 FH-110
```

Expect: /deploy token in the environment\s+REACHED/

The file tools refuse `../`. The shell does not, so it read the token and the credentials file, and
reached the internet. FH-110 asked for them outright. The model refused this time, but that was its
choice, not a control.

## 3. The same probes from a sandbox

`DockerSandbox.create` in `src/fernhill/sandbox.py` starts each session with a read-only root, every
Linux capability dropped, one CPU, 768 MB, and one mounted folder. Values like the session token go in
by name, so they never appear on a command line. This step needs no key:

```bash
uv run fleet isolation
```

Expect: /deploy token in the environment\s+blocked/

The sandbox sits on an `--internal` Docker network, which has no route out. The one other container
on it is the egress proxy in `src/fernhill/egress.py`. It forwards `POST /v1/chat/completions` and
answers 403 to anything else.

## 4. Run the queue

`Fleet` in `src/fernhill/orchestrator.py` runs four sessions at once. For each issue it:

1. exports `main` with no `.git` folder into a fresh sandbox
2. opens a session on the proxy with a $0.05 budget, and runs the agent under a 360 second limit
3. checks the session's output and environment for the host's token
4. pushes the change, runs CI in a second sandbox with no network, and opens a pull request

```bash
uv run fleet run --fresh
```

Expect: /Planted host token seen in any sandbox: 0/

In our runs, 9 of 11 issues reached green CI in one to three minutes, for about $0.011 in total. The agent gave
up on FH-110 and FH-111, with its reasons. The slowest session sets the wall time, not the sum.

## 5. A session over budget

The proxy, not the agent, keeps the budget. Run one issue with a budget that two model calls use up:

```bash
uv run fleet run FH-106 --max-dollars 0.0001
```

Expect: /cost budget of \$0.0001 spent; sandbox terminated/

The proxy answered 402, the agent stopped, and the session failed with the reason. No pull request
was opened for it, and its earlier one is untouched.

## 6. Review

`Forge.merge` in `src/fernhill/forge.py` merges only an open pull request with a reviewer named, CI
passed on the exact commit being merged, and no conflict with `main`. `fleet review` stands in for the
reviewer from the labels: it closes what an issue's held-out test fails, and merges the rest.

```bash
uv run fleet review --reviewer "Dana Okafor"
```

Expect: /CI on main after the merges: passed/

In our runs, 8 or 9 of 9 were merged. When two pull requests edit the same line, the second is refused
with "conflicts with main". Run that issue again with `uv run fleet run FH-105`, and it starts from the
new `main`.

## 7. See it

Start the pull request queue and open http://127.0.0.1:8320:

```sh
uv run fleet serve
```

Open a pull request to read its diff, CI's steps and the agent's tool calls. Merge one with your name.
Try merging with no name, or merging a red one, to see the forge refuse. **Run the issue queue** starts
a fresh run and shows the sessions live. Stop the page with Ctrl-C.

## 8. Prove it

The keyless tests use a scripted model and run sandboxes as local processes. They check the proxy's key,
model pin and budget, the sandbox's flags, both session budgets, merge refusals and the page's
local-only server:

```bash
uv run pytest -q
```

Expect: /\d+ passed/

The proof uses the real model and Docker. The laptop must reach the token and no sandbox may. At least
7 of 9 issues must reach green CI, every merge must be green on its commit, and `main` must stay green:

```bash
uv run pytest -m proof -q
```

Expect: 1 passed

## At a startup

Swap Docker for Modal. Write a `ModalSandbox` class with the same four methods as `Sandbox`, using
`modal.Sandbox.create`, `exec`, `open` and `terminate`. Give it the proxy as its only network, and you
need a Modal token. Open sessions from a Slack command, and let the agent push with the requester's
GitHub token, as Ramp does.

## At an enterprise

Put the proxy's allow list and budgets under the platform team, and log every refused request. Require
the CI check and one approval in branch protection, so no person or agent can skip them. Run a merge
queue, so CI also runs on the merged result. Track the merged share per repository each week.

## Change it

- Add the host's `FERNHILL_DEPLOY_TOKEN` to the `env` that `run_issue` gives the sandbox, then run
  `fleet run FH-110 --fresh`. What does `Planted host token seen` say?
- Remove the `CI must pass on the commit being merged` check in `Forge.merge`, then run the tests. Which one
  fails?
- Read the workflow from the branch instead of `main` in `workflow_steps`. Which test catches it?
- Set `PARALLEL_SESSIONS` to 1 and compare the wall time of `fleet run --fresh`.

Sources: [Ramp, why we built our background agent](https://builders.ramp.com/post/why-we-built-our-background-agent),
[Modal Sandboxes](https://modal.com/docs/guide/sandboxes) and its
[`modal.Sandbox` reference](https://modal.com/docs/reference/modal.Sandbox),
[Docker security](https://docs.docker.com/engine/security/),
[`docker network create --internal`](https://docs.docker.com/reference/cli/docker/network/create/), and
[GitHub protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).

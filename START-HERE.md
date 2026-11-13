# Start here

This page tells you where to begin, which labs to do, and how long they take.

## Who this is for

You write code and you are new to shipping AI features. You know a little Python. You have never put
an LLM into a product that real people use.

## What every lab has

Every lab copies a job that a real company's product does today. Each lab has four parts:

1. **The job.** What the product does. For example, Ramp matches each receipt photo to the card
   charge it pays for.
2. **The truth.** Something the model did not write, which we check its answer against. For example,
   the bank's card feed.
3. **The metric.** One number the product team watches. For example, the share of receipts matched
   with no person.
4. **The proof.** A test that runs the lab on a real model and checks the metric.

You read the lab's README from top to bottom. Every command in it shows what it prints, so you can
see when something went wrong.

## What you need

- [uv](https://docs.astral.sh/uv/), which installs Python and each lab's packages for you.
- A key for any OpenAI-compatible endpoint, for example OpenRouter.
- Some labs need more, such as Docker or a big download. The course map in the README lists them.

## Levels

| Level | What it means |
|---|---|
| spine | Only uv and a key. It teaches the pattern every other lab reuses: typed output, a truth, a metric. |
| core | One new idea on top of the spine. |
| production | Docker, a second account, special hardware, or several moving parts. |

## How long a lab takes

Most labs take 35 to 80 minutes, not 15. The minutes in the course map are our estimate. Plan for
them, so you never feel slow.

## Pick a path

Everyone does the **Spine** first. Then pick the one path that fits your job. Every lab sits in
exactly one path, so you never meet the same lab twice.

<!-- paths: generated from course.toml by `make readme` -->

| Path | For | Labs, in order | Minutes | You finish with |
|---|---|---|---|---|
| Spine | everyone, first | [Extract Structured Data with LLMs](labs/01-structured-output), [Test LLM Features with a Golden Dataset](labs/02-golden-set), [Build a PDF RAG Pipeline with Citations](labs/04-rag-pipeline), [Measure RAG Accuracy with Recall@k](labs/05-rag-recall), [Build an AI Agent Loop with Tool Calling](labs/11-agent-loop) | 215 | A receipt matcher, a golden set gate, a cited RAG answer with recall@k, and an agent loop with a budget. |
| Ship an AI feature | engineers adding AI to a product | [Stop RAG Hallucinations with an LLM Judge](labs/06-rag-judge), [Build an AI Workflow with Mastra](labs/08-ai-workflow), [Route Requests with LLM Classification](labs/09-request-routing), [Add Long-Term Memory to AI Agents with Mem0](labs/14-agent-memory), [Gate AI Releases with Evals in CI](labs/29-evals-ci), [Ship a Streaming AI Agent API with an API Gateway](labs/31-agent-api) | 300 | An agent API behind a gateway with evals in CI. |
| Build agents | engineers building agent products | [Design Tools for AI Agents with MCP](labs/12-mcp-tools), [Cut Agent Token Costs with Context Engineering](labs/13-context-engineering), [Build a Deep Research Agent with Subagents](labs/15-research-subagents), [Build Durable AI Workflows with Temporal](labs/10-durable-workflow), [Run Background Coding Agents with Cloud Sandboxes](labs/32-background-agents) | 225 | Tools you measured, a cached context, a lead agent with subagents, a durable job, sandboxed background agents. |
| Make AI safe | security and trust engineers | [Ship a Remote MCP Server with OAuth](labs/20-remote-mcp-oauth), [Authorize AI Agent Actions with RBAC and ABAC](labs/22-agent-actions-authz), [Enforce Document Permissions in RAG with OpenFGA](labs/21-rag-permissions), [Block Prompt Injection with Guardrails](labs/23-guardrails), [Redact PII and PHI with Presidio](labs/24-pii-phi), [Control AI Agent Tool Access with an MCP Gateway](labs/25-mcp-gateway), [Govern AI Systems with an Inventory and Audit Log](labs/30-ai-governance) | 360 | Every zero-count metric in the course (scope, leaks, destructive actions, unapproved servers) and an audit answer. |
| Run AI in production | platform engineers | [Route LLM Calls with an LLM Gateway](labs/26-llm-gateway), [Trace AI Agents with OpenTelemetry](labs/28-tracing), [Track Prompt Versions with Langfuse](labs/27-prompt-registry), [Run AI Agents on Serverless with Durable Functions](labs/33-serverless-agents) | 230 | A model gateway, traces with cost per customer, a prompt registry with rollback, a crash-proof serverless job. |
| Own the model | data and ML engineers | [Process a Million Prompts with Batch APIs](labs/03-batch-prompts), [Build Agentic RAG with LangGraph](labs/07-agentic-rag), [Score Risk with a Decision Model](labs/19-decision-ai), [Fine-Tune a Small LLM with Reviewed Data](labs/34-fine-tune), [Serve Open LLMs with Serverless vLLM](labs/35-serverless-gpu) | 260 | Batch cost per thousand, agentic retrieval, a calibrated risk model, a tuned small model and its serving bill. |
| Voice and vision | multimodal product engineers | [Read Images with Vision LLMs](labs/18-vision-damage), [Build a Voice AI Agent with Pipecat](labs/16-voice-pipecat), [Build a Phone AI Agent with LiveKit](labs/17-voice-phone) | 155 | A vision claim reader and two phone agents. |

<!-- end paths -->

## How you know you are done

The last step of every lab is called "Prove it". It has two commands:

```bash
uv run pytest -q
uv run pytest -m proof -q
```

The first runs the tests with a fake model and needs no key. The second runs the real model and checks
the metric. When both pass, you are done with that lab. Then post your build in Discussions, as the
README says.

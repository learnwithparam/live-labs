# Live Labs

Small AI projects rebuilt from real production systems, each one measured and runnable in about 15
minutes. The labs belong to the **Applied AI Engineering for Software Engineers** course, and this
repo's Discussions hold the show-and-tell and Q&A for every lab.

## Run a lab

Each lab folder stands on its own. Its README is the walkthrough: every command, and what it prints.
You need [uv](https://docs.astral.sh/uv/) and a key for any OpenAI-compatible endpoint.

`make check` tests the whole repo with no key. `make proof LAB=01-structured-output` copies a lab to a
scratch folder, replays its README against a real model and stops at the first step that does not
print what the README promises.

## The labs

<!-- course map: generated from course.toml by `make readme` -->

| # | Lab | Built on | Status |
|---|---|---|---|
| 1 | [Extract Structured Data with LLMs](labs/01-structured-output) | Ramp matches uploaded receipts to card transactions | ready |
| 2 | [Build a Golden Set for an LLM Feature](labs/02-golden-set) | LinkedIn: the last 15% took four more months | ready |
| 3 | [Process a Million Prompts with Batch APIs](labs/03-batch-prompts) | Instacart Maple batch LLM platform | ready |
| 4 | [Build a RAG Pipeline over PDFs](labs/04-rag-pipeline) | Uber Genie on-call copilot | ready |
| 5 | [Measure RAG Accuracy with Recall@k](labs/05-rag-recall) | Uber's golden set over policy PDFs | ready |
| 6 | [Stop RAG Hallucinations with an LLM Judge](labs/06-rag-judge) | DoorDash Dasher support guardrail and judge | ready |
| 7 | [Build Agentic RAG with LangGraph](labs/07-agentic-rag) | Uber enhanced agentic RAG | ready |
| 8 | [Build an AI Workflow with Mastra](labs/08-ai-workflow) | Shopify Magic writes product descriptions from merchant details | ready |
| 9 | [Route Support Tickets with a Decision Model](labs/09-request-routing) | Zendesk intelligent triage | ready |
| 10 | [Make an AI Workflow Survive a Crash](labs/10-durable-workflow) | Temporal case studies: Coinbase and Netflix | ready |
| 11 | [Build an AI Agent Loop from Scratch](labs/11-agent-loop) | Klarna's assistant handled two thirds of support chats | ready |
| 12 | [Design Tools for AI Agents with MCP](labs/12-mcp-tools) | Shopify Sidekick tool design | ready |
| 13 | Cut Agent Token Costs with Context Engineering | Manus KV-cache hit rate | planned |
| 14 | Give AI Agents Long-Term Memory with Mem0 | ChatGPT memory and its controls | planned |
| 15 | Build a Deep Research Agent with Subagents | Anthropic's multi-agent research system | planned |
| 16 | Build a Voice AI Agent with Pipecat | SoundHound takes restaurant phone orders | planned |
| 17 | Put a Voice AI Agent on a Phone Line | LiveKit behind ChatGPT voice | planned |
| 18 | Assess Car Damage Photos with Vision LLMs | Tractable damage appraisal | planned |
| 19 | Score Refund Risk with a Decision Model | Stripe Radar score, thresholds and review | planned |
| 20 | Ship a Remote MCP Server with OAuth | Remote MCP servers from Linear, Sentry and Atlassian | planned |
| 21 | Enforce Document Permissions in RAG with OpenFGA | Glean permission-aware search | planned |
| 22 | Authorize AI Agent Actions with RBAC and ABAC | Block's Goose: read-only and destructive tools | planned |
| 23 | Block Prompt Injection with Guardrails | Zillow Fair Housing Classifier | planned |
| 24 | Redact PII and PHI before the LLM | Clinical scribe products under HIPAA | planned |
| 25 | Control AI Agent Tool Access with an MCP Gateway | Block's curated list of approved MCP servers | planned |
| 26 | Route LLM Calls with an LLM Gateway | RouteLLM and Instacart's AI gateway | planned |
| 27 | Manage Prompts Outside Your Code | Langfuse and PromptLayer prompt registries | planned |
| 28 | Trace AI Agents with OpenTelemetry | OpenTelemetry GenAI conventions | planned |
| 29 | Gate AI Releases with Evals in CI | Shopify Sidekick evals before release | planned |
| 30 | Govern AI Systems with an Inventory and Audit Log | EU AI Act transparency duties from Aug 2026 | planned |
| 31 | Ship a Streaming AI Agent API with an API Gateway | The chat endpoint behind every in-app assistant | planned |
| 32 | Run Background Coding Agents with Cloud Sandboxes | Ramp Inspect, run on Boat sandboxes | planned |
| 33 | Run AI Agents on Serverless with Durable Functions | AWS Lambda durable functions and Bedrock AgentCore | planned |
| 34 | Fine-Tune a Small LLM with Reviewed Data | Checkr and Ramp replaced a large model with a tuned small one | planned |
| 35 | Serve an Open LLM on Serverless GPUs | Ramp on Modal | planned |

<!-- end course map -->

## Post your build

Open [Show and tell](https://github.com/learnwithparam/live-labs/discussions/categories/show-and-tell) and
start the title with the lab's slug in brackets, for example `[mcp-gateway] Gateway in front of our Jira
server`. Each lab page links straight to a prefilled post. Include:

1. What you built, in one sentence.
2. A link to the code, or a screenshot if the code is private.
3. One thing that broke and how you fixed it.
4. One question you want a reviewer to answer.

Leaders: a playbook, metric charter or upskilling plan counts as a build. Remove company names first.

## Get unstuck

Ask in [Q&A](https://github.com/learnwithparam/live-labs/discussions/categories/q-a) with the lab slug in
the title. Mark the reply that solved it as the answer, so the next person finds it.

## Review someone else

Every lab page lists three things you can do afterwards. Use them as the review checklist: say which of
the three the build shows, and one change you would make. Members who review well are invited to be lab
captains.

## Norms

Read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). Short version: critique the code, not the person; no
recruiting or selling; never post secrets or anything your employer has not cleared.

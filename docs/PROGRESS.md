# Progress

## 2026-11-04: model-selected order discovery before cancellation

Added an opt-in customer-scoped fixture order Agent subgraph between classification
and the existing guarded workflow. It selects time search/detail/clarification/
binding tools, persists pauses via the parent SQLite saver, and requires customer
selection for multiple matches. React now shows choices and completed tool traces.
20 new tests, 173 total pass; four scripted API scenarios pass. Real DeepSeek
acceptance was attempted but blocked by HTTP402; not a live-store/model success claim.
See [implementation and boundaries](order-discovery-agent.md).

## 2026-11-04: reviewed external Shop Skill

Added a separate catalog toolset: the model loads reviewed read-only sections of
Shopify's pinned Shop Skill, then invokes the official CLI to search public real
merchant products. Validate price/currency/selected availability and keep exact
URLs. Original source/license retained. No private inventory, order or purchase
access. Real model run selected both Skill loader and catalog tool successfully.

## 2026-11-04: real model-selected external tools

Added a bounded read-only LangGraph tool loop with a policy retrieval tool and
external Frankfurter reference conversion. The real model selected both tools
for a combined query, received ToolMessages and answered from the results.
`--rag agentic` enables this in policy-question handling; the direct CLI also
supports currency-only queries. Order writes stay under the original rules.
See `read-only-tool-agent.md` for tool budget, validation and limits.

## 2026-11-04: opt-in multilingual evidence retrieval

Added a LangGraph rewrite/retrieve/grade subgraph: Chinese search translation,
BM25+dense RRF candidates, structured evidence selection, and exact quote/ID
checks. Empty or failed evidence retrieval escalates before policy answering.
Baseline stays selectable. Additional synthetic labels are frozen before live
comparison. See `advanced-rag.md`; production and final-answer quality are not
established by retrieval scores.

Feature log: what's built, what's next. Details live in
[tech_spec.md](tech_spec.md) and [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

The July entries below are upstream history. This local learning fork starts
from upstream commit `9c1d5644effac7aed10079d23fc2a9f3f2982b1a`, obtained
from the official codeload archive after Git cloning failed. Its local Git
snapshot is not the upstream repository's complete history. Entries dated
2026-10-28 describe the AI-assisted local extension, not an upstream merge or
an independently authored user contribution.

## Roadmap

- [x] 1. Scaffold + Shopify client wrapper + smoke test
- [x] 2. Seed script — 50 live test orders across policy edge cases
- [x] 3. Domain contracts + pure policy gate + live policy-check CLI
- [x] 4. Ticket classifier (structured output, task list) + CLI
- [x] 5. LangGraph pipeline: approval interrupt, SQLite checkpoints, ticket CLI
- [x] 5b. Real Shopify writes: cancel + full refund behind approval
- [x] 6. Policy corpus + policy-question answering with verified citations
- [x] Hardening: strict order-reference binding
- [x] 6b. RAG behind `find_policy_context` (local Chroma, chunk by heading)
- [x] 5c. Address extraction in the classifier + `orderUpdate` write
- [x] 7a. Polish: LangSmith trace walkthrough (docs + README teaser image)
- [x] 8.0. Ticket registry + completed-id reuse guard
- [x] 8.1. FastAPI wrapper: create/list/detail/decision endpoints
- [x] 8.2. React console: ticket list, create form, and result detail
- [x] 8.3. Approval inbox: safety details, approve/reject, and final results
- [x] 8. Operator console: FastAPI API wrapping the graph and a Vite + React
      single-page UI with full CLI feature parity and single-process serving
- [x] 7b. Polish: README hero screenshot of the operator console
      (console GIF deferred to a future demo recording)
- [x] Local extension: single cancellation missing an order number pauses for
      a structured answer, then continues through lookup, gate, and approval.
- [x] Local learning mode: deterministic offline console demo with separate
      databases and simulated Shopify effects.
- [x] v2. Multi-request tickets: the classifier's ordered task list is the plan;
      code validates it and fans tasks out via `Send` to the
      existing task subgraph (actions) or a research path (questions), and a
      composer merges the task results into one reply. Approval stays a graph
      stage inside the action path — never its own agent. It reuses the task
      subgraph and the classifier's task list, with
      `draft_reply` composing from structured results. The API, CLI, and console
      address each pending interrupt by id.
- [ ] Backlog: seed-script argparse, scope trim, retry story for tickets
      whose graph run failed after registration

## Log

### Local — Structured order-number clarification (2026-10-28)

Added `clarify_missing_order_reference` before plan validation and fan-out.
The same ticket waits for one order number, validates it, records a copied-task
update and clarification history, then uses the existing action subgraph.
Customer answers and operator approvals have different payloads, statuses,
API endpoints, CLI flags, and console cards; neither can resume the other.

The app serializes graph operations within one API process. Run one worker
and stop the API before using the CLI on the same checkpoint database.
The original classifier, policy gate, fan-out, RAG, approval mechanism, and
refund idempotency remain upstream features.

### Local — Offline learning and validation (2026-10-28)

Added an explicit `storekeeper.demo` launcher with a deterministic parser,
fixed replies, and fixtures `#1036` / `#1037`. It serves the built console,
stores state under `var/offline_demo/`, and makes no model or Shopify calls.
Its results demonstrate graph behavior, not LLM quality or live-store effects.

Before the local changes, all 82 upstream offline tests and the frontend
build/lint passed. Local checks cover invalid and wrong-kind resumes, task
binding, approval-before-write, duplicate submissions, and SQLite restarts.
No local model API call, Shopify write, customer deployment, or new LangSmith
trace is claimed. See [testing.md](testing.md) for reproducible checks.

### 1 — Shopify foundation (2026-07-12)

Env config and a single `ShopifyClient` (client-credentials auth, token
caching, one retry on 401). Smoke test passed against the live dev store.

### 2 — Seed data (2026-07-12)

Resumable seed script created 50 orders across happy / fulfilled / old /
high-value segments; reruns create only what's missing.

### 3 — Policy gate + live check (2026-07-12)

Shared domain contracts and a pure-Python gate: cancel and address changes
need an unfulfilled order, refunds need ≤ 30 days, over $100 gets flagged.
Live order lookup + `check_order_policy.py` CLI verified against real orders.

### 4 — Ticket classifier (2026-07-12)

LLM classification into a validated task list (Pydantic on `ChatOpenRouter`,
model slug from env). The model picks only the intent; the action is derived
in code. Proven live via `classify_ticket.py`.

### 5 — Guarded graph (2026-07-13)

LangGraph ticket graph wrapping a per-task subgraph; eligible writes pause at
a human-approval interrupt that survives process restarts (SQLite, thread_id
= ticket id). `run_ticket.py` runs, `--approve` / `--reject` resume. Replies
are LLM-drafted from structured results.

### 5b — Real writes (2026-07-13)

`orderCancel` and `suggestedRefund`-driven `refundCreate` (order-derived
idempotency key) run after approval; a rejected approval sends zero write
calls. Verified live: real cancellation and a real 42 USD refund.
Address changes still escalated here; slice 5c later automated them.

### 6 — Policy answers (2026-07-13)

`policies/` corpus (numbers locked as real policy: 30 days / $100) behind the
`find_policy_context()` seam. Policy questions get answered with citations
verified in code; denied requests cite the relevant policy in their reply.

### 6b — Policy RAG (2026-07-13)

Policy questions retrieve the top three heading chunks from a local Chroma
index using local MiniLM embeddings. Index and search CLIs prove retrieval
before graph use; action-denial citations still read deterministic whole docs.

### 5c — Shipping-address changes (2026-07-13)

The classifier extracts the requested address without inventing missing fields;
incomplete requests escalate. Eligible changes show current and final addresses
for human approval, then execute Shopify `orderUpdate`.

### 7a — LangSmith walkthrough (2026-07-13)

`docs/langsmith.md` reads three real traces: a cancel run whose tree ends at
the approval interrupt, the post-approval resume where the write node first
appears, and a policy answer with its verified citation. The README links it
with a teaser image; the console GIF waits for slice 8.

### 8.0 — Ticket registry (2026-07-13)

A separate SQLite registry now indexes ticket ids for CLI/API listing while
LangGraph checkpoints remain the source of truth for status. New CLI tickets
refuse reused registry ids or existing graph threads; approval resumes are unchanged.

### 8.1 — FastAPI wrapper (2026-07-13)

The localhost API now creates, lists, reads, and decides tickets through the
existing graph. Pydantic models expose validated state and the approval payload,
including the gate reason; stubbed HTTP tests make no Shopify or model calls.

### 8.2 — Read-side operator console (2026-07-14)

A Vite + React + TypeScript SPA now creates tickets, browses registry history,
and renders outcomes, reply drafts, and verified citations through the FastAPI
API. Plain fetch and CSS reproduce the operator-console mockup without UI or
state libraries; approval controls remain for 8.3.

### 8.3 — Approval inbox (2026-07-14)

Pending actions now show requested versus resolved order, amount, gate reason,
flags, and address comparisons before an explicit decision. Approve and Reject
resume the existing graph through the API and render the final draft immediately;
conflict and missing-ticket errors stay readable in the card.

### 8.4 — Single-process serving (2026-07-14)

FastAPI now serves the built React console at `/` after registering `/api/`,
so one local process runs the complete operator workflow. API-only startup
still works when `frontend/dist` is absent; HTTP tests cover both paths.

### Hardening — order binding (2026-07-13)

Order references are allowlisted (`#`+digits), the returned order must match
the reference exactly, and the approval screen shows requested vs. found.
A hostile reference fails softly with zero Shopify calls.

### v2 - Parallel multi-task orchestration (2026-07-15)

The classifier's task list now fans out through deterministic `Send` routing.
Independent writes pause together with separate interrupt ids; the API, CLI,
and approval inbox resume one card at a time. Results return to customer order,
and one composer drafts the final or holding reply. Same-order write plans are
rejected before lookup so parallel work cannot race on one order. Live-verified
end to end, including an approved parallel write executing on the dev store.

### 7b - README showcase (2026-07-15)

The README now opens in plain language with a hero screenshot: one customer
message producing two pending approval cards, each showing gate reason and
exact-match order binding. The diagram gained the escalation path, limitations
name manual ticket entry and draft-only replies, and quickstart commands now
run on any platform. The console GIF waits for a future demo recording.
### Local DeepSeek preview and initial evaluation (2026-10-29)

Added a real DeepSeek model adapter and a separate fixture-order UI on port 8766.
A fixed 24-case classification development set and four API flows ran against
the real model; raw reports include outputs, timing and token usage. Local
Shopify effects remain simulated, and production/draft factual quality is not
established by these small-set results.
### Submitted cancellation receipts and frozen evaluation (2026-10-30)

Cancellation jobs now produce a submitted receipt with unverified settlement,
and the UI and deterministic reply no longer claim completion. Added a fixed
40-case synthetic test set separate from development data and a token/latency
runner. Real model results were 40/40 strict matches and 4/4 fixture API flows;
124 local tests passed. Real Shopify completion remains unverified.

### Frozen policy retrieval baseline (2026-10-30)

Added labeled retrieval probes and repeatable ID precision/recall scoring for
the original Chroma top3 seam. English 40: recall 100%, precision 36.67%;
separate Chinese 10: recall 60%, precision 20%. Six no-answer questions still
return contexts. The baseline exposes language/irrelevant-context limits;
no retrieval optimization or generated-answer accuracy is claimed.

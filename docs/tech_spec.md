# As-built technical specification

## Optional order discovery Agent

`build_ticket_graph(order_agent_model=..., order_directory=...)` inserts the
`resolve_order_agent` node only when explicitly configured with a simulated
action client. `order_resolution.py` builds an inherited-checkpointer subgraph
with model/tool turns, verified candidates and a customer-choice interrupt.
The model can select four read/selection tools; it never receives cancel/refund
tools. Success updates a copied task's reference and proceeds through the
existing gate/approval path. Failure escalates with deterministic wording.
API clarification payloads add optional `choices` and `resolver`; detail responses
add `order_resolution`. Legacy interrupts still work. Selection is validated in
the API and graph. `FixtureOrderDirectory` is a test customer binding, not login
or production RBAC. See [order-discovery-agent.md](order-discovery-agent.md).

## Read-only model-selected tools

`tool_agent.py` exposes policy retrieval and fixed-host rate conversion via
`bind_tools`. A bounded LangGraph loop executes allowlisted requests and returns
ToolMessages. `POLICY_RETRIEVAL_MODE=agentic` uses it inside policy-question
handling. Currency calculations use Decimal; errors cannot create order writes.
See `read-only-tool-agent.md`. Tool requests are real function execution, unlike
the upstream classifier's schema-only function-calling interface.

## Optional enhanced policy retrieval (2026-11-04)

`POLICY_RETRIEVAL_MODE=enhanced` routes policy questions through
`advanced_retrieval.retrieve_policy_chunks`, a bounded LangGraph subgraph with
query translation, BM25+dense RRF and LLM evidence grading. IDs and verbatim
quotes are checked in Python. Trace data lives in task `action_result` and
includes retrieval ranks, selected evidence, model usage and timings. Empty
evidence/errors escalate; action gates keep their deterministic whole-doc path.
The baseline remains the default. See `advanced-rag.md` for configuration,
provenance, limits and evaluation commands.

Local extension dated 2026-10-28, based on upstream snapshot
`9c1d5644effac7aed10079d23fc2a9f3f2982b1a`. Local validation uses injected
models and Shopify clients; upstream live-store history is not a local result.

## Domain contracts

`src/storekeeper/domain.py` owns shared `Task`, `ShippingAddress`, `OrderFacts`,
`GateVerdict`, and `TaskResult` types. `OrderFacts` is a small normalized view
of a Shopify order. `Task.order_reference` holds the order name as the customer
wrote it (e.g. `#1036`), not the Shopify GraphQL id. Address-change tasks carry
the customer-provided address fields in `Task.new_shipping_address`.

## Ticket classifier

`classify.classify_ticket(ticket_text)` turns a customer message into domain
`Task`s using LangChain structured output on `ChatOpenRouter`. The model slug
comes from the `OPENROUTER_MODEL` env variable.

- A Pydantic schema (`TicketClassification`) validates the LLM output before
  any other code sees it; intent values reuse the `Intent` literal from
  `domain.py`, so allowed values have one source of truth.
- The model picks only the intent. `requested_action` is derived in code from
  a fixed intent→action mapping, so the model cannot emit an inconsistent
  intent/action pair.
- For address changes, the model extracts structured address fields but must
  leave omitted fields null. Automation requires street, city, state or
  province, postal code, and country; incomplete requests escalate instead of
  guessing.
- Output is an ordered task list. Code assigns `task-1`, `task-2`, and so on
  after classification; these ids correlate fan-out results and approvals
  without letting the model choose execution identity. In v2 this flat list is
  the plan.
- Tests inject a fake model; the unit suite makes no network calls.

## Structured order-number clarification

`graph/clarification.py` adds `clarify_missing_order_reference` between
`classify` and `validate_task_plan`. It pauses only when there is exactly one
task whose intent and action are `cancel_order` and whose order reference is
null. Other requests retain their previous handling.

The interrupt payload is `{kind: "clarification", task_id,
field: "order_reference", question}`. LangGraph supplies its interrupt id.
Resume uses `Command(resume={interrupt_id: {"order_reference": "#1036"}})`.
The shared normalizer accepts one number of 1–10 ASCII digits, strips outer
whitespace, accepts an optional `#`, and returns the `#`-prefixed reference.
The API validates before invoking the graph; the graph validates again and
rejects resume dictionaries with missing or extra fields.

The node copies the original task and changes only `order_reference`.
Classification is not repeated, task identity and action stay fixed, and the
original `ticket_text` is retained. `clarification_history` stores
`{task_id, field, value}` records in a normal replacement state channel. The
node returns no `task_results`, whose append reducer is reserved for completed
workers, so replay cannot append a duplicate result from this node.

After the answer, the original lookup, policy gate, and approval subgraph still
run. Customer information never authorizes a write. This extension does not
implement general conversation memory, intent changes, address collection, or
multi-task clarification.

## Policy corpus and answering

- `policies/` (repo root) holds short markdown store-policy documents. Their
  numbers must match the gate's constants (30-day refunds, $100 high-value
  threshold) — the gate decides, the docs explain.
- `policy_docs.find_policy_context(task, ticket_text)` is the retrieval seam.
  Action intents still map deterministically to their whole policy document.
  Policy questions with ticket text query the top three heading chunks from a
  persistent Chroma collection in `var/policy_index/`; calling without ticket
  text keeps the whole-corpus fallback for compatibility. The collection uses
  Chroma's local ONNX `all-MiniLM-L6-v2` embedding function and cosine distance,
  so indexing and retrieval need no API key. Policy text feeds the answering
  and drafting LLMs only — never the gate.
- `scripts/index_policies.py` rebuilds the collection from `policies/*.md`.
  Each `##` section becomes one chunk containing the document title, heading,
  and body, with source filename and heading metadata. Run it after changing a
  policy document. `scripts/search_policy.py` prints the nearest three chunks
  and their cosine distances so retrieval quality can be checked before graph
  use.
- The read-only policy worker answers with structured output
  (`PolicyAnswer`); citations are kept only if they name documents that were
  actually provided. The result is a `TaskResult` with outcome `"answered"`
  and `policy_citations`; `draft_reply` remains the single reply author.
- Denied actions carry the relevant document names in `policy_citations`, and
  the drafter receives the policy text so replies cite store policy.

## Ticket registry

`tickets.py` maintains a small SQLite registry at `var/tickets.sqlite` with a
ticket id, its original text, and creation time. The registry is only the
listing index for the CLI and API; it does not duplicate workflow state.
`get_ticket_status()` reads the ticket's LangGraph checkpoint and derives
`waiting_for_customer`, `pending_approval`, `resolved`, or `not_found` from the saved values and
interrupts. Starting a new CLI ticket refuses an id already present in either
the registry or the checkpoint database, while clarification and approval
resumes keep using the existing id. A clarification payload means
`waiting_for_customer`; legacy interrupts without a `kind` remain approvals.

## Operator API

`api/app.py` owns a FastAPI application factory and five synchronous routes:
`POST /api/tickets`, `GET /api/tickets`, `GET /api/tickets/{ticket_id}`, and
`POST /api/tickets/{ticket_id}/decision`, plus
`POST /api/tickets/{ticket_id}/clarification`. The application lifecycle opens the
shared SQLite checkpointer and compiles the existing graph once. Tests inject a
stub graph through the same factory.

`api/schemas.py` defines the public Pydantic request and response contracts.
Responses mirror ticket state, ordered task results, separate
`pending_approvals` and `pending_clarifications` lists, and
`clarification_history`, rather than returning raw checkpoint objects.
The clarification request accepts only `interrupt_id` and `order_reference`;
extra fields and invalid references return 422 before graph resume.
Each resume endpoint checks both the current status and the pending interrupt
id, so customer information cannot resume an approval or vice versa.
Duplicate ids and decisions on tickets
that are not pending return 409; unknown registry ids return 404. Endpoints use
plain `def` because graph, model, Shopify, and SQLite operations are synchronous.
The API never calls Shopify, the classifier, or the drafter directly.
The response builder assigns deterministic fallback ids to v1 tasks, results,
and approval payloads, so checkpoints created before `task_id` remain readable.

Every API graph read or mutation holds the app's shared `RLock`, including
the pending-state check and resume. Run one API worker: this lock is local to
one application process. Stop the API before using the CLI against the same
checkpoint database; a separate CLI process does not share the lock.

## Operator console

`frontend/` is a Vite + React + TypeScript single-page application. In
development, Vite runs on `127.0.0.1:5173` and proxies `/api` to FastAPI on
`127.0.0.1:8000`, so no CORS configuration is needed. It uses plain `fetch`
and mirrors the Pydantic response fields with TypeScript interfaces.

For a built run, Vite writes static assets to `frontend/dist`. The FastAPI app
mounts that directory at `/` after registering `/api/`, so one process serves
both the console and API without changing the frontend's relative requests.
The mount is skipped when `dist` is absent, preserving API-only development.

`App.tsx` owns ticket selection, list refresh, and new-ticket mode.
`TicketList`, `TicketForm`, and `TicketDetail` render the two-column console:
newest-first history on the left and the selected customer message, outcome,
reply draft, and verified citations on the right. Detailed outcome badges are
derived from the selected ticket's `task_results`; unselected list rows use
the checkpoint-derived summary status.

Pending tickets render every API approval payload without changing its field
names: customer-written reference, resolved Shopify order, action, amount, gate
rule and reason, flags, and current versus proposed shipping addresses. Approve
and Reject call `POST /api/tickets/{ticket_id}/decision` with that card's
`interrupt_id`, disable that card while the synchronous graph resume runs, and
keep other pending cards visible. The approve button names the exact Shopify consequence.
Concurrent-decision 409s and unknown-ticket 404s remain visible with a refresh
action instead of clearing the selected ticket.

`ClarificationCard.tsx` displays the requested order number separately from
approval controls. It submits the answer to `/clarification`, then refreshes
the same ticket. Ticket details retain the original customer text and show
the recorded clarification values. A waiting-for-customer badge differs from
the pending-approval badge.

The console is localhost-only and has no authentication. It is not a public
deployment surface.

## Ticket graph

`graph/build.py` assembles two LangGraph `StateGraph`s:

- **Ticket graph** (`TicketState`): `classify` →
  `clarify_missing_order_reference` → `validate_task_plan` → a
  `Send` fan-out to one `process_task` worker per task → `draft_reply`.
  Unsupported or incomplete individual tasks become structured escalation
  results. Conflicting writes for the same order escalate before fan-out.
- **Task pipeline subgraph** (`TaskState`): `lookup_order` → `policy_gate` →
  route → `await_approval` (denied requests skip straight to a result). The
  subgraph is invoked by each action worker. Policy questions stay on a bounded
  read-only branch and never enter the write subgraph.

Rules of the graph:

- All routing is plain Python route functions reading state; the LLM never
  chooses an edge. There is no path to `execute_action` that bypasses
  `policy_gate` and `await_approval`.
- `await_approval` calls `interrupt()` with a JSON-safe payload and routes the
  decision with `Command(goto=...)`. Parallel workers can expose several
  interrupts at once; callers resume one with
  `Command(resume={interrupt_id: "approve" | "reject"})`.
- Checkpoints persist in `var/checkpoints.sqlite` (`SqliteSaver`);
  `thread_id` is the ticket id, so a pending clarification or approval
  survives process restarts. The clarification history remains available
  after a new graph instance resumes the same thread.
- The CLI answers a clarification with
  `--clarify INTERRUPT_ID --order-reference '#1036'`. These flags are separate
  from `--approve` / `--reject`; the CLI rejects a mismatched interrupt kind.
- `execute_action` runs real writes from `shopify/writes.py`: `cancel_order`
  (async `orderCancel`; refunds the original payment and restocks) and
  `issue_full_refund` (`order.suggestedRefund` decides which payment
  transactions to reverse; orders without captured payments get their items
  marked refunded), plus `update_shipping_address` (`orderUpdate`). Address
  updates preserve the current recipient name, company, and phone when the
  customer omits them, while replacing the delivery-location fields. The
  approval payload shows both current and final addresses. `refundCreate`
  carries an order-derived `@idempotent` key, so a re-executed node cannot
  refund twice.
  Note: `orderCancel` returning a job id means Shopify *accepted* the
  cancellation; completion happens asynchronously on Shopify's side.

## Offline learning demo

`python -m storekeeper.demo --port 8765` serves the built console and the
same API/graph with a deterministic parser, fixed replies, and fake Shopify
responses. Fixture `#1036` is unfulfilled and `#1037` is fulfilled. State and
registry databases live under `var/offline_demo/`, apart from the normal
workflow. Build `frontend/dist` before opening the demo in a browser.

This mode needs no model or Shopify credentials and makes no model or Shopify
calls. It demonstrates state transitions and simulated effects, not language
understanding accuracy, real cancellations, or production behavior. The
normal application and CLI retain their real clients.

## What leaves the machine

- **OpenRouter** receives ticket text (classification, drafting), policy
  document text and questions (answering), and structured task results
  including order names and amounts (drafting).
- **LangSmith** (when `LANGSMITH_TRACING=true`) receives full run traces:
  prompts, ticket text, order data, and drafted replies.
- **`var/checkpoints.sqlite`** retains every ticket's state — text, order
  facts, clarification history, decisions, replies — locally and indefinitely.
  Keep it private.
- **Shopify** receives only order lookups and approved write mutations; ticket
  text itself is never sent to Shopify.
- An explicit order reference that cannot be found becomes outcome `failed`,
  keeps the ticket escalated, and receives an apologetic holding reply draft.
  A missing reference on a single cancellation instead waits for clarification.
- `draft_reply` is the composer: one LLM call over the complete,
  customer-ordered `task_results` list after all branches finish.

## Policy gate

`shopify.operations.lookup_order()` reads an order by name and converts Shopify's
response into `OrderFacts`. Any status except `UNFULFILLED` is treated as fulfilled.

Order binding is strict, because the reference comes from ticket text:

- A reference must normalize to `#` + digits (allowlist — anything else raises
  `InvalidOrderReferenceError` before any Shopify call).
- The returned order's name must equal the normalized reference exactly, or
  the order is treated as not found. An action can never bind to an order the
  customer did not name.
- The approval payload carries the customer-written reference alongside the
  resolved order name, so the human approves the binding, not just the result.

`policy_gate(requested_action, order_facts, evaluated_at)` is deterministic and
has no Shopify, LLM, or graph dependency.

- Cancellation and address changes require an unfulfilled order.
- Refunds require `processedAt` to be 30 days old or less.
- Refunds over 100 store-currency units receive a `high_value` flag.
- Passing a rule means eligible for later human approval, not execution.
# Local cancellation submission contract (2026-10-30)

`TaskOutcome` includes `submitted`. A cancellation job id is a receipt, not
evidence of completed cancellation or settled payment. The adapter exposes
`operation_status=submitted`, `job_reported_done`, `refund_status=unverified` and
the job id. A missing job receipt raises an explicit write error. No mutation
request parameters changed; no polling or automatic retry is added.

For a ticket containing a submitted task, `submission_receipts.py` builds the
reply directly from structured results in customer task order. The LLM is not
asked to paraphrase that receipt. Other intents retain their existing drafting
path. Fixture receipts carry a test-data notice. The UI uses an attention label
for submissions. Ticket `resolved` still means the local handling run ended;
it does not mean an asynchronous external effect completed. Existing saved
checkpoints are not migrated; use new tickets to exercise the new contract.

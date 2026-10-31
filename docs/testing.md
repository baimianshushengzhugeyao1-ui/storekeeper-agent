# Testing

The 2026-10-28 local extension is checked without model or Shopify calls.
Upstream live-check instructions are retained below; they are not a record of
live validation performed for this fork.

## Offline regression checks

```powershell
uv run python -m unittest discover -s tests -v
uv run python -m compileall -q src scripts tests
uv run python -m unittest discover -s tests -p "test_clarification*.py" -v
cd frontend
npm run build
npm run lint
cd ..
```

The graph tests inject fake models and a fake Shopify client. The HTTP tests
exercise validation and resume behavior; SQLite tests close the checkpointer,
rebuild the graph, and resume the saved clarification and approval separately.
They check that:

- A single cancellation missing its order number waits for customer input
  before order lookup, drafting, or writing.
- A valid answer updates only the original task's order reference, preserves
  the original text, and does not classify again or duplicate task results.
- Wrong kinds, stale ids, extra fields, multiple order numbers, query text,
  and non-ASCII digits cannot authorize a write.
- A supplied reference still goes through lookup and the policy gate;
  an eligible action needs a separate human approval.
- Restarting retains the question, answer history, and pending approval.

In the restricted Windows agent runner, TestClient's local loopback setup and
temporary SQLite files required a permitted execution process. See
[TROUBLESHOOTING.md](TROUBLESHOOTING.md) if checks stall before assertions or
cannot open temporary databases. This is separate from external API access.

## Offline console walkthrough

Build the frontend first, then run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m storekeeper.demo --port 8765
```

Open `http://127.0.0.1:8765`. This uses a deterministic parser, fixed replies,
and fake Shopify responses. Its state lives under `var/offline_demo/`, not
the normal checkpoint files. These cases test the product flow rather than
LLM performance:

1. Create `Please cancel my order.`. Confirm the same ticket shows
   `waiting_for_customer` and an order-number card, with no approval card.
2. Stop and restart the demo before answering. The ticket must still wait
   for its order number. Invalid input must not advance the workflow.
3. Supply `#1036`. Confirm the original message and answer history remain
   visible, and the ticket now waits for a separate approval. Approve to see
   a simulated cancellation, or reject to see a human-rejection outcome.
4. Create another missing-order cancellation and supply `#1037`. Its
   fulfilled fixture must be denied by policy without an approval card.

For a local HTTP-only check, keep the demo running and use a new ticket id:

```powershell
$ticketId = "TICKET-OFFLINE-CLARIFY-1"
$ticketBody = @{ticket_id = $ticketId; ticket_text = "Please cancel my order."} | ConvertTo-Json -Compress
$ticketBody | curl.exe -s -X POST localhost:8765/api/tickets -H "Content-Type: application/json" --data-binary '@-'
$detail = curl.exe -s "localhost:8765/api/tickets/$ticketId" | ConvertFrom-Json
$clarificationId = $detail.pending_clarifications[0].interrupt_id
$answer = @{interrupt_id = $clarificationId; order_reference = "#1036"} | ConvertTo-Json -Compress
$answer | curl.exe -s -X POST "localhost:8765/api/tickets/$ticketId/clarification" -H "Content-Type: application/json" --data-binary '@-'
```

The response must contain one history record, no task result yet, and one
pending approval. Repeating the clarification returns 409. Invalid references
or extra request fields return 422 before graph resume; neither endpoint may
consume the other kind of interrupt. Use the console or `/decision` for the
subsequent approval or rejection.

## Normal Shopify workflow checks

These manual checks use configured development-store/model credentials.
Run one API worker, and stop it before running the CLI against the same
checkpoint database. The API's process-local lock cannot serialize a separate
CLI process.

```powershell
uv run python scripts/smoke_shopify.py
uv run python scripts/seed_store.py --plan
uv run python scripts/check_order_policy.py '#1001' cancel_order
uv run python scripts/classify_ticket.py "Please cancel order #1036. I ordered it by mistake."
uv run python scripts/classify_ticket.py "Change order #1036 to 20 Lake Road, Dhaka, Dhaka 1205, Bangladesh."
uv run python scripts/index_policies.py
uv run python scripts/search_policy.py "How long is your warranty?"
uv run python scripts/run_ticket.py TICKET-1 "Please cancel order #1001."
uv run python scripts/run_ticket.py TICKET-1 --approve INTERRUPT_ID
uv run python scripts/run_ticket.py TICKET-2 "How long is your warranty?"
uv run python scripts/run_ticket.py TICKET-3 "Change order #1036 to 20 Lake Road, Dhaka, Dhaka 1205, Bangladesh."
uv run python scripts/run_ticket.py TICKET-4 "Please cancel my order."
uv run python scripts/run_ticket.py TICKET-4 --clarify CLARIFICATION_ID --order-reference '#1036'
uv run uvicorn storekeeper.api.app:app --host 127.0.0.1 --port 8000
```

`classify_ticket.py` and the normal `run_ticket.py` make paid OpenRouter
model calls, and **approving a `run_ticket.py` action executes a real write on
the store** (cancel, refund, or address update). Running `--approve` in
a separate invocation from the ticket run verifies that the approval interrupt
survives a process restart (state lives in `var/checkpoints.sqlite`).
For the missing-order case, copy the clarification id from the first run;
after `--clarify`, use the new approval id for a separate decision. Supplying
customer information alone never approves a store action.

`index_policies.py` and `search_policy.py` are local and need no API key. The
first run downloads Chroma's MiniLM ONNX model once. Rebuild the index after
editing any file in `policies/`; cosine distances printed by the search CLI are
lower for more similar chunks.

With the API running, use `curl.exe` in PowerShell to verify the HTTP wrapper:

```powershell
$ticketId = "TICKET-API-CURL-1"
$ticketBody = @{ticket_id = $ticketId; ticket_text = "Please cancel order #1003."} | ConvertTo-Json -Compress
$ticketBody | curl.exe -s -X POST localhost:8000/api/tickets -H "Content-Type: application/json" --data-binary '@-'
curl.exe -s localhost:8000/api/tickets
curl.exe -s "localhost:8000/api/tickets/$ticketId"
$interruptId = (curl.exe -s "localhost:8000/api/tickets/$ticketId" | ConvertFrom-Json).pending_approvals[0].interrupt_id
$decisionBody = @{interrupt_id = $interruptId; decision = "reject"} | ConvertTo-Json -Compress
$decisionBody | curl.exe -s -X POST "localhost:8000/api/tickets/$ticketId/decision" -H "Content-Type: application/json" --data-binary '@-'
```

Creating and resolving tickets makes paid OpenRouter calls. An `approve`
decision can execute a real Shopify write; use `reject` when verifying the API
without changing the development store.

For the browser flow, keep FastAPI running on port 8000 and Vite on port 5173.
In a second PowerShell terminal, run:

```powershell
cd frontend
npm run build
npm run lint
npm run dev
```

Open `http://127.0.0.1:5173`, create the policy question `How long is your
warranty?`, and verify that the new ticket appears first in the list with an
Answered badge. Its detail view must show a reply draft and the verified
`warranty.md` citation. The Copy draft button must copy the displayed reply.
This live flow makes paid OpenRouter calls but performs no Shopify write.

To verify the approval inbox, create a cancellation for a known unfulfilled
development-store order. Confirm the card shows the customer reference beside
the resolved order, amount, gate rule and reason, and any flags. Reject one
eligible ticket and verify the order stays unchanged. Approve another only when
a real development-store write is intended; verify Shopify records the write
and the final draft appears immediately. A refund for an order older than 30
days must resolve as Denied by policy with a citation and never enter the inbox.

For an address-change ticket, confirm the card shows the current and proposed
addresses side by side. Rejecting it must preserve the original Shopify address.

For v2, create one ticket with two independent eligible writes against two
different unfulfilled orders. Both approval cards must appear together. Reject
one card and confirm the other remains pending; reject the second and confirm
one final draft covers both results in the customer's original request order.

To verify single-process serving, stop Vite after the frontend checks, build the
console, and run only FastAPI:

```powershell
cd frontend
npm run build
cd ..
uv run uvicorn storekeeper.api.app:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` and repeat the create, detail, and safe reject
flow. Confirm `curl.exe -s -I http://127.0.0.1:8000/` returns an HTML response
and `curl.exe -s http://127.0.0.1:8000/api/tickets` still returns JSON. Removing
or renaming `frontend/dist` is not required for normal verification; the unit
suite covers API-only startup when the build is absent.
# Real DeepSeek with fixture orders (2026-10-29)

`python -m storekeeper.live_preview --port 8766` serves the existing UI with real
model classification/drafting and a fixed Shopify client. It never opens a
Shopify connection. State is isolated under `var/live_preview`; the original
offline demo remains on 8765. Model calls are billable and external tracing is
disabled for this preview/evaluation.

`python scripts/evaluate_live_model.py --workflows` runs 24 pre-labeled Chinese
and English classification cases, then four API flows with fixture orders.
Exact matching compares intent, reference and task order, not complete address
extraction or reply factuality. The baseline is the narrow offline keyword
parser, not the upstream real LLM. Workflows check pause/resume, approval or
denial and simulated write count. Reports preserve settings, prompt digest,
per-case predictions, elapsed time and model token usage. API failures are
recorded as failures, with no mock fallback or automatic paid retry.

First run: 24/24 classification cases, 4/4 workflows; 32 model calls, 38,071
input and 2,162 output tokens. Median classification latency 2.05 s, P95 4.26 s
(nearest rank, n=24). All input data is synthetic; this is a single small
development-set measurement. Two separate unprefixed-reference probes reached
approval and are not included in those denominators. The test suite is separate:
117 local tests passed after the new model dependency was installed.

Manual draft review found an existing source risk: `cancel_order` returns on a
Shopify async job id and supplies a summary that says payment is refunded. The
model repeats completion/refund wording. Fixtures do not verify actual async
completion or settlement; this needs a separate state-contract correction and
development-store verification before claiming live cancellation/refund success.
# Frozen reliability test v1 (2026-10-30)

The original 24 examples remain development data. `evaluation/test_v1.json`
contains 40 new synthetic examples, fixed before the first run. They cover five
intents with eight cases each. Address tests additionally compare all fields,
including requiring omitted fields to stay null. This is a locally authored
frozen set, not a third-party blind benchmark or independent production sample.

`python scripts/evaluate_reliability.py` records dataset and prompt digests,
every prediction, errors, latency and token usage, then runs the four fixture
API workflows. Classification failures count in the denominator; auth/quota
failures stop further paid calls. Unknown billing is not reported as zero.

First run: intent/reference exact match 40/40, address fields 8/8, API flows 4/4.
Median serial classification latency 1.54 s, nearest-rank P95 2.23 s; 47 model
calls, 58,512 input and 3,387 output tokens. These are not RAG retrieval metrics
or a throughput/load test. Do not compare this latency directly to the different
24-example development set to claim a performance improvement.

After the submission-contract correction, 124 local tests passed; frontend
TypeScript, Vite build and lint passed. Live model tests used fixture Shopify
responses only. No real store write was attempted.

# Retrieval-only baseline (2026-10-30)

Run `python scripts/evaluate_retrieval.py`. It saves the dataset, source hashes
and policy chunks before retrieval, then builds an isolated Chroma index.
The unchanged production retrieval seam uses heading chunks, cosine distance,
default all-MiniLM-L6-v2 embeddings and top-three results. The embedding cache
stays in `var/embedding_cache`; the app's default index is not overwritten or
created. No API key, paid chat calls or Shopify access is used. A completed run
cannot be overwritten. Corpus/labels/retriever drift rejects an existing manifest.

`evaluation/retrieval_v1.json` has 40 English answerable questions (eight per
topic, 36 single-evidence and four two-evidence questions), 10 paired Chinese
questions and six unanswerable questions. All are Codex-authored synthetic
examples fixed before the first retrieval. ID labels mark direct support for
the requested facts, not general topical overlap. They are not an external
blind benchmark or a user's independent annotation.

Precision is relevant retrieved chunks / returned chunks; recall is relevant
retrieved chunks / reference chunks. Report macro means and micro counts
separately. This matches Ragas ID-based formulas but uses our own scorer, not
Ragas LLM-based Context Precision (which is ranking weighted). Missing query
results score zero for answerable questions. Zero-reference recall is undefined,
and language/negative groups are never mixed into the English denominator.

First real retrieval run: English recall 44/44 = 100%, precision 44/120 =
36.67%, hit rate and full-evidence coverage 40/40, MRR@3 0.9583. At fixed top3,
44/120 is the maximum precision permitted by these strict relevance labels.
Chinese recall 6/10 = 60%, precision 6/30 = 20%. All six unanswerable questions
returned three chunks, which is a retrieval observation, not proof that the
answering model hallucinates. No answer generation, refusal evaluation or
end-to-end policy-question workflow ran. Retrieval settings were not tuned.

Raw reports and frozen evidence are in `var/evaluations/retrieval_v1_first/`;
the scorer has seven offline regression tests for denominator/label errors.
The full local suite now passes 131 tests. On Windows the restricted runner
blocked asyncio's local socket-pair setup; the suite completed in 8.321 seconds
when run with local networking permitted. No external model/store calls ran.

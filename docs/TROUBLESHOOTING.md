# Troubleshooting

Problems we hit and how we fixed them. Newest first. Format: symptom → cause → fix.

Entries dated 2026-10-28 concern this local fork. The July entries preserve
upstream troubleshooting history; their live-store results were not repeated
for this local extension.

---

## Windows API tests wait inside socketpair (local, 2026-10-30)

**Symptom:** Full test discovery appeared stuck at the first TestClient context.
**Cause:** A timed traceback showed asyncio waiting in Windows socketpair setup
while the restricted runner disallowed local socket access.
**Fix:** Run the same tests with local networking allowed. All 131 passed in
8.321 seconds; no product change, paid model call or Shopify access was needed.

## Cancellation receipt was described as completed (local, 2026-10-30)

**Symptom:** The initial real-model draft confirmed cancellation/refund after
the write adapter only received an asynchronous job receipt.

**Cause:** The adapter summary said payment was refunded, the task outcome was
always `executed`, and the drafting prompt told the model to confirm completion.

**Fix:** Cancellation now returns `submitted`, preserves job metadata and marks
settlement unverified. A deterministic receipt bypasses free-form drafting when
any task is submitted. UI labels distinguish submissions from completed actions.
Regression tests cover pending/done/missing receipts and a draft model that would
otherwise claim a completed refund. This is a local contract correction, not a
live Shopify verification or a completion-polling implementation.

## Missing order number ended the cancellation flow (local, 2026-10-28)

**Symptom:** A single request such as "Please cancel my order" was escalated
because it named no order, with no structured way to continue the ticket.

**Cause:** The worker treated incomplete tasks as final escalation results.
All pending interrupts and status handling represented human approval only.

**Fix:** Add a clarification interrupt before validation/fan-out for this one
case. Keep its status and input separate from approval, validate the answer
before resume and inside the graph, and fill only the original task's order
reference. Do not create another ticket with the same id.

## Offline checks blocked by Windows sandbox resources (local, 2026-10-28)

**Symptom:** The restricted test runner stalled in TestClient's event-loop
setup; a SQLite restart test could not open its temporary database and failed
to clean up the temporary directory.

**Cause:** This agent sandbox restricted loopback and temporary-directory
resources needed by the local test runtime.

**Fix:** Run the same offline checks in a permitted local process with access
to those resources. The checks use fake clients; granting those runtime
resources does not require Shopify credentials or model calls.

## Git clone unavailable during local setup (local, 2026-10-28)

**Symptom:** Git cloning did not complete in the setup environment.

**Fix:** Obtain the official GitHub codeload archive for upstream commit
`9c1d5644effac7aed10079d23fc2a9f3f2982b1a` and initialize a local source
snapshot on `feature/order-clarification`. This preserves source provenance,
but it does not import upstream commit history.

## v1 tickets returned 500 after adding v2 task ids (2026-07-15)

**Symptom:** The operator console loaded new v2 tickets, but detail requests for
older tickets returned 500 with `KeyError: 'task_id'`.

**Cause:** Existing SQLite checkpoints contain v1 tasks and approval payloads
created before task ids existed.

**Fix:** Normalize legacy tasks, results, and approval payloads at the API
boundary, and use `task-1` when an old single-task graph resumes.

## Operator console showed "Unexpected token '<'" (2026-07-14)

**Symptom:** The ticket list failed with `Unexpected token '<'` because the
`/api/tickets` response started with `<!doctype html>`.

**Cause:** A stale Vite process was serving the SPA without the current
`/api` proxy configuration, so the API path fell back to `index.html`.

**Fix:** Stop that Vite process and restart `npm run dev` from `frontend/`.
Confirm `http://127.0.0.1:5173/api/tickets` returns JSON before reloading the UI.

## PowerShell curl request reached FastAPI with invalid JSON (2026-07-13)

**Symptom:** A JSON-looking `curl.exe -d` command returned 422 with
`json_invalid` and "Expecting property name enclosed in double quotes."

**Cause:** PowerShell's native-command argument handling removed the embedded
JSON quotes before curl sent the body.

**Fix:** Build the body with `ConvertTo-Json`, pipe it to `curl.exe`, and use
`--data-binary '@-'` to read the exact JSON from standard input.

## Temporary SQLite database stayed locked on Windows (2026-07-13)

**Symptom:** Registry tests passed their assertions but cleanup failed with
`PermissionError: [WinError 32]` for `tickets.sqlite`.

**Cause:** A `sqlite3.Connection` context manager commits or rolls back but
does not close the connection.

**Fix:** Wrap each short-lived registry connection with `contextlib.closing`
so Windows can release and delete the database file immediately.

## Refund came back as 0.0 USD (2026-07-13)

**Symptom:** `refundCreate` succeeded but refunded 0.0 USD, and the order
stayed PAID. Earlier attempts found no refundable transaction at all, and
`maximumRefundableV2` was null even on an order with a real SALE transaction.

**Cause:** Seed orders created with `financialStatus: PAID` but no
`transactions` have no payment to reverse — Shopify can only mark their items
refunded. Hand-picking transactions by `maximumRefundableV2` also breaks
because manual-gateway transactions report it as null.

**Fix:** Ask Shopify instead of guessing: `order.suggestedRefund` returns the
exact transactions to reverse; feed them to `refundCreate`. The seed script
now attaches a captured SALE transaction to new orders so they refund like
really-paid orders.

## refundCreate rejected: @idempotent directive required (2026-07-13)

**Symptom:** `refundCreate` failed with "The @idempotent directive is
required for this mutation but was not provided."

**Cause:** The 2026-04 Admin API requires an idempotency key on refund
mutations.

**Fix:** Add `@idempotent(key: $idempotencyKey)` to the mutation field. We
derive the key from the order id, which doubles as protection against a
resumed graph node refunding the same order twice.

## OpenRouter rejected every call with 401 (2026-07-12)

**Symptom:** `UnauthorizedResponseError: Missing Authentication header`, even
though `OPENROUTER_API_KEY` was set and loaded.

**Cause:** The key was a bare 64-character hex string — a provisioning-style
key, not an inference key. OpenRouter returns its generic "missing header"
message for keys it doesn't recognize.

**Fix:** Create an API key at openrouter.ai → Settings → Keys and use that.
Inference keys start with `sk-or-v1-`.

## Old seed order passed the refund rule (2026-07-12)

**Symptom:** A 40-day-old seeded order appeared new.

**Cause:** Shopify `createdAt` was today; the seeded date was in `processedAt`.

**Fix:** Normalize `processedAt` as the order date used by the policy gate.

## Order query blocked after scopes were deployed (2026-07-12)

**Symptom:** Auth worked, but `orders` returned a protected customer data error.

**Cause:** The app had no distribution method, so the installed grant was stale.

**Fix:** Select **Custom distribution**, uninstall the app, then reinstall it.
Order queries now work. The token lists `write_orders`, not `read_orders`.

**Seed status:** 50/50 orders exist. Reruns create only missing segments;
use `--plan` to preview.

## Shopify auth: from `app_not_installed` to granted scopes (2026-07-12)

### How scopes flow

Scopes live in four places. They only move forward at specific moments:

```
shopify.app.*.toml            what you WANT
      |  shopify app deploy
      v
released app version          what the app DECLARES
      |  Install app -> consent screen -> approve
      v
store's grant to the app      what the store GRANTED (frozen at consent)
      |  POST /admin/oauth/access_token
      v
token `scope` field           what API calls CAN DO
```

Editing the TOML does nothing until you deploy. Deploying does nothing for a
store that already installed — the grant froze at consent time. The token
mirrors the grant, not the config. On `ACCESS_DENIED`, ask: which stage did
the scopes stop at?

### `app_not_installed` from the token endpoint

Valid credentials are not enough — client credentials only works for installed
apps. Fix: Dev Dashboard → app → Installs → Install app.

Same error while installed = the `.env` credentials belong to a different app
or an old secret. Compare Client IDs.

### Token works, but `ACCESS_DENIED` on `orders`

The token carried zero scopes — we log the token's `scope` field as
`ShopifyClient.granted_scopes`. Two causes stacked:

1. The version live at install time declared no scopes, so the store granted none.
2. `use_legacy_install_flow = true` — legacy flow never grants config scopes
   for a UI-less app.

Fix: set `use_legacy_install_flow = false` in the TOML,
`shopify app deploy --allow-updates`, reinstall the app.

### CLI: "No app with client ID ... found"

Our hand-written TOML had a client ID copied from a screenshot, one chunk
duplicated. Every CLI command failed, some with misleading errors.

Fix: `shopify app config link` — the CLI writes the TOML with the real ID.
Rule: never copy IDs from screenshots; use a machine source (copy button,
CLI, API response).

### Browser lands on "Example Domain" after install

Harmless. Shopify redirects to the app's `application_url` after install; ours
was the `example.com` placeholder. UI-less apps should use
`https://shopify.dev/apps/default-app-home`.

### Tools that cracked it

- `ShopifyClient.granted_scopes` — turns "auth is broken" into "the store
  granted nothing".
- `shopify app config link` — the CLI's own list of your apps; if it can't see
  the app there, no deploy will either.

# Model-selected read-only tools

This slice adds actual tool execution, beyond using function-calling schemas as
structured output. It does not grant the model order-write authority.

## Tools

- `search_store_policy(question)`: local enhanced RAG over public shop rules.
  Its nested rewrite/grading steps use the configured model.
- `convert_currency(amount, base_currency, quote_currency)`: makes a real HTTP
  GET to a fixed Frankfurter v2 host, validates the returned currency pair and
  positive rate, then multiplies with Decimal and rounds to two decimals.
  The external service sees only a currency pair, not customer text or amounts.
  Amounts are positive, <=1 million, with at most two decimal places. Supported
  codes are USD/EUR/CNY/GBP/JPY. No arbitrary URLs or headers are accepted.

Frankfurter needs no key. [Official API documentation](https://frankfurter.dev/)
describes date-stamped reference rates and local amount conversion. These are
indicative conversions, not refund settlement promises or changes to eligibility.

## Execution

`tool_agent.run_tool_agent` builds a LangGraph `agent -> tools -> agent` loop.
The model sees tool names, descriptions and Pydantic argument schemas through
`bind_tools(..., tool_choice="auto")`. The registry validates and executes only
allowed names; each result returns as `ToolMessage` with the matching call ID.
The model can request one or both tools, or answer without a tool when appropriate.

There are at most three executed tool calls and four model decision turns;
extra requested calls return errors without execution. The policy tool can add
its own bounded rewrite/grading calls, so this is not a total three-LLM-call
budget. HTTP connect/read timeouts are 5/15 seconds and redirects are disabled.
Errors are returned as structured tool outcomes. Prompt constraints are not a
complete hallucination or injection defense. The loop itself has no persistent
checkpointer; it is a short read-only query, not restartable order execution.

## Run

Configure the existing DeepSeek key in local `.env`, never commit it. Index the
policy documents once, then run:

```sh
uv run python scripts/index_policies.py
uv run python scripts/run_tool_agent.py
uv run python scripts/run_tool_agent.py --question "100 USD to CNY, please use the current reference rate."
```

Or double-click `run_readonly_agent.cmd`. This uses paid model calls and the
public rate API. Tracing export is disabled; a local trace goes to
`var/tool_agent/` with tool requests, validated outputs, source/date and usage.

For existing policy-question tickets, `--rag agentic` enables the same loop in
the application policy handler:

```sh
uv run python -m storekeeper.live_preview --rag agentic --port 8768
```

The classifier still controls which tickets reach policy handling. Pure
currency-only questions may classify as `other`; use the direct CLI above for
those queries. Other order branches retain existing code rules and approval.
This does not add a general payment agent or alter the intent classifier.

## Actual run, 2026-11-04

For a combined Chinese question about refund review and USD100 conversion, the
real model requested both registered tools in one response. Python executed
them and returned the rule evidence and HTTP result. The policy evidence was
`returns-and-refunds.md#high-value-orders`; the external tool returned rate
6.6578 for USD/CNY dated 2026-11-04, giving CNY665.78. The final model reply used
both results and identified the conversion as indicative. This is a single
functional example, not a measured task completion or hallucination rate.

`tests/test_tool_agent.py` checks Decimal calculations, currency mismatch,
invalid amounts, actual registry dispatch, ToolMessages, unknown tools, call
budget, tool errors and the application policy handler. No production deployment,
MCP server or autonomous order operations are implied.

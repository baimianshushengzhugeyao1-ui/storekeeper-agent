"""Strict acceptance checks and API accounting for synthetic order scenarios."""
import time
from datetime import datetime

from langchain_core.callbacks import BaseCallbackHandler


class ModelAccounting(BaseCallbackHandler):
    def __init__(self):
        self.records, self.started = [], {}

    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.started[str(run_id)] = time.perf_counter()

    def on_llm_start(self, serialized, prompts, *, run_id, **kwargs):
        self.started.setdefault(str(run_id), time.perf_counter())

    def on_llm_end(self, response, *, run_id, **kwargs):
        message = response.generations[0][0].message
        usage = getattr(message, "usage_metadata", None) or {}
        if not usage:
            raw = (response.llm_output or {}).get("token_usage", {})
            usage = {"input_tokens": raw.get("prompt_tokens", 0),
                     "output_tokens": raw.get("completion_tokens", 0),
                     "total_tokens": raw.get("total_tokens", 0)}
        started = self.started.pop(str(run_id), time.perf_counter())
        self.records.append({"status": "ok", "seconds": round(time.perf_counter() - started, 4),
                             "usage": usage})

    def on_llm_error(self, error, *, run_id, **kwargs):
        started = self.started.pop(str(run_id), time.perf_counter())
        self.records.append({"status": "error", "seconds": round(time.perf_counter() - started, 4),
                             "error_type": type(error).__name__, "http_status": getattr(error, "status_code", None)})

    def summary(self):
        return {"scope": "all recorded model calls, including preflight, classifier, discovery and drafter",
                "calls": len(self.records), "successful_calls": sum(r["status"] == "ok" for r in self.records),
                "input_tokens": sum(r.get("usage", {}).get("input_tokens", 0) for r in self.records),
                "output_tokens": sum(r.get("usage", {}).get("output_tokens", 0) for r in self.records),
                "total_tokens": sum(r.get("usage", {}).get("total_tokens", 0) for r in self.records)}


def assess_discovery(case: dict, initial: dict, current: dict) -> dict[str, bool]:
    """Do not award success merely because the final order number looks right."""
    trace = current.get("order_resolution") or {}
    events = [e for e in trace.get("events", []) if e.get("kind") == "tool"]
    searches = [e for e in events if e.get("name") == "search_orders_by_time" and e.get("result", {}).get("ok")]
    names = [e.get("name") for e in events]
    checks = {
        "initial_status": initial.get("status") == case["status"],
        "pending_approval": current.get("status") == "pending_approval",
        "verified_binding": trace.get("status") == "resolved" and trace.get("selected_reference") == case["reference"],
        "correct_task": len(current.get("tasks", [])) == 1
                        and current["tasks"][0].get("intent") == "cancel_order"
                        and current["tasks"][0].get("order_reference") == case["reference"],
        "selection_tool_executed": "select_order" in names,
        "no_write_tool": not any(name in ("cancel_order", "issue_refund", "update_shipping_address") for name in names),
    }
    if case.get("answer"):
        pending = initial.get("pending_clarifications", [])
        choices = pending[0].get("choices") or [] if len(pending) == 1 else []
        checks["choice_set"] = len(pending) == 1 and {o["order_reference"] for o in choices} == set(case.get("choices", []))
        checks["customer_choice_recorded"] = trace.get("customer_selected_reference") == case["reference"]
        checks["clarification_tool_executed"] = "request_order_reference" in names
    if case.get("search_start"):
        checks["time_search_result"] = any(
            datetime.fromisoformat(e["arguments"]["start_time"]) == datetime.fromisoformat(case["search_start"])
            and datetime.fromisoformat(e["arguments"]["end_time"]) == datetime.fromisoformat(case["search_end"])
            and e["result"].get("match_count") == case["match_count"]
            for e in searches)
    else:
        checks["no_invented_time_search"] = not searches
    return checks

"""Small live-model acceptance run; synthetic customers, no real order writes."""
import argparse
import json
import os
import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from storekeeper.live_model import PROJECT_ROOT, create_live_model
from storekeeper.order_agent_preview import create_order_agent_preview
from storekeeper.order_agent_evaluation import ModelAccounting, assess_discovery

CASES = [
    {"id": "unique", "question": "帮我取消昨天下午13点到14点下的订单。", "status": "pending_approval", "reference": "#2036",
     "search_start": "2026-11-03T13:00:00+08:00", "search_end": "2026-11-03T14:00:00+08:00", "match_count": 1},
    {"id": "multiple", "question": "帮我取消昨天下午下的那个订单。", "status": "waiting_for_customer", "answer": "#2037", "reference": "#2037",
     "search_start": "2026-11-03T12:00:00+08:00", "search_end": "2026-11-03T18:00:00+08:00", "match_count": 2, "choices": ["#2036", "#2037"]},
    {"id": "missing", "question": "帮我取消订单。", "status": "waiting_for_customer", "answer": "#2036", "reference": "#2036"},
    {"id": "empty", "question": "帮我取消昨天上午9点到10点下的订单。", "status": "waiting_for_customer", "answer": "#2036", "reference": "#2036",
     "search_start": "2026-11-03T09:00:00+08:00", "search_end": "2026-11-03T10:00:00+08:00", "match_count": 0, "choices": []},
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay", action="store_true", help="Scripted integration rehearsal, not LLM evaluation.")
    parser.add_argument("--case", choices=[c["id"] for c in CASES])
    args = parser.parse_args()
    os.environ["LANGSMITH_TRACING"] = "false"
    run = PROJECT_ROOT / "var/order_agent_evaluation" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    run.mkdir(parents=True)
    accounting = ModelAccounting()
    hashes = {relative: hashlib.sha256((PROJECT_ROOT / relative).read_bytes()).hexdigest() for relative in
              ["src/storekeeper/order_resolution.py", "src/storekeeper/order_agent_fixtures.py", "scripts/evaluate_order_agent.py"]}
    report = {"mode": "scripted_replay" if args.replay else "real_deepseek", "status": "running", "cases": [],
              "source_sha256": hashes, "scope": "four synthetic development scenarios; regression, not unseen test accuracy"}
    # Preflight prevents one failed billing call per case; retain the error class
    # and HTTP status only, never the key, request headers or full exception.
    model = None
    if not args.replay:
        try:
            model = create_live_model(callbacks=[accounting])
            response = model.invoke("Reply with exactly OK.")
            report["preflight"] = {"usage": response.usage_metadata or {}, "model": model.model_name}
        except Exception as error:
            report.update(status="blocked", error_type=type(error).__name__,
                          http_status=getattr(error, "status_code", None))
            report.update(model_usage_summary=accounting.summary(), model_calls=accounting.records)
            (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({**report, "report_path": str(run / "report.json")}, ensure_ascii=True))
            return
    for case in CASES:
        if args.case and case["id"] != args.case:
            continue
        app = create_order_agent_preview(run / case["id"], replay=args.replay, model=model)
        with TestClient(app) as http:
            started = time.perf_counter()
            created = http.post("/api/tickets", json={"ticket_id": case["id"], "ticket_text": case["question"]})
            row = {"id": case["id"], "question": case["question"], "http_status": created.status_code,
                   "initial": created.json(), "expected": case}
            passed = created.status_code == 201 and row["initial"]["status"] == case["status"]
            current = row["initial"]
            if passed and case.get("answer"):
                interrupt_id = current["pending_clarifications"][0]["interrupt_id"]
                continued = http.post(f"/api/tickets/{case['id']}/clarification", json={
                    "interrupt_id": interrupt_id, "order_reference": case["answer"]})
                row["continued_status"], row["continued"] = continued.status_code, continued.json()
                current = row["continued"]
                passed = continued.status_code == 200 and current["status"] == "pending_approval"
            row["checks"] = assess_discovery(case, row["initial"], current)
            passed = passed and all(row["checks"].values())
            row["writes_before_approval"] = len(app.state.fixture_shopify_client.write_calls)
            passed = passed and row["writes_before_approval"] == 0
            if passed:
                approval = current["pending_approvals"][0]
                decided = http.post(f"/api/tickets/{case['id']}/decision", json={
                    "interrupt_id": approval["interrupt_id"], "decision": "approve"})
                row["approved_status"], row["approved"] = decided.status_code, decided.json()
                results = row["approved"].get("task_results", [])
                action = results[0].get("action_result") or {} if results else {}
                passed = (decided.status_code == 200 and len(app.state.fixture_shopify_client.write_calls) == 1
                          and bool(results) and results[0].get("outcome") == "submitted"
                          and action.get("simulated") is True and action.get("refund_status") == "unverified")
            row["automated_flow_seconds"] = round(time.perf_counter() - started, 4)
            row["passed"] = passed
            report["cases"].append(row)
            report.update(model_usage_summary=accounting.summary(), model_calls=accounting.records)
            (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"id": case["id"], "passed": passed}, ensure_ascii=True), flush=True)
            if created.status_code in (502, 503):
                report["status"] = "blocked"
                break
    if report["status"] == "running":
        report["status"] = "complete"
    report["passed"] = sum(row["passed"] for row in report["cases"])
    report["total"] = len(report["cases"])
    (run / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(run / "report.json"), flush=True)


if __name__ == "__main__":
    main()

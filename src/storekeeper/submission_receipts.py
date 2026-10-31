"""Evidence-bound receipts for submissions whose external effect is unverified."""

from storekeeper.domain import TaskResult


def submission_receipt(task_results: list[TaskResult]) -> str:
    lines = []
    for result in task_results:
        reference = result["task"].get("order_reference") or "(no order reference)"
        if result["outcome"] == "submitted":
            lines.append(f"Order {reference}: cancellation request submitted. Cancellation completion and refund settlement have not been verified; please await confirmation.")
        elif result["outcome"] == "denied_by_policy":
            reason = (result.get("gate_verdict") or {}).get("reason", "Store policy does not allow this action.")
            lines.append(f"Order {reference}: request denied. {reason}")
        elif result["outcome"] == "rejected_by_human":
            lines.append(f"Order {reference}: the operator declined the request; no change was made.")
        else:
            action_result = result.get("action_result") or {}
            details = action_result.get("answer") or action_result.get("summary") or action_result.get("reason") or result["outcome"]
            lines.append(f"Order {reference}: {details}")
    notice = "TEST ORDERS (OFFLINE DEMO): No real order or payment was changed.\n\n" if any((result.get("action_result") or {}).get("simulated") for result in task_results) else ""
    return notice + "\n\n".join(lines) + "\n\nThe Support Team"

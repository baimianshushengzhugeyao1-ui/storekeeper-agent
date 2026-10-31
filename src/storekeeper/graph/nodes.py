"""Node functions and routing rules for the ticket graph.

Routing decisions live in plain Python route functions. The LLM classifies
and drafts; it never chooses which node runs next.
"""

import json
from datetime import datetime, timezone
from typing import Callable, Literal

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command, Send, interrupt
from pydantic import BaseModel, Field

from storekeeper.classify import classify_ticket
from storekeeper.domain import Task, TaskOutcome, TaskResult
from storekeeper.submission_receipts import submission_receipt
from storekeeper.graph.state import TaskState, TicketState
from storekeeper.graph.task_plan import find_task_plan_conflict
from storekeeper.policy.gate import policy_gate
from storekeeper.policy_docs import find_policy_context, format_policy_extracts
from storekeeper.shopify.client import ShopifyClient
from storekeeper.shopify.operations import (
    InvalidOrderReferenceError,
    OrderNotFoundError,
    lookup_order,
)
from storekeeper.shopify.writes import (
    build_updated_shipping_address,
    cancel_order,
    issue_full_refund,
    update_shipping_address,
)

REPLY_DRAFT_SYSTEM_PROMPT = """\
You draft replies to customer support tickets for a Shopify store.

You receive the customer's message and structured task results describing what
the system did or decided. Write a short, warm, professional reply.

Rules:
- State only what the task results say. Never promise anything beyond them.
- Outcome "executed": confirm the action was completed.
- Outcome "submitted": the request was accepted, but completion and refund settlement are unverified. Never claim they completed.
- Outcome "denied_by_policy": explain the denial using the gate reason; when
  store policy text is provided, refer to the policy naturally.
- Outcome "rejected_by_human": say the request was reviewed and declined.
- Outcome "failed": apologize and ask the customer to double-check the details
  they gave, such as the order number.
- Outcome "answered": relay the provided answer faithfully; do not add policy
  claims that are not in it.
- Outcome "escalated_to_human": explain that a team member must handle that
  request and use the provided reason. Do not imply the request was completed.
- When several task results are provided, cover every result in one coherent
  reply and keep the customer's original request order.
- Do not invent order details, amounts, dates, or policy that are not in the input.
- Sign off as "The Support Team".
"""

POLICY_ANSWER_SYSTEM_PROMPT = """\
You answer customer questions about store policy for a Shopify store.

You receive the customer's message and the store's policy documents. Answer
using only what the documents say.

Rules:
- If the documents do not cover the question, say so and tell the customer a
  team member will follow up. Never invent policy.
- Keep the answer short and factual.
- In cited_documents, list the file name of every document you used.
"""


class PolicyAnswer(BaseModel):
    """A policy answer plus the documents that back it."""

    answer: str = Field(
        description="The answer to the customer's question, based only on the policy documents."
    )
    cited_documents: list[str] = Field(
        description="File names of the policy documents the answer is based on."
    )


# --- Ticket-level nodes -----------------------------------------------------


def make_classify_node(classifier_model: BaseChatModel | None) -> Callable:
    def classify_node(state: TicketState) -> dict:
        return {"tasks": classify_ticket(state["ticket_text"], classifier_model=classifier_model)}

    return classify_node


def get_task_id(task: Task) -> str:
    # v1 checkpoints predate task ids and can still resume after upgrading.
    return task.get("task_id") or "task-1"


def validate_task_plan_node(state: TicketState) -> dict:
    if not state["tasks"]:
        return {"plan_conflict_reason": "The classifier found no request in the ticket."}
    return {"plan_conflict_reason": find_task_plan_conflict(state["tasks"])}


def dispatch_ticket_tasks(
    state: TicketState,
) -> Literal["escalate_ticket"] | list[Send]:
    if state["plan_conflict_reason"] is not None:
        return "escalate_ticket"
    return [
        Send(
            "process_task",
            {
                "ticket_text": state["ticket_text"],
                "task": task,
            },
        )
        for task in state["tasks"]
    ]


def escalate_ticket_node(state: TicketState) -> dict:
    return {
        "ticket_outcome": "escalated_to_human",
        "escalation_reason": (
            state["plan_conflict_reason"]
            or describe_escalation_reason(state["tasks"])
        ),
    }


def describe_escalation_reason(tasks: list[Task]) -> str:
    if len(tasks) == 0:
        return "The classifier found no request in the ticket."
    if len(tasks) > 1:
        return "The ticket plan needs human review before its tasks can run."
    single_task = tasks[0]
    if single_task["requested_action"] is None:
        return f"Intent '{single_task['intent']}' has no automated handling yet."
    if not single_task["order_reference"]:
        return "The request names no order."
    if single_task["intent"] == "address_change":
        return (
            "The request needs a complete new shipping address: street, city, "
            "state or province, postal code, and country."
        )
    return "The request cannot be automated."


def answer_policy_task(
    ticket_text: str,
    question_task: Task,
    answer_model: BaseChatModel,
) -> TaskResult:
    policy_extracts = find_policy_context(question_task, ticket_text=ticket_text)
    structured_answerer = answer_model.with_structured_output(PolicyAnswer)
    policy_answer = structured_answerer.invoke(
        [
            SystemMessage(content=POLICY_ANSWER_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    f"Customer question:\n{ticket_text}\n\n"
                    f"Policy documents:\n{format_policy_extracts(policy_extracts)}"
                )
            ),
        ]
    )
    provided_document_names = {
        extract["document_name"] for extract in policy_extracts
    }
    verified_citations = [
        document_name
        for document_name in policy_answer.cited_documents
        if document_name in provided_document_names
    ]
    return {
        "task_id": get_task_id(question_task),
        "task": question_task,
        "outcome": "answered",
        "gate_verdict": None,
        "action_result": {"answer": policy_answer.answer},
        "policy_citations": verified_citations,
    }


def make_draft_reply_node(draft_model: BaseChatModel) -> Callable:
    def draft_reply_node(state: TicketState) -> dict:
        task_order = {
            get_task_id(task): task_number
            for task_number, task in enumerate(state["tasks"])
        }
        ordered_task_results = sorted(
            state["task_results"],
            key=lambda task_result: task_order.get(
                task_result.get("task_id") or get_task_id(task_result["task"]),
                len(task_order),
            ),
        )
        # default=str renders Decimal amounts and datetimes readably.
        task_results_json = json.dumps(ordered_task_results, indent=2, default=str)

        # Denied requests get the relevant policy text so the reply can cite
        # store policy instead of just the gate's one-line reason.
        denied_task_results = [
            task_result
            for task_result in ordered_task_results
            if task_result["outcome"] == "denied_by_policy"
        ]
        policy_reference_block = ""
        if denied_task_results:
            policy_extracts = find_policy_context(denied_task_results[0]["task"])
            policy_reference_block = (
                f"\n\nStore policy for reference:\n{format_policy_extracts(policy_extracts)}"
            )

        if any(result["outcome"] == "submitted" for result in ordered_task_results):
            response = AIMessage(content=submission_receipt(ordered_task_results))
        else:
            response = draft_model.invoke(
                [
                    SystemMessage(content=REPLY_DRAFT_SYSTEM_PROMPT),
                    HumanMessage(
                        content=(
                            f"Customer message:\n{state['ticket_text']}\n\n"
                            f"Task results:\n{task_results_json}"
                            f"\n\nTicket escalation reason:\n"
                            f"{state['escalation_reason'] or '(none)'}"
                            f"{policy_reference_block}"
                        )
                    ),
                ]
            )
        task_escalation_reasons = [
            str(task_result["action_result"]["reason"])
            for task_result in ordered_task_results
            if task_result["outcome"] == "escalated_to_human"
            and task_result["action_result"] is not None
            and task_result["action_result"].get("reason")
        ]
        ticket_is_escalated = (
            state["ticket_outcome"] == "escalated_to_human"
            or bool(task_escalation_reasons)
            or any(
                task_result["outcome"] == "failed"
                for task_result in ordered_task_results
            )
        )
        failed_task_reason = (
            "One or more tasks could not complete automatically."
            if any(
                task_result["outcome"] == "failed"
                for task_result in ordered_task_results
            )
            else None
        )
        return {
            "reply_draft": response.content,
            "ticket_outcome": (
                "escalated_to_human" if ticket_is_escalated else "resolved"
            ),
            "escalation_reason": (
                state["escalation_reason"]
                or (" ".join(task_escalation_reasons) if task_escalation_reasons else None)
                or failed_task_reason
            ),
        }

    return draft_reply_node


def build_task_escalation_result(task: Task) -> TaskResult:
    return {
        "task_id": get_task_id(task),
        "task": task,
        "outcome": "escalated_to_human",
        "gate_verdict": None,
        "action_result": {"reason": describe_task_escalation_reason(task)},
        "policy_citations": [],
    }


def describe_task_escalation_reason(task: Task) -> str:
    if task["requested_action"] is None:
        return f"Intent '{task['intent']}' has no automated handling yet."
    if not task["order_reference"]:
        return "The request names no order."
    if task["intent"] == "address_change":
        return (
            "The request needs a complete new shipping address: street, city, "
            "state or province, postal code, and country."
        )
    return "The request cannot be automated."


# --- Task-pipeline nodes ------------------------------------------------------


def make_lookup_order_node(shopify_client: ShopifyClient | None) -> Callable:
    def lookup_order_node(state: TaskState) -> dict:
        # lookup_order validates and normalizes the customer-written reference
        # itself; a hostile or garbled reference fails softly here, never
        # reaching the gate or an approval.
        try:
            shopify_order = lookup_order(state["task"]["order_reference"], client=shopify_client)
        except (InvalidOrderReferenceError, OrderNotFoundError):
            return {"task_result": build_task_result(state, outcome="failed")}
        return {"shopify_order": shopify_order}

    return lookup_order_node


def route_after_lookup(state: TaskState) -> Literal["policy_gate", "__end__"]:
    if state.get("task_result") is not None:
        return END
    return "policy_gate"


def policy_gate_node(state: TaskState) -> dict:
    requested_action = state["task"]["requested_action"]
    shopify_order = state["shopify_order"]
    # Routing guarantees an action and a fetched order before this node runs.
    assert requested_action is not None and shopify_order is not None
    gate_verdict = policy_gate(
        requested_action,
        shopify_order["facts"],
        evaluated_at=datetime.now(timezone.utc),
    )
    return {"gate_verdict": gate_verdict}


def route_after_gate(state: TaskState) -> Literal["await_approval", "record_policy_denial"]:
    gate_verdict = state["gate_verdict"]
    # This route only runs after the gate node has written its verdict.
    assert gate_verdict is not None
    if gate_verdict["passed"]:
        return "await_approval"
    return "record_policy_denial"


def await_approval_node(state: TaskState) -> Command[Literal["execute_action", "record_human_rejection"]]:
    shopify_order = state["shopify_order"]
    gate_verdict = state["gate_verdict"]
    # Only the gate's passing edge leads here, so both are always present.
    assert shopify_order is not None and gate_verdict is not None
    order_facts = shopify_order["facts"]
    # The payload must be JSON-serializable, so the Decimal amount becomes text.
    approval_payload = {
        "question": "Approve this action?",
        "task_id": get_task_id(state["task"]),
        "action": state["task"]["requested_action"],
        "order": shopify_order["name"],
        # What the customer actually wrote, so the approver can audit the
        # request-to-order binding, not just the selected order.
        "requested_reference": state["task"]["order_reference"],
        "amount": f"{order_facts['total_amount']} {order_facts['currency_code']}",
        "gate_rule": gate_verdict["rule"],
        "gate_reason": gate_verdict["reason"],
        "flags": gate_verdict["flags"],
        "current_shipping_address": None,
        "new_shipping_address": None,
    }
    if state["task"]["requested_action"] == "update_shipping_address":
        requested_shipping_address = state["task"]["new_shipping_address"]
        assert requested_shipping_address is not None
        approval_payload["current_shipping_address"] = shopify_order["shipping_address"]
        approval_payload["new_shipping_address"] = build_updated_shipping_address(
            shopify_order["shipping_address"], requested_shipping_address
        )

    decision = interrupt(approval_payload)
    if decision == "approve":
        return Command(goto="execute_action")
    return Command(goto="record_human_rejection")


def make_execute_action_node(shopify_client: ShopifyClient | None) -> Callable:
    def execute_action_node(state: TaskState) -> dict:
        requested_action = state["task"]["requested_action"]
        shopify_order = state["shopify_order"]
        # Execution is only reachable through lookup, gate, and approval.
        assert shopify_order is not None
        shopify_order_id = shopify_order["id"]
        if requested_action == "cancel_order":
            action_result = cancel_order(shopify_order_id, client=shopify_client)
        elif requested_action == "issue_refund":
            action_result = issue_full_refund(shopify_order_id, client=shopify_client)
        elif requested_action == "update_shipping_address":
            requested_shipping_address = state["task"]["new_shipping_address"]
            assert requested_shipping_address is not None
            updated_shipping_address = build_updated_shipping_address(
                shopify_order["shipping_address"], requested_shipping_address
            )
            action_result = update_shipping_address(
                shopify_order_id,
                updated_shipping_address,
                client=shopify_client,
            )
        else:
            raise ValueError(f"No write operation wired for action: {requested_action}")
        outcome = "submitted" if action_result.get("operation_status") == "submitted" else "executed"
        return {"task_result": build_task_result(state, outcome=outcome, action_result=action_result)}

    return execute_action_node


def record_policy_denial_node(state: TaskState) -> dict:
    relevant_policy_names = [
        extract["document_name"] for extract in find_policy_context(state["task"])
    ]
    return {
        "task_result": build_task_result(
            state, outcome="denied_by_policy", policy_citations=relevant_policy_names
        )
    }


def record_human_rejection_node(state: TaskState) -> dict:
    return {"task_result": build_task_result(state, outcome="rejected_by_human")}


def build_task_result(
    state: TaskState,
    outcome: TaskOutcome,
    action_result: dict | None = None,
    policy_citations: list[str] | None = None,
) -> TaskResult:
    return {
        "task_id": get_task_id(state["task"]),
        "task": state["task"],
        "outcome": outcome,
        "gate_verdict": state.get("gate_verdict"),
        "action_result": action_result,
        "policy_citations": policy_citations if policy_citations is not None else [],
    }

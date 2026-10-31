"""Run one support ticket through the guarded pipeline, or resume a paused one."""

import argparse
import json

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from storekeeper.graph.build import build_ticket_graph
from storekeeper.graph.clarification import (
    is_clarification_payload,
    normalize_clarification_order_reference,
)
from storekeeper.tickets import (
    CHECKPOINT_DATABASE_PATH,
    DuplicateTicketError,
    create_ticket,
    get_ticket_status,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticket_id", help="Stable id for this ticket; also the handle used to resume it")
    parser.add_argument("ticket_text", nargs="?", help="The customer's message, quoted (omit when resuming)")
    decision_group = parser.add_mutually_exclusive_group()
    decision_group.add_argument(
        "--approve",
        metavar="INTERRUPT_ID",
        help="Approve one pending action by interrupt id",
    )
    decision_group.add_argument(
        "--reject",
        metavar="INTERRUPT_ID",
        help="Reject one pending action by interrupt id",
    )
    decision_group.add_argument(
        "--clarify", metavar="INTERRUPT_ID",
        help="Answer one pending order-number question (requires --order-reference)",
    )
    parser.add_argument("--order-reference", help="Customer-provided order number, for example '#1036'")
    arguments = parser.parse_args()

    resuming = any(value is not None for value in (arguments.approve, arguments.reject, arguments.clarify))
    if resuming and arguments.ticket_text:
        parser.error("Pass either ticket text or a decision flag, not both.")
    if not resuming and not arguments.ticket_text:
        parser.error("A new ticket needs ticket text; resuming needs --approve, --reject, or --clarify.")
    if (arguments.clarify is None) != (arguments.order_reference is None):
        parser.error("Use --clarify and --order-reference together.")
    normalized_order_reference = None
    if arguments.clarify is not None:
        try:
            normalized_order_reference = normalize_clarification_order_reference(arguments.order_reference)
        except ValueError as error:
            parser.error(str(error))

    CHECKPOINT_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    config = {"configurable": {"thread_id": arguments.ticket_id}}

    with SqliteSaver.from_conn_string(str(CHECKPOINT_DATABASE_PATH)) as checkpointer:
        ticket_graph = build_ticket_graph(checkpointer=checkpointer)
        if resuming:
            interrupt_id = arguments.approve or arguments.reject or arguments.clarify
            assert interrupt_id is not None
            decision = "approve" if arguments.approve is not None else "reject"
            state_snapshot = ticket_graph.get_state(config)
            completed_task_ids = {
                task_result.get("task_id")
                or task_result["task"].get("task_id")
                or "task-1"
                for task_result in state_snapshot.values.get("task_results", [])
            }
            pending_interrupt_ids = {
                pending_interrupt.id
                for pending_interrupt in state_snapshot.interrupts
                if pending_interrupt.value.get("task_id") not in completed_task_ids
            }
            if interrupt_id not in pending_interrupt_ids:
                parser.error(
                    f"Interrupt id {interrupt_id!r} is not pending on "
                    f"ticket {arguments.ticket_id!r}."
                )
            selected_interrupt = next(
                pending_interrupt for pending_interrupt in state_snapshot.interrupts
                if pending_interrupt.id == interrupt_id
            )
            if is_clarification_payload(selected_interrupt.value) != (arguments.clarify is not None):
                parser.error("Use --clarify for a customer question and --approve/--reject for an approval.")
            resume_value = (
                {"order_reference": normalized_order_reference}
                if arguments.clarify is not None else decision
            )
            result = ticket_graph.invoke(
                Command(resume={interrupt_id: resume_value}),
                config,
            )
        else:
            assert arguments.ticket_text is not None
            if get_ticket_status(arguments.ticket_id, ticket_graph) != "not_found":
                parser.error(
                    f"Ticket id {arguments.ticket_id!r} already has saved graph state. "
                    "Use a new id for a new ticket."
                )
            try:
                create_ticket(arguments.ticket_id, arguments.ticket_text)
            except DuplicateTicketError as error:
                parser.error(str(error))
            result = ticket_graph.invoke(
                {
                    "ticket_text": arguments.ticket_text,
                    "tasks": [],
                    "task_results": [],
                    "reply_draft": None,
                    "ticket_outcome": None,
                    "escalation_reason": None,
                    "plan_conflict_reason": None,
                    "clarification_history": [],
                },
                config,
            )
        print_result(arguments.ticket_id, result)


def print_result(ticket_id: str, result: dict) -> None:
    if "__interrupt__" in result:
        pending_interrupts = result["__interrupt__"]
        print(
            f"== {len(pending_interrupts)} REQUEST(S) PAUSED "
            f"-- ticket {ticket_id} =="
        )
        for pending_interrupt in pending_interrupts:
            pending_approval = pending_interrupt.value
            print(f"\n  Interrupt id: {pending_interrupt.id}")
            if is_clarification_payload(pending_approval):
                print(f"  Waiting for customer: {pending_approval['question']}")
                print(
                    f"  To provide the order number: run_ticket.py {ticket_id} "
                    f"--clarify {pending_interrupt.id} --order-reference '#1036'"
                )
                continue
            print(
                f"  Action: {pending_approval['action']} "
                f"on order {pending_approval['order']}"
            )
            print(f"  Customer wrote: \"{pending_approval['requested_reference']}\"")
            print(f"  Amount: {pending_approval['amount']}")
            print(f"  Gate:   passed ({pending_approval['gate_rule']})")
            print(f"  Reason: {pending_approval['gate_reason']}")
            if pending_approval["new_shipping_address"] is not None:
                print(
                    "  Current address: "
                    f"{json.dumps(pending_approval['current_shipping_address'], ensure_ascii=False)}"
                )
                print(
                    "  New address:     "
                    f"{json.dumps(pending_approval['new_shipping_address'], ensure_ascii=False)}"
                )
            if pending_approval["flags"]:
                print(f"  Flags:  {', '.join(pending_approval['flags'])}")
            print(
                f"  To decide: run_ticket.py {ticket_id} "
                f"--approve {pending_interrupt.id}   or   "
                f"--reject {pending_interrupt.id}"
            )
        return

    print(f"== Ticket {ticket_id}: {result['ticket_outcome']} ==")
    if result["ticket_outcome"] == "escalated_to_human":
        print(f"  Reason: {result['escalation_reason']}")

    task_order = {
        task.get("task_id") or f"task-{task_number + 1}": task_number
        for task_number, task in enumerate(result["tasks"])
    }
    ordered_task_results = sorted(
        result["task_results"],
        key=lambda task_result: task_order[
            task_result.get("task_id")
            or task_result["task"].get("task_id")
            or "task-1"
        ],
    )
    for task_number, task_result in enumerate(ordered_task_results, start=1):
        print(f"  Task {task_number} outcome: {task_result['outcome']}")
        action_result = task_result["action_result"]
        if action_result and action_result.get("summary"):
            print(f"    Action: {action_result['summary']}")
        if action_result and action_result.get("reason"):
            print(f"    Reason: {action_result['reason']}")
        if task_result["policy_citations"]:
            print(
                "    Policy cited: "
                f"{', '.join(task_result['policy_citations'])}"
            )
    print()
    print("  Reply draft:")
    for draft_line in result["reply_draft"].splitlines():
        print(f"    {draft_line}")


if __name__ == "__main__":
    main()

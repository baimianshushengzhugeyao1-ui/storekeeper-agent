"""Assembles the ticket-handling graph and its per-task subgraph."""

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from langchain_openrouter import ChatOpenRouter

from storekeeper.config import load_classifier_settings
from storekeeper.graph.clarification import clarify_missing_order_reference_node
from storekeeper.graph.nodes import (
    answer_policy_task,
    await_approval_node,
    build_task_escalation_result,
    dispatch_ticket_tasks,
    escalate_ticket_node,
    make_classify_node,
    make_draft_reply_node,
    make_execute_action_node,
    make_lookup_order_node,
    policy_gate_node,
    record_human_rejection_node,
    record_policy_denial_node,
    route_after_gate,
    route_after_lookup,
    validate_task_plan_node,
)
from storekeeper.graph.state import TaskState, TicketState, TicketTaskWorkerState
from storekeeper.shopify.client import ShopifyClient
from storekeeper.shopify.writes import requested_shipping_address_is_complete


def build_task_pipeline_graph(shopify_client: ShopifyClient | None) -> CompiledStateGraph:
    """The guarded per-task pipeline: lookup -> gate -> approval -> execute.

    Compiled without its own checkpointer; it inherits the parent graph's
    checkpointer through the config it is invoked with.
    """
    task_builder = StateGraph(TaskState)
    task_builder.add_node("lookup_order", make_lookup_order_node(shopify_client))
    task_builder.add_node("policy_gate", policy_gate_node)
    task_builder.add_node("await_approval", await_approval_node)
    task_builder.add_node("execute_action", make_execute_action_node(shopify_client))
    task_builder.add_node("record_policy_denial", record_policy_denial_node)
    task_builder.add_node("record_human_rejection", record_human_rejection_node)

    task_builder.add_edge(START, "lookup_order")
    task_builder.add_conditional_edges("lookup_order", route_after_lookup, ["policy_gate", END])
    task_builder.add_conditional_edges(
        "policy_gate", route_after_gate, ["await_approval", "record_policy_denial"]
    )
    # await_approval routes itself via Command: execute_action or record_human_rejection.
    task_builder.add_edge("execute_action", END)
    task_builder.add_edge("record_policy_denial", END)
    task_builder.add_edge("record_human_rejection", END)
    return task_builder.compile()


def build_ticket_graph(
    *,
    checkpointer: BaseCheckpointSaver,
    classifier_model: BaseChatModel | None = None,
    draft_model: BaseChatModel | None = None,
    answer_model: BaseChatModel | None = None,
    shopify_client: ShopifyClient | None = None,
    order_agent_model=None,
    order_directory=None,
) -> CompiledStateGraph:
    """Classify, clarify a missing order, validate, process tasks, then draft."""
    if draft_model is None:
        # The drafter reuses the classifier's OpenRouter model for now.
        classifier_settings = load_classifier_settings()
        draft_model = ChatOpenRouter(model=classifier_settings.openrouter_model)
    if answer_model is None:
        answer_model = draft_model

    task_pipeline_graph = build_task_pipeline_graph(shopify_client)

    def process_task(state: TicketTaskWorkerState, config: RunnableConfig) -> dict:
        # This worker re-runs whenever an interrupt inside the action subgraph
        # resumes, so it must stay free of side effects outside that subgraph.
        task = state["task"]
        if task["intent"] == "policy_question":
            task_result = answer_policy_task(
                state["ticket_text"],
                task,
                answer_model,
            )
            return {"task_results": [task_result]}

        task_can_use_action_pipeline = (
            task["requested_action"] is not None
            and task["order_reference"] is not None
            and (
                task["intent"] != "address_change"
                or requested_shipping_address_is_complete(
                    task["new_shipping_address"]
                )
            )
        )
        if not task_can_use_action_pipeline:
            return {"task_results": [build_task_escalation_result(task)]}

        task_output = task_pipeline_graph.invoke(
            {
                "task": task,
                "shopify_order": None,
                "gate_verdict": None,
                "task_result": None,
            },
            config,
        )
        return {"task_results": [task_output["task_result"]]}

    ticket_builder = StateGraph(TicketState)
    ticket_builder.add_node("classify", make_classify_node(classifier_model))
    ticket_builder.add_node("clarify_missing_order_reference", clarify_missing_order_reference_node)
    ticket_builder.add_node("validate_task_plan", validate_task_plan_node)
    ticket_builder.add_node("process_task", process_task)
    ticket_builder.add_node("escalate_ticket", escalate_ticket_node)
    ticket_builder.add_node("draft_reply", make_draft_reply_node(draft_model))

    ticket_builder.add_edge(START, "classify")
    if order_directory is not None:
        if order_agent_model is None or not getattr(shopify_client, "is_simulated", False):
            raise ValueError("Order discovery preview requires a model and an explicit simulated client.")
        from storekeeper.order_resolution import make_order_resolution_node
        ticket_builder.add_node("resolve_order_agent", make_order_resolution_node(order_agent_model, order_directory))
        ticket_builder.add_edge("classify", "resolve_order_agent")
        ticket_builder.add_conditional_edges("resolve_order_agent",
            lambda state: "escalate_ticket" if state.get("order_resolution", {}).get("status") == "failed"
            else "clarify_missing_order_reference",
            ["escalate_ticket", "clarify_missing_order_reference"])
    else:
        ticket_builder.add_edge("classify", "clarify_missing_order_reference")
    ticket_builder.add_edge("clarify_missing_order_reference", "validate_task_plan")
    ticket_builder.add_conditional_edges(
        "validate_task_plan",
        dispatch_ticket_tasks,
        ["process_task", "escalate_ticket"],
    )
    ticket_builder.add_edge("process_task", "draft_reply")
    ticket_builder.add_edge("escalate_ticket", "draft_reply")
    ticket_builder.add_edge("draft_reply", END)
    return ticket_builder.compile(checkpointer=checkpointer)

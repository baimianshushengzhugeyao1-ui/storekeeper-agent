import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from storekeeper.classify import (
    ClassifiedShippingAddress,
    ClassifiedTask,
    TicketClassification,
)
from storekeeper.domain import Intent
from storekeeper.graph.build import build_ticket_graph
from storekeeper.graph.nodes import PolicyAnswer
from test_classify import (
    FakeClassifierModel,
    make_complete_classified_shipping_address,
)
from test_shopify_writes import (
    make_cancel_success_response,
    make_order_update_success_response,
)


class FakeGraphShopifyClient:
    """Serves order lookups from a fixed list and write mutations from a queue."""

    def __init__(self, shopify_orders: list[dict], write_responses: list[dict] | None = None):
        self.shopify_orders = shopify_orders
        self.write_responses = list(write_responses or [])
        self.received_variables: dict | None = None
        self.write_calls: list[tuple[str, dict | None]] = []

    def graphql(self, query: str, variables: dict | None = None) -> dict:
        is_order_lookup = "orders(" in query
        if is_order_lookup:
            self.received_variables = variables
            assert variables is not None
            requested_order_name = variables["searchQuery"].removeprefix("name:")
            matching_orders = [
                order
                for order in self.shopify_orders
                if order["name"] == requested_order_name
            ]
            return {"orders": {"nodes": matching_orders[:1]}}
        self.write_calls.append((query, variables))
        return self.write_responses.pop(0)


class FakeDraftModel:
    """Stands in for the reply-drafting model; returns preset text."""

    def __init__(self, reply_text: str = "Drafted reply."):
        self.reply_text = reply_text
        self.received_messages: list | None = None

    def invoke(self, messages: list) -> AIMessage:
        self.received_messages = messages
        return AIMessage(content=self.reply_text)


class FakeAnswerModel:
    """Stands in for the policy-answering model; returns a preset PolicyAnswer."""

    def __init__(self, policy_answer: PolicyAnswer):
        self.policy_answer = policy_answer
        self.received_messages: list | None = None

    def with_structured_output(self, schema: type) -> "FakeAnswerModel":
        return self

    def invoke(self, messages: list) -> PolicyAnswer:
        self.received_messages = messages
        return self.policy_answer


def make_classification(
    intent: Intent = "cancel_order",
    order_reference: str | None = "#1036",
    new_shipping_address: ClassifiedShippingAddress | None = None,
) -> TicketClassification:
    return TicketClassification(
        tasks=[
            ClassifiedTask(
                intent=intent,
                order_reference=order_reference,
                new_shipping_address=new_shipping_address,
                confidence=0.95,
            )
        ]
    )


def make_live_shopify_order(
    fulfillment_status: str = "UNFULFILLED",
    processed_days_ago: int = 3,
    order_name: str = "#1036",
    order_id: str = "gid://shopify/Order/123",
) -> dict:
    processed_at = datetime.now(timezone.utc) - timedelta(days=processed_days_ago)
    return {
        "id": order_id,
        "name": order_name,
        "processedAt": processed_at.isoformat(),
        "displayFulfillmentStatus": fulfillment_status,
        "shippingAddress": {
            "firstName": "Rahat",
            "lastName": "Kabir",
            "company": None,
            "address1": "10 Old Road",
            "address2": "Unit 2",
            "city": "Austin",
            "province": "Texas",
            "zip": "78701",
            "country": "United States",
            "phone": "+1 555 0100",
        },
        "totalPriceSet": {"shopMoney": {"amount": "125.50", "currencyCode": "USD"}},
    }


def build_test_graph(
    classification: TicketClassification,
    shopify_orders: list[dict],
    write_responses: list[dict] | None = None,
    policy_answer: PolicyAnswer | None = None,
):
    fake_shopify_client = FakeGraphShopifyClient(shopify_orders, write_responses)
    fake_draft_model = FakeDraftModel()
    fake_answer_model = FakeAnswerModel(
        policy_answer or PolicyAnswer(answer="Answered from policy.", cited_documents=["warranty.md"])
    )
    ticket_graph = build_ticket_graph(
        checkpointer=InMemorySaver(),
        classifier_model=FakeClassifierModel(classification),
        draft_model=fake_draft_model,
        answer_model=fake_answer_model,
        shopify_client=fake_shopify_client,
    )
    return ticket_graph, fake_shopify_client, fake_draft_model


def make_ticket_input(ticket_text: str = "Please cancel order #1036.") -> dict:
    return {
        "ticket_text": ticket_text,
        "tasks": [],
        "task_results": [],
        "reply_draft": None,
        "ticket_outcome": None,
        "escalation_reason": None,
        "plan_conflict_reason": None,
    }


def thread_config(ticket_id: str) -> dict:
    return {"configurable": {"thread_id": ticket_id}}


class TicketGraphTests(unittest.TestCase):
    def test_denied_action_resolves_without_interrupt(self) -> None:
        ticket_graph, _, _ = build_test_graph(
            make_classification(), [make_live_shopify_order("FULFILLED")]
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("denied-1"))

        self.assertNotIn("__interrupt__", result)
        self.assertEqual(result["task_results"][0]["outcome"], "denied_by_policy")
        self.assertEqual(result["ticket_outcome"], "resolved")
        self.assertEqual(result["reply_draft"], "Drafted reply.")

    def test_eligible_action_pauses_for_approval(self) -> None:
        ticket_graph, _, _ = build_test_graph(
            make_classification(), [make_live_shopify_order("UNFULFILLED")]
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("pause-1"))

        self.assertIn("__interrupt__", result)
        pending_approval = result["__interrupt__"][0].value
        self.assertEqual(pending_approval["action"], "cancel_order")
        self.assertEqual(pending_approval["order"], "#1036")
        self.assertEqual(pending_approval["requested_reference"], "#1036")
        self.assertEqual(pending_approval["gate_rule"], "cancel_order_unfulfilled")
        self.assertEqual(
            pending_approval["gate_reason"],
            "The order is unfulfilled and can be cancelled.",
        )

    def test_approval_executes_the_real_cancel_write(self) -> None:
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(),
            [make_live_shopify_order("UNFULFILLED")],
            write_responses=[make_cancel_success_response()],
        )
        config = thread_config("approve-1")
        ticket_graph.invoke(make_ticket_input(), config)

        result = ticket_graph.invoke(Command(resume="approve"), config)

        self.assertNotIn("__interrupt__", result)
        final_task_result = result["task_results"][0]
        self.assertEqual(final_task_result["outcome"], "submitted")
        self.assertEqual(final_task_result["action_result"]["action"], "cancel_order")
        self.assertEqual(len(fake_shopify_client.write_calls), 1)
        _, cancel_variables = fake_shopify_client.write_calls[0]
        assert cancel_variables is not None
        self.assertEqual(cancel_variables["orderId"], "gid://shopify/Order/123")
        self.assertEqual(result["ticket_outcome"], "resolved")
        self.assertIn("have not been verified", result["reply_draft"])

    def test_rejection_never_reaches_shopify(self) -> None:
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(), [make_live_shopify_order("UNFULFILLED")]
        )
        config = thread_config("no-write-1")
        ticket_graph.invoke(make_ticket_input(), config)

        ticket_graph.invoke(Command(resume="reject"), config)

        self.assertEqual(fake_shopify_client.write_calls, [])

    def test_incomplete_address_change_escalates_without_shopify_lookup(self) -> None:
        incomplete_shipping_address = make_complete_classified_shipping_address().model_copy(
            update={"zip": None}
        )
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(
                intent="address_change",
                new_shipping_address=incomplete_shipping_address,
            ),
            [],
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("address-1"))

        self.assertEqual(result["ticket_outcome"], "escalated_to_human")
        self.assertIn("complete new shipping address", result["escalation_reason"])
        self.assertIsNone(fake_shopify_client.received_variables)
        self.assertEqual(fake_shopify_client.write_calls, [])

    def test_complete_address_change_pauses_with_current_and_new_addresses(self) -> None:
        ticket_graph, _, _ = build_test_graph(
            make_classification(
                intent="address_change",
                new_shipping_address=make_complete_classified_shipping_address(),
            ),
            [make_live_shopify_order("UNFULFILLED")],
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("address-pause-1"))

        pending_approval = result["__interrupt__"][0].value
        self.assertEqual(pending_approval["action"], "update_shipping_address")
        self.assertEqual(pending_approval["gate_rule"], "address_change_unfulfilled")
        self.assertEqual(
            pending_approval["current_shipping_address"]["address1"], "10 Old Road"
        )
        self.assertEqual(
            pending_approval["new_shipping_address"]["address1"], "20 Lake Road"
        )
        self.assertEqual(pending_approval["new_shipping_address"]["first_name"], "Rahat")

    def test_approved_address_change_executes_order_update(self) -> None:
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(
                intent="address_change",
                new_shipping_address=make_complete_classified_shipping_address(),
            ),
            [make_live_shopify_order("UNFULFILLED")],
            write_responses=[make_order_update_success_response()],
        )
        config = thread_config("address-approve-1")
        ticket_graph.invoke(make_ticket_input(), config)

        result = ticket_graph.invoke(Command(resume="approve"), config)

        final_task_result = result["task_results"][0]
        self.assertEqual(final_task_result["outcome"], "executed")
        self.assertEqual(
            final_task_result["action_result"]["action"], "update_shipping_address"
        )
        self.assertEqual(len(fake_shopify_client.write_calls), 1)
        _, update_variables = fake_shopify_client.write_calls[0]
        assert update_variables is not None
        self.assertEqual(update_variables["input"]["id"], "gid://shopify/Order/123")
        self.assertEqual(
            update_variables["input"]["shippingAddress"]["address1"], "20 Lake Road"
        )
        self.assertEqual(
            update_variables["input"]["shippingAddress"]["firstName"], "Rahat"
        )

    def test_fulfilled_order_address_change_is_denied_without_write(self) -> None:
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(
                intent="address_change",
                new_shipping_address=make_complete_classified_shipping_address(),
            ),
            [make_live_shopify_order("FULFILLED")],
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("address-denied-1"))

        final_task_result = result["task_results"][0]
        self.assertEqual(final_task_result["outcome"], "denied_by_policy")
        self.assertEqual(
            final_task_result["gate_verdict"]["rule"], "address_change_fulfilled"
        )
        self.assertEqual(final_task_result["policy_citations"], ["address-changes.md"])
        self.assertEqual(fake_shopify_client.write_calls, [])

    def test_rejection_records_the_human_decision(self) -> None:
        ticket_graph, _, _ = build_test_graph(
            make_classification(), [make_live_shopify_order("UNFULFILLED")]
        )
        config = thread_config("reject-1")
        ticket_graph.invoke(make_ticket_input(), config)

        result = ticket_graph.invoke(Command(resume="reject"), config)

        self.assertEqual(result["task_results"][0]["outcome"], "rejected_by_human")
        self.assertEqual(result["ticket_outcome"], "resolved")

    def test_multi_request_ticket_processes_every_task_and_drafts_one_reply(self) -> None:
        two_request_classification = TicketClassification(
            tasks=[
                ClassifiedTask(
                    intent="cancel_order",
                    order_reference="#1023",
                    new_shipping_address=None,
                    confidence=0.9,
                ),
                ClassifiedTask(
                    intent="policy_question",
                    order_reference=None,
                    new_shipping_address=None,
                    confidence=0.8,
                ),
            ]
        )
        ticket_graph, _, _ = build_test_graph(
            two_request_classification,
            [],
            policy_answer=PolicyAnswer(
                answer="Products carry a 12-month warranty.",
                cited_documents=["warranty.md"],
            ),
        )

        with patch(
            "storekeeper.policy_docs.search_policy_chunks",
            return_value=[
                {
                    "chunk_id": "warranty.md#coverage",
                    "document_name": "warranty.md",
                    "heading": "Coverage",
                    "document_text": "Products carry a 12-month warranty.",
                    "distance": 0.1,
                }
            ],
        ):
            result = ticket_graph.invoke(make_ticket_input(), thread_config("multi-1"))

        self.assertEqual(result["ticket_outcome"], "escalated_to_human")
        self.assertEqual(
            [task_result["outcome"] for task_result in result["task_results"]],
            ["failed", "answered"],
        )
        self.assertIsNotNone(result["reply_draft"])

    def test_independent_write_tasks_pause_together_and_resume_by_interrupt_id(self) -> None:
        two_write_classification = TicketClassification(
            tasks=[
                ClassifiedTask(
                    intent="cancel_order",
                    order_reference="#1036",
                    new_shipping_address=None,
                    confidence=0.95,
                ),
                ClassifiedTask(
                    intent="refund_request",
                    order_reference="#1037",
                    new_shipping_address=None,
                    confidence=0.94,
                ),
            ]
        )
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            two_write_classification,
            [
                make_live_shopify_order(order_name="#1036"),
                make_live_shopify_order(
                    order_name="#1037",
                    order_id="gid://shopify/Order/124",
                ),
            ],
        )
        config = thread_config("parallel-approvals-1")

        paused_result = ticket_graph.invoke(make_ticket_input(), config)

        self.assertEqual(len(paused_result["__interrupt__"]), 2)
        interrupt_ids_by_task_id = {
            pending_interrupt.value["task_id"]: pending_interrupt.id
            for pending_interrupt in paused_result["__interrupt__"]
        }
        partially_resumed_result = ticket_graph.invoke(
            Command(
                resume={interrupt_ids_by_task_id["task-1"]: "reject"}
            ),
            config,
        )
        self.assertEqual(len(partially_resumed_result["__interrupt__"]), 1)
        self.assertEqual(
            partially_resumed_result["__interrupt__"][0].value["task_id"],
            "task-2",
        )

        final_result = ticket_graph.invoke(
            Command(
                resume={interrupt_ids_by_task_id["task-2"]: "reject"}
            ),
            config,
        )

        self.assertNotIn("__interrupt__", final_result)
        self.assertEqual(
            [task_result["task_id"] for task_result in final_result["task_results"]],
            ["task-1", "task-2"],
        )
        self.assertEqual(
            [task_result["outcome"] for task_result in final_result["task_results"]],
            ["rejected_by_human", "rejected_by_human"],
        )
        self.assertEqual(fake_shopify_client.write_calls, [])
        self.assertEqual(final_result["reply_draft"], "Drafted reply.")

    def test_parallel_plan_conflict_escalates_same_order_writes(self) -> None:
        conflicting_classification = TicketClassification(
            tasks=[
                ClassifiedTask(
                    intent="cancel_order",
                    order_reference="#1036",
                    new_shipping_address=None,
                    confidence=0.95,
                ),
                ClassifiedTask(
                    intent="refund_request",
                    order_reference="1036",
                    new_shipping_address=None,
                    confidence=0.94,
                ),
            ]
        )
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            conflicting_classification,
            [make_live_shopify_order()],
        )

        result = ticket_graph.invoke(
            make_ticket_input(),
            thread_config("parallel-conflict-1"),
        )

        self.assertNotIn("__interrupt__", result)
        self.assertEqual(result["ticket_outcome"], "escalated_to_human")
        self.assertIn("same order (#1036)", result["escalation_reason"])
        self.assertEqual(fake_shopify_client.write_calls, [])
        self.assertEqual(result["reply_draft"], "Drafted reply.")

    def test_policy_question_is_answered_with_citations(self) -> None:
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(intent="policy_question", order_reference=None),
            [],
            policy_answer=PolicyAnswer(
                answer="Products carry a 12-month warranty.",
                cited_documents=["warranty.md"],
            ),
        )

        with patch(
            "storekeeper.policy_docs.search_policy_chunks",
            return_value=[
                {
                    "chunk_id": "warranty.md#coverage",
                    "document_name": "warranty.md",
                    "heading": "Coverage",
                    "document_text": "# Warranty\n\n## Coverage\n\nProducts carry a 12-month warranty.",
                    "distance": 0.1,
                }
            ],
        ) as mock_search_policy_chunks:
            result = ticket_graph.invoke(
                make_ticket_input("How long is your warranty?"),
                thread_config("policy-1"),
            )

        self.assertNotIn("__interrupt__", result)
        answered_task_result = result["task_results"][0]
        self.assertEqual(answered_task_result["outcome"], "answered")
        self.assertEqual(
            answered_task_result["action_result"]["answer"],
            "Products carry a 12-month warranty.",
        )
        self.assertEqual(answered_task_result["policy_citations"], ["warranty.md"])
        self.assertEqual(result["ticket_outcome"], "resolved")
        self.assertEqual(result["reply_draft"], "Drafted reply.")
        self.assertEqual(fake_shopify_client.write_calls, [])
        mock_search_policy_chunks.assert_called_once_with("How long is your warranty?")

    def test_hallucinated_citations_are_filtered_out(self) -> None:
        ticket_graph, _, _ = build_test_graph(
            make_classification(intent="policy_question", order_reference=None),
            [],
            policy_answer=PolicyAnswer(
                answer="An answer.", cited_documents=["warranty.md", "invented-policy.md"]
            ),
        )

        with patch(
            "storekeeper.policy_docs.search_policy_chunks",
            return_value=[
                {
                    "chunk_id": "warranty.md#coverage",
                    "document_name": "warranty.md",
                    "heading": "Coverage",
                    "document_text": "# Warranty\n\n## Coverage\n\nProducts carry a 12-month warranty.",
                    "distance": 0.1,
                }
            ],
        ):
            result = ticket_graph.invoke(make_ticket_input(), thread_config("policy-2"))

        self.assertEqual(result["task_results"][0]["policy_citations"], ["warranty.md"])

    def test_other_intent_escalates(self) -> None:
        ticket_graph, _, _ = build_test_graph(
            make_classification(intent="other", order_reference=None), []
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("other-1"))

        self.assertEqual(result["ticket_outcome"], "escalated_to_human")
        self.assertIn("other", result["escalation_reason"])

    def test_denied_action_cites_the_relevant_policy(self) -> None:
        ticket_graph, _, _ = build_test_graph(
            make_classification(), [make_live_shopify_order("FULFILLED")]
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("denied-cite-1"))

        denied_task_result = result["task_results"][0]
        self.assertEqual(denied_task_result["outcome"], "denied_by_policy")
        self.assertEqual(denied_task_result["policy_citations"], ["cancellations.md"])

    def test_unknown_order_fails_softly_with_a_reply(self) -> None:
        ticket_graph, _, _ = build_test_graph(make_classification(), [])

        result = ticket_graph.invoke(make_ticket_input(), thread_config("missing-1"))

        self.assertEqual(result["task_results"][0]["outcome"], "failed")
        self.assertEqual(result["ticket_outcome"], "escalated_to_human")
        self.assertEqual(result["reply_draft"], "Drafted reply.")

    def test_hostile_order_reference_fails_softly_without_any_lookup(self) -> None:
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(order_reference="1001 OR status:any"), []
        )

        result = ticket_graph.invoke(make_ticket_input(), thread_config("hostile-1"))

        self.assertNotIn("__interrupt__", result)
        self.assertEqual(result["task_results"][0]["outcome"], "failed")
        self.assertEqual(result["ticket_outcome"], "escalated_to_human")
        # The hostile reference never reached Shopify: no lookup, no writes.
        self.assertIsNone(fake_shopify_client.received_variables)
        self.assertEqual(fake_shopify_client.write_calls, [])

    def test_bare_order_reference_is_normalized_before_lookup(self) -> None:
        ticket_graph, fake_shopify_client, _ = build_test_graph(
            make_classification(order_reference="1036"),
            [make_live_shopify_order("FULFILLED")],
        )

        ticket_graph.invoke(make_ticket_input(), thread_config("bare-ref-1"))

        self.assertEqual(
            fake_shopify_client.received_variables, {"searchQuery": "name:#1036"}
        )


if __name__ == "__main__":
    unittest.main()

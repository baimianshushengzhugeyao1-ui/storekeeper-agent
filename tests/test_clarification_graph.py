import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from storekeeper.classify import TicketClassification
from storekeeper.graph.build import build_ticket_graph
from storekeeper.graph.clarification import normalize_clarification_order_reference
from storekeeper.tickets import get_ticket_status
from test_classify import FakeClassifierModel
from test_graph import (
    FakeDraftModel,
    FakeGraphShopifyClient,
    make_classification,
    make_live_shopify_order,
    make_ticket_input,
    thread_config,
)
from test_shopify_writes import make_cancel_success_response


class CountingClassifierModel(FakeClassifierModel):
    def __init__(self, classification: TicketClassification):
        super().__init__(classification)
        self.invocation_count = 0

    def invoke(self, messages: list) -> TicketClassification:
        self.invocation_count += 1
        return super().invoke(messages)


class ClarificationOrderReferenceTests(unittest.TestCase):
    def test_normalizes_one_ascii_order_number(self) -> None:
        for raw_reference, normalized_reference in [
            ("#1036", "#1036"),
            (" 1036 ", "#1036"),
            ("#0001", "#0001"),
            ("1", "#1"),
            ("1234567890", "#1234567890"),
        ]:
            with self.subTest(raw_reference=raw_reference):
                self.assertEqual(
                    normalize_clarification_order_reference(raw_reference),
                    normalized_reference,
                )

    def test_rejects_ambiguous_hostile_and_non_ascii_references(self) -> None:
        for invalid_reference in [
            "", "#", "1036 OR status:any", "#1036 #1037", "#1036\n#1037",
            "12345678901", "-1036", "##1036", "１０３６", "١٠٣٦", None, 1036,
        ]:
            with self.subTest(invalid_reference=invalid_reference):
                with self.assertRaises(ValueError):
                    normalize_clarification_order_reference(invalid_reference)


class ClarificationGraphTests(unittest.TestCase):
    def make_graph(self, checkpointer=None, classification=None, orders=None):
        classifier_model = CountingClassifierModel(
            classification or make_classification(order_reference=None)
        )
        shopify_client = FakeGraphShopifyClient(
            [make_live_shopify_order()] if orders is None else orders,
            [make_cancel_success_response()],
        )
        draft_model = FakeDraftModel()
        ticket_graph = build_ticket_graph(
            checkpointer=checkpointer if checkpointer is not None else InMemorySaver(),
            classifier_model=classifier_model,
            draft_model=draft_model,
            answer_model=draft_model,
            shopify_client=shopify_client,
        )
        return ticket_graph, classifier_model, shopify_client, draft_model

    def test_missing_order_pauses_before_lookup_and_reply_drafting(self) -> None:
        ticket_graph, classifier_model, shopify_client, draft_model = self.make_graph()
        config = thread_config("clarify-missing")

        result = ticket_graph.invoke(make_ticket_input("Please cancel my order."), config)

        self.assertEqual(get_ticket_status("clarify-missing", ticket_graph), "waiting_for_customer")
        self.assertEqual(len(result["__interrupt__"]), 1)
        self.assertEqual(
            result["__interrupt__"][0].value,
            {
                "kind": "clarification",
                "task_id": "task-1",
                "field": "order_reference",
                "question": "What is the order number for the order you want to cancel?",
            },
        )
        self.assertEqual(classifier_model.invocation_count, 1)
        self.assertIsNone(shopify_client.received_variables)
        self.assertEqual(shopify_client.write_calls, [])
        self.assertIsNone(draft_model.received_messages)
        self.assertEqual(result["task_results"], [])

    def test_resume_preserves_task_and_still_requires_human_approval(self) -> None:
        ticket_graph, classifier_model, shopify_client, _ = self.make_graph()
        config = thread_config("clarify-approve")
        paused_result = ticket_graph.invoke(make_ticket_input("Please cancel my order."), config)
        original_task = dict(paused_result["tasks"][0])
        clarification_id = paused_result["__interrupt__"][0].id

        approval_result = ticket_graph.invoke(
            Command(resume={clarification_id: {"order_reference": " 1036 "}}), config
        )

        self.assertEqual(classifier_model.invocation_count, 1)
        self.assertEqual(approval_result["ticket_text"], "Please cancel my order.")
        self.assertEqual(
            approval_result["tasks"], [{**original_task, "order_reference": "#1036"}]
        )
        self.assertIsNone(paused_result["tasks"][0]["order_reference"])
        self.assertEqual(
            approval_result["clarification_history"],
            [{"task_id": "task-1", "field": "order_reference", "value": "#1036"}],
        )
        self.assertEqual(approval_result["task_results"], [])
        self.assertEqual(get_ticket_status("clarify-approve", ticket_graph), "pending_approval")
        self.assertEqual(shopify_client.received_variables, {"searchQuery": "name:#1036"})
        self.assertEqual(shopify_client.write_calls, [])
        approval_interrupt = approval_result["__interrupt__"][0]
        self.assertEqual(approval_interrupt.value["action"], "cancel_order")
        self.assertEqual(approval_interrupt.value["requested_reference"], "#1036")

        final_result = ticket_graph.invoke(
            Command(resume={approval_interrupt.id: "approve"}), config
        )

        self.assertEqual(len(shopify_client.write_calls), 1)
        self.assertEqual(len(final_result["task_results"]), 1)
        self.assertEqual(final_result["task_results"][0]["outcome"], "submitted")
        self.assertEqual(final_result["clarification_history"], approval_result["clarification_history"])
        self.assertEqual(classifier_model.invocation_count, 1)

    def test_graph_revalidates_direct_resume_before_any_lookup(self) -> None:
        for invalid_response in [
            "approve", {}, {"order_reference": None}, {"order_reference": "#1036 OR status:any"},
            {"order_reference": "#1036", "requested_action": "issue_refund"},
        ]:
            with self.subTest(invalid_response=invalid_response):
                ticket_graph, classifier_model, shopify_client, _ = self.make_graph()
                config = thread_config("clarify-invalid")
                paused_result = ticket_graph.invoke(make_ticket_input(), config)

                with self.assertRaises(ValueError):
                    ticket_graph.invoke(
                        Command(resume={paused_result["__interrupt__"][0].id: invalid_response}),
                        config,
                    )

                saved_values = ticket_graph.get_state(config).values
                self.assertIsNone(saved_values["tasks"][0]["order_reference"])
                self.assertEqual(saved_values.get("clarification_history", []), [])
                self.assertEqual(saved_values["task_results"], [])
                self.assertEqual(classifier_model.invocation_count, 1)
                self.assertIsNone(shopify_client.received_variables)
                self.assertEqual(shopify_client.write_calls, [])

    def test_clarified_order_still_passes_through_policy_gate(self) -> None:
        ticket_graph, _, shopify_client, _ = self.make_graph(
            orders=[make_live_shopify_order("FULFILLED")]
        )
        config = thread_config("clarify-denied")
        paused_result = ticket_graph.invoke(make_ticket_input(), config)

        result = ticket_graph.invoke(
            Command(resume={paused_result["__interrupt__"][0].id: {"order_reference": "#1036"}}),
            config,
        )

        self.assertNotIn("__interrupt__", result)
        self.assertEqual(result["task_results"][0]["outcome"], "denied_by_policy")
        self.assertEqual(shopify_client.write_calls, [])

    def test_other_missing_order_requests_keep_existing_escalation(self) -> None:
        for intent in ["refund_request", "address_change", "other"]:
            with self.subTest(intent=intent):
                ticket_graph, _, shopify_client, _ = self.make_graph(
                    classification=make_classification(intent=intent, order_reference=None)
                )

                result = ticket_graph.invoke(make_ticket_input(), thread_config(f"clarify-{intent}"))

                self.assertNotIn("__interrupt__", result)
                self.assertEqual(result["ticket_outcome"], "escalated_to_human")
                self.assertIsNone(shopify_client.received_variables)
                self.assertEqual(shopify_client.write_calls, [])

    def test_multiple_missing_order_cancellations_do_not_enter_clarification(self) -> None:
        classification = make_classification(order_reference=None)
        classification.tasks.append(classification.tasks[0].model_copy())
        ticket_graph, _, shopify_client, _ = self.make_graph(classification=classification)

        result = ticket_graph.invoke(make_ticket_input(), thread_config("clarify-multiple"))

        self.assertNotIn("__interrupt__", result)
        self.assertEqual(len(result["task_results"]), 2)
        self.assertEqual(result["ticket_outcome"], "escalated_to_human")
        self.assertIsNone(shopify_client.received_variables)
        self.assertEqual(shopify_client.write_calls, [])

    def test_sqlite_restart_preserves_clarification_then_approval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            checkpoint_path = str(Path(temporary_directory) / "checkpoints.sqlite")
            config = thread_config("clarify-restart")
            with SqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
                ticket_graph, first_classifier, first_client, _ = self.make_graph(checkpointer)
                paused_result = ticket_graph.invoke(make_ticket_input("Please cancel my order."), config)
                clarification_id = paused_result["__interrupt__"][0].id
                self.assertEqual(first_classifier.invocation_count, 1)
                self.assertIsNone(first_client.received_variables)

            with SqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
                ticket_graph, resumed_classifier, resumed_client, _ = self.make_graph(checkpointer)
                self.assertEqual(get_ticket_status("clarify-restart", ticket_graph), "waiting_for_customer")
                self.assertEqual(ticket_graph.get_state(config).interrupts[0].id, clarification_id)

                approval_result = ticket_graph.invoke(
                    Command(resume={clarification_id: {"order_reference": "#1036"}}), config
                )
                approval_id = approval_result["__interrupt__"][0].id
                self.assertEqual(resumed_classifier.invocation_count, 0)
                self.assertEqual(resumed_client.write_calls, [])

            with SqliteSaver.from_conn_string(checkpoint_path) as checkpointer:
                ticket_graph, final_classifier, final_client, _ = self.make_graph(checkpointer)
                self.assertEqual(get_ticket_status("clarify-restart", ticket_graph), "pending_approval")

                final_result = ticket_graph.invoke(Command(resume={approval_id: "approve"}), config)

                self.assertEqual(final_classifier.invocation_count, 0)
                self.assertEqual(len(final_client.write_calls), 1)
                self.assertEqual(len(final_result["task_results"]), 1)
                self.assertEqual(
                    final_result["clarification_history"],
                    [{"task_id": "task-1", "field": "order_reference", "value": "#1036"}],
                )
                self.assertEqual(get_ticket_status("clarify-restart", ticket_graph), "resolved")


if __name__ == "__main__":
    unittest.main()

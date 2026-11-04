import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite import SqliteSaver

from storekeeper.api.app import create_app
from storekeeper.graph.build import build_ticket_graph
from test_classify import FakeClassifierModel
from test_graph import (
    FakeDraftModel,
    FakeGraphShopifyClient,
    make_classification,
    make_live_shopify_order,
    thread_config,
)
from test_shopify_writes import make_cancel_success_response


class ClarificationApiTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_directory_path = Path(temporary_directory.name)
        self.checkpoint_database_path = self.temporary_directory_path / "checkpoints.sqlite"
        self.ticket_database_path = self.temporary_directory_path / "tickets.sqlite"
        self.classifier_model = FakeClassifierModel(
            make_classification(order_reference=None)
        )
        self.classify_calls = self.enterContext(
            patch.object(
                self.classifier_model,
                "invoke",
                wraps=self.classifier_model.invoke,
            )
        )
        self.shopify_client = FakeGraphShopifyClient(
            [make_live_shopify_order()],
            [make_cancel_success_response()],
        )
        self.original_ticket_text = "Please cancel my order."

    @contextmanager
    def open_client(self):
        # Each context opens a fresh graph and SQLite connection, as a restart does.
        with SqliteSaver.from_conn_string(str(self.checkpoint_database_path)) as checkpointer:
            ticket_graph = build_ticket_graph(
                checkpointer=checkpointer,
                classifier_model=self.classifier_model,
                draft_model=FakeDraftModel(),
                shopify_client=self.shopify_client,
            )
            application = create_app(
                ticket_graph=ticket_graph,
                ticket_database_path=self.ticket_database_path,
                frontend_dist_path=self.temporary_directory_path / "missing-dist",
            )
            with TestClient(application) as client:
                yield client, ticket_graph

    def create_waiting_ticket(self, client, ticket_id: str = "CLARIFY-1") -> dict:
        response = client.post(
            "/api/tickets",
            json={
                "ticket_id": ticket_id,
                "ticket_text": self.original_ticket_text,
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        ticket = response.json()
        self.assertEqual(ticket["status"], "waiting_for_customer")
        self.assertEqual(len(ticket["pending_clarifications"]), 1)
        return ticket

    def supplement(self, client, ticket: dict, order_reference: str = " 1036 "):
        return client.post(
            f"/api/tickets/{ticket['ticket_id']}/clarification",
            json={
                "interrupt_id": ticket["pending_clarifications"][0]["interrupt_id"],
                "order_reference": order_reference,
            },
        )

    def test_missing_order_exposes_customer_question_without_lookup_or_approval(self) -> None:
        with self.open_client() as (client, ticket_graph):
            ticket = self.create_waiting_ticket(client)
            clarification = ticket["pending_clarifications"][0]

            self.assertEqual(
                set(clarification),
                {"interrupt_id", "kind", "task_id", "field", "question", "choices", "resolver"},
            )
            self.assertEqual(clarification["kind"], "clarification")
            self.assertEqual(clarification["task_id"], "task-1")
            self.assertEqual(clarification["field"], "order_reference")
            self.assertTrue(clarification["question"].strip())
            self.assertEqual(ticket["pending_approvals"], [])
            self.assertEqual(ticket["clarification_history"], [])
            self.assertEqual(ticket["task_results"], [])
            self.assertIsNone(ticket["reply_draft"])
            snapshot = ticket_graph.get_state(thread_config(ticket["ticket_id"]))
            self.assertEqual(snapshot.interrupts[0].value["kind"], "clarification")
            self.assertEqual(snapshot.values["ticket_text"], self.original_ticket_text)
            self.assertEqual(client.get("/api/tickets/CLARIFY-1").json(), ticket)
            self.assertEqual(
                client.get("/api/tickets").json()[0]["status"],
                "waiting_for_customer",
            )

        self.assertEqual(self.classify_calls.call_count, 1)
        self.assertIsNone(self.shopify_client.received_variables)
        self.assertEqual(self.shopify_client.write_calls, [])

    def test_invalid_answer_is_rejected_before_resume_and_preserves_question(self) -> None:
        invalid_references = [
            "", "   ", "#", "##1036", "1036 and 1037", "#1036 OR 1=1",
            "１２３４", "١٢٣٤", "12345678901", "10.36", "-1036", 1036, None,
        ]
        with self.open_client() as (client, ticket_graph):
            ticket = self.create_waiting_ticket(client)
            interrupt_id = ticket["pending_clarifications"][0]["interrupt_id"]
            invalid_bodies = [
                {"interrupt_id": interrupt_id, "order_reference": reference}
                for reference in invalid_references
            ]
            invalid_bodies.extend([
                {"interrupt_id": interrupt_id},
                {"interrupt_id": " ", "order_reference": "1036"},
                {"interrupt_id": interrupt_id, "order_reference": "1036", "action": "issue_refund"},
                {"interrupt_id": interrupt_id, "order_reference": "1036", "ticket_text": "Refund instead."},
            ])
            with patch.object(ticket_graph, "invoke", wraps=ticket_graph.invoke) as invoke_calls:
                for request_body in invalid_bodies:
                    with self.subTest(request_body=request_body):
                        response = client.post(
                            "/api/tickets/CLARIFY-1/clarification",
                            json=request_body,
                        )
                        self.assertEqual(response.status_code, 422, response.text)
                        self.assertEqual(client.get("/api/tickets/CLARIFY-1").json(), ticket)
                invoke_calls.assert_not_called()

        self.assertEqual(self.classify_calls.call_count, 1)
        self.assertIsNone(self.shopify_client.received_variables)
        self.assertEqual(self.shopify_client.write_calls, [])

    def test_answer_preserves_request_and_requires_separate_approval_before_write(self) -> None:
        with self.open_client() as (client, ticket_graph):
            waiting_ticket = self.create_waiting_ticket(client)
            original_task = waiting_ticket["tasks"][0]
            response = self.supplement(client, waiting_ticket)

            self.assertEqual(response.status_code, 200, response.text)
            approval_ticket = response.json()
            self.assertEqual(approval_ticket["status"], "pending_approval")
            self.assertEqual(approval_ticket["ticket_text"], self.original_ticket_text)
            self.assertEqual(
                approval_ticket["tasks"][0],
                {**original_task, "order_reference": "#1036"},
            )
            self.assertEqual(approval_ticket["pending_clarifications"], [])
            self.assertEqual(approval_ticket["task_results"], [])
            self.assertEqual(approval_ticket["clarification_history"], [
                {"task_id": "task-1", "field": "order_reference", "value": "#1036"}
            ])
            approval = approval_ticket["pending_approvals"][0]
            self.assertEqual(approval["action"], "cancel_order")
            self.assertEqual(approval["requested_reference"], "#1036")
            self.assertEqual(approval["order"], "#1036")
            self.assertEqual(approval["gate_rule"], "cancel_order_unfulfilled")
            self.assertEqual(self.classify_calls.call_count, 1)
            self.assertEqual(self.shopify_client.write_calls, [])
            self.assertEqual(
                self.shopify_client.received_variables,
                {"searchQuery": "name:#1036"},
            )
            self.assertEqual(
                ticket_graph.get_state(thread_config("CLARIFY-1")).values["ticket_text"],
                self.original_ticket_text,
            )

            decision = {"interrupt_id": approval["interrupt_id"], "decision": "approve"}
            resolved_response = client.post("/api/tickets/CLARIFY-1/decision", json=decision)
            self.assertEqual(resolved_response.status_code, 200, resolved_response.text)
            resolved_ticket = resolved_response.json()
            self.assertEqual(resolved_ticket["status"], "resolved")
            self.assertEqual(resolved_ticket["task_results"][0]["outcome"], "submitted")
            self.assertEqual(resolved_ticket["pending_approvals"], [])
            self.assertEqual(resolved_ticket["pending_clarifications"], [])
            self.assertEqual(resolved_ticket["clarification_history"], approval_ticket["clarification_history"])
            self.assertEqual(client.post("/api/tickets/CLARIFY-1/decision", json=decision).status_code, 409)
            self.assertEqual(self.supplement(client, waiting_ticket).status_code, 409)

        self.assertEqual(self.classify_calls.call_count, 1)
        self.assertEqual(len(self.shopify_client.write_calls), 1)
        self.assertEqual(self.shopify_client.write_calls[0][1]["orderId"], "gid://shopify/Order/123")

    def test_routes_reject_wrong_interrupt_type_cross_ticket_and_replayed_answers(self) -> None:
        with self.open_client() as (client, ticket_graph):
            first_ticket = self.create_waiting_ticket(client, "FIRST")
            second_ticket = self.create_waiting_ticket(client, "SECOND")
            first_interrupt = first_ticket["pending_clarifications"][0]["interrupt_id"]
            second_interrupt = second_ticket["pending_clarifications"][0]["interrupt_id"]
            with patch.object(ticket_graph, "invoke", wraps=ticket_graph.invoke) as invoke_calls:
                approval_on_question = client.post(
                    "/api/tickets/FIRST/decision",
                    json={"interrupt_id": first_interrupt, "decision": "approve"},
                )
                other_ticket_answer = client.post(
                    "/api/tickets/FIRST/clarification",
                    json={"interrupt_id": second_interrupt, "order_reference": "1036"},
                )
                unknown_interrupt_answer = client.post(
                    "/api/tickets/FIRST/clarification",
                    json={"interrupt_id": "not-pending", "order_reference": "1036"},
                )
                self.assertEqual(approval_on_question.status_code, 409)
                self.assertEqual(other_ticket_answer.status_code, 409)
                self.assertEqual(unknown_interrupt_answer.status_code, 409)
                invoke_calls.assert_not_called()
            self.assertEqual(client.get("/api/tickets/FIRST").json(), first_ticket)
            self.assertEqual(client.get("/api/tickets/SECOND").json(), second_ticket)

            answered_response = self.supplement(client, first_ticket)
            self.assertEqual(answered_response.status_code, 200, answered_response.text)
            approval_ticket = answered_response.json()
            approval_interrupt = approval_ticket["pending_approvals"][0]["interrupt_id"]
            with patch.object(ticket_graph, "invoke", wraps=ticket_graph.invoke) as invoke_calls:
                self.assertEqual(self.supplement(client, first_ticket).status_code, 409)
                self.assertEqual(client.post(
                    "/api/tickets/FIRST/clarification",
                    json={"interrupt_id": approval_interrupt, "order_reference": "1036"},
                ).status_code, 409)
                self.assertEqual(client.post(
                    "/api/tickets/FIRST/decision",
                    json={"interrupt_id": first_interrupt, "decision": "approve"},
                ).status_code, 409)
                invoke_calls.assert_not_called()
            self.assertEqual(client.get("/api/tickets/FIRST").json(), approval_ticket)

        self.assertEqual(self.shopify_client.write_calls, [])

    def test_unknown_ticket_clarification_returns_not_found(self) -> None:
        with self.open_client() as (client, ticket_graph):
            with patch.object(ticket_graph, "invoke", wraps=ticket_graph.invoke) as invoke_calls:
                response = client.post(
                    "/api/tickets/UNKNOWN/clarification",
                    json={"interrupt_id": "missing", "order_reference": "1036"},
                )
                self.assertEqual(response.status_code, 404)
                invoke_calls.assert_not_called()

    def test_short_and_maximum_length_references_are_normalized(self) -> None:
        self.shopify_client.shopify_orders = [
            make_live_shopify_order(order_name="#0"),
            make_live_shopify_order(order_name="#1234567890"),
        ]
        with self.open_client() as (client, _):
            for ticket_id, answer, expected_reference in [
                ("ONE-DIGIT", " 0 ", "#0"),
                ("TEN-DIGITS", " #1234567890 ", "#1234567890"),
            ]:
                with self.subTest(answer=answer):
                    ticket = self.create_waiting_ticket(client, ticket_id)
                    response = self.supplement(client, ticket, answer)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "pending_approval")
                    self.assertEqual(response.json()["tasks"][0]["order_reference"], expected_reference)
                    self.assertEqual(response.json()["clarification_history"][0]["value"], expected_reference)

    def test_supplement_still_obeys_policy_gate(self) -> None:
        self.shopify_client.shopify_orders = [make_live_shopify_order("FULFILLED")]
        with patch("storekeeper.graph.nodes.find_policy_context", return_value=[]):
            with self.open_client() as (client, _):
                ticket = self.create_waiting_ticket(client)
                response = self.supplement(client, ticket)
                self.assertEqual(response.status_code, 200, response.text)
                denied_ticket = response.json()
                self.assertEqual(denied_ticket["status"], "resolved")
                self.assertEqual(denied_ticket["pending_approvals"], [])
                self.assertEqual(denied_ticket["pending_clarifications"], [])
                self.assertEqual(denied_ticket["task_results"][0]["outcome"], "denied_by_policy")
                self.assertFalse(denied_ticket["task_results"][0]["gate_verdict"]["passed"])
                self.assertEqual(len(denied_ticket["clarification_history"]), 1)
        self.assertEqual(self.shopify_client.write_calls, [])

    def test_customer_question_and_answer_history_survive_sqlite_restarts(self) -> None:
        with self.open_client() as (client, _):
            waiting_ticket = self.create_waiting_ticket(client)

        with self.open_client() as (client, _):
            self.assertEqual(client.get("/api/tickets/CLARIFY-1").json(), waiting_ticket)
            response = self.supplement(client, waiting_ticket)
            self.assertEqual(response.status_code, 200, response.text)
            approval_ticket = response.json()

        with self.open_client() as (client, _):
            self.assertEqual(client.get("/api/tickets/CLARIFY-1").json(), approval_ticket)
            self.assertEqual(self.supplement(client, waiting_ticket).status_code, 409)
            approval = approval_ticket["pending_approvals"][0]
            response = client.post(
                "/api/tickets/CLARIFY-1/decision",
                json={"interrupt_id": approval["interrupt_id"], "decision": "approve"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            resolved_ticket = response.json()

        with self.open_client() as (client, _):
            self.assertEqual(client.get("/api/tickets/CLARIFY-1").json(), resolved_ticket)
            self.assertEqual(resolved_ticket["status"], "resolved")
            self.assertEqual(resolved_ticket["clarification_history"], [
                {"task_id": "task-1", "field": "order_reference", "value": "#1036"}
            ])
        self.assertEqual(self.classify_calls.call_count, 1)
        self.assertEqual(len(self.shopify_client.write_calls), 1)

    def test_two_concurrent_answers_accept_only_one_resume(self) -> None:
        with self.open_client() as (client, _):
            ticket = self.create_waiting_ticket(client)
            simultaneous_requests = Barrier(2)

            def submit_answer():
                simultaneous_requests.wait(timeout=10)
                return self.supplement(client, ticket)

            with ThreadPoolExecutor(max_workers=2) as executor:
                submissions = [executor.submit(submit_answer) for _ in range(2)]
                responses = [submission.result(timeout=30) for submission in submissions]
            self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
            current_ticket = client.get("/api/tickets/CLARIFY-1").json()
            self.assertEqual(current_ticket["status"], "pending_approval")
            self.assertEqual(len(current_ticket["clarification_history"]), 1)
            self.assertEqual(len(current_ticket["pending_approvals"]), 1)
            self.assertEqual(current_ticket["pending_clarifications"], [])
        self.assertEqual(self.classify_calls.call_count, 1)
        self.assertEqual(self.shopify_client.write_calls, [])


if __name__ == "__main__":
    unittest.main()

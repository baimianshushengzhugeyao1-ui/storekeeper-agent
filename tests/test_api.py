import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langgraph.types import Command

from storekeeper.api.app import create_app


def make_task() -> dict:
    return {
        "task_id": "task-1",
        "intent": "cancel_order",
        "order_reference": "#1002",
        "requested_action": "cancel_order",
        "new_shipping_address": None,
        "confidence": 0.98,
    }


def make_approval_payload() -> dict:
    return {
        "task_id": "task-1",
        "question": "Approve this action?",
        "action": "cancel_order",
        "order": "#1002",
        "requested_reference": "#1002",
        "amount": "38.50 USD",
        "gate_rule": "cancel_order_unfulfilled",
        "gate_reason": "The order is unfulfilled and can be cancelled.",
        "flags": [],
        "current_shipping_address": None,
        "new_shipping_address": None,
    }


class StubTicketGraph:
    def __init__(self):
        self.state_snapshots: dict[str, SimpleNamespace] = {}
        self.received_resume_decisions: list[dict[str, str]] = []

    def get_state(self, config: dict) -> SimpleNamespace:
        ticket_id = config["configurable"]["thread_id"]
        return self.state_snapshots.get(
            ticket_id,
            SimpleNamespace(values={}, interrupts=()),
        )

    def invoke(self, graph_input: dict | Command, config: dict) -> dict:
        ticket_id = config["configurable"]["thread_id"]
        if isinstance(graph_input, Command):
            self.received_resume_decisions.append(graph_input.resume)
            decision = next(iter(graph_input.resume.values()))
            task = make_task()
            task_result = {
                "task_id": "task-1",
                "task": task,
                "outcome": (
                    "executed" if decision == "approve" else "rejected_by_human"
                ),
                "gate_verdict": {
                    "passed": True,
                    "rule": "cancel_order_unfulfilled",
                    "reason": "The order is unfulfilled and can be cancelled.",
                    "flags": [],
                },
                "action_result": (
                    {
                        "action": "cancel_order",
                        "summary": "Shopify accepted the cancellation.",
                    }
                    if decision == "approve"
                    else None
                ),
                "policy_citations": [],
            }
            state_values = {
                "ticket_text": "Please cancel order #1002.",
                "tasks": [task],
                "task_results": [task_result],
                "reply_draft": "Order #1002 has been cancelled.",
                "ticket_outcome": "resolved",
                "escalation_reason": None,
            }
            self.state_snapshots[ticket_id] = SimpleNamespace(
                values=state_values,
                interrupts=(),
            )
            return state_values

        task = make_task()
        state_values = {
            **graph_input,
            "tasks": [task],
        }
        approval_interrupt = SimpleNamespace(
            id="interrupt-task-1",
            value=make_approval_payload(),
        )
        self.state_snapshots[ticket_id] = SimpleNamespace(
            values=state_values,
            interrupts=(approval_interrupt,),
        )
        return {**state_values, "__interrupt__": (approval_interrupt,)}


class TicketApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.ticket_database_path = (
            Path(self.temporary_directory.name) / "tickets.sqlite"
        )
        self.ticket_graph = StubTicketGraph()
        application = create_app(
            ticket_graph=self.ticket_graph,
            ticket_database_path=self.ticket_database_path,
            frontend_dist_path=Path(self.temporary_directory.name) / "missing-dist",
        )
        self.test_client_context = TestClient(application)
        self.client = self.test_client_context.__enter__()

    def tearDown(self) -> None:
        self.test_client_context.__exit__(None, None, None)
        self.temporary_directory.cleanup()

    def test_create_list_detail_and_decision_happy_path(self) -> None:
        create_response = self.client.post(
            "/api/tickets",
            json={
                "ticket_id": "TICKET-API-1",
                "ticket_text": "Please cancel order #1002.",
            },
        )

        self.assertEqual(create_response.status_code, 201)
        created_ticket = create_response.json()
        self.assertEqual(created_ticket["status"], "pending_approval")
        self.assertEqual(
            created_ticket["pending_approvals"][0]["gate_reason"],
            "The order is unfulfilled and can be cancelled.",
        )

        list_response = self.client.get("/api/tickets")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()[0]["ticket_id"], "TICKET-API-1")
        self.assertEqual(list_response.json()[0]["status"], "pending_approval")

        detail_response = self.client.get("/api/tickets/TICKET-API-1")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(
            detail_response.json()["pending_approvals"][0]["order"],
            "#1002",
        )

        decision_response = self.client.post(
            "/api/tickets/TICKET-API-1/decision",
            json={"interrupt_id": "interrupt-task-1", "decision": "approve"},
        )

        self.assertEqual(decision_response.status_code, 200)
        decided_ticket = decision_response.json()
        self.assertEqual(decided_ticket["status"], "resolved")
        self.assertEqual(decided_ticket["pending_approvals"], [])
        self.assertEqual(decided_ticket["task_results"][0]["outcome"], "executed")
        self.assertEqual(
            decided_ticket["reply_draft"],
            "Order #1002 has been cancelled.",
        )
        self.assertEqual(
            self.ticket_graph.received_resume_decisions,
            [{"interrupt-task-1": "approve"}],
        )

    def test_duplicate_ticket_id_returns_conflict(self) -> None:
        request_body = {
            "ticket_id": "TICKET-API-2",
            "ticket_text": "Please cancel order #1002.",
        }
        self.client.post("/api/tickets", json=request_body)

        duplicate_response = self.client.post("/api/tickets", json=request_body)

        self.assertEqual(duplicate_response.status_code, 409)
        self.assertIn("saved graph state", duplicate_response.json()["detail"])

    def test_unknown_ticket_returns_not_found(self) -> None:
        detail_response = self.client.get("/api/tickets/UNKNOWN")
        decision_response = self.client.post(
            "/api/tickets/UNKNOWN/decision",
            json={"interrupt_id": "missing-interrupt", "decision": "reject"},
        )

        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(decision_response.status_code, 404)

    def test_decision_on_resolved_ticket_returns_conflict(self) -> None:
        self.client.post(
            "/api/tickets",
            json={
                "ticket_id": "TICKET-API-3",
                "ticket_text": "Please cancel order #1002.",
            },
        )
        self.client.post(
            "/api/tickets/TICKET-API-3/decision",
            json={"interrupt_id": "interrupt-task-1", "decision": "reject"},
        )

        repeated_decision_response = self.client.post(
            "/api/tickets/TICKET-API-3/decision",
            json={"interrupt_id": "interrupt-task-1", "decision": "approve"},
        )

        self.assertEqual(repeated_decision_response.status_code, 409)
        self.assertIn("not awaiting approval", repeated_decision_response.json()["detail"])

    def test_decision_for_non_pending_interrupt_returns_conflict(self) -> None:
        self.client.post(
            "/api/tickets",
            json={
                "ticket_id": "TICKET-API-WRONG-INTERRUPT",
                "ticket_text": "Please cancel order #1002.",
            },
        )

        response = self.client.post(
            "/api/tickets/TICKET-API-WRONG-INTERRUPT/decision",
            json={"interrupt_id": "not-pending", "decision": "reject"},
        )

        self.assertEqual(response.status_code, 409)
        self.assertIn("not pending", response.json()["detail"])

    def test_v1_checkpoint_without_task_ids_remains_readable(self) -> None:
        self.client.post(
            "/api/tickets",
            json={
                "ticket_id": "TICKET-API-V1-COMPAT",
                "ticket_text": "Please cancel order #1002.",
            },
        )
        legacy_task = make_task()
        legacy_task.pop("task_id")
        legacy_task_result = {
            "task": legacy_task,
            "outcome": "rejected_by_human",
            "gate_verdict": {
                "passed": True,
                "rule": "cancel_order_unfulfilled",
                "reason": "The order is unfulfilled and can be cancelled.",
                "flags": [],
            },
            "action_result": None,
            "policy_citations": [],
        }
        self.ticket_graph.state_snapshots["TICKET-API-V1-COMPAT"] = SimpleNamespace(
            values={
                "ticket_text": "Please cancel order #1002.",
                "tasks": [legacy_task],
                "task_results": [legacy_task_result],
                "reply_draft": "The request was declined.",
                "ticket_outcome": "resolved",
                "escalation_reason": None,
            },
            interrupts=(),
        )

        response = self.client.get("/api/tickets/TICKET-API-V1-COMPAT")

        self.assertEqual(response.status_code, 200)
        response_body = response.json()
        self.assertEqual(response_body["tasks"][0]["task_id"], "task-1")
        self.assertEqual(response_body["task_results"][0]["task_id"], "task-1")
        self.assertEqual(
            response_body["task_results"][0]["task"]["task_id"],
            "task-1",
        )

    def test_v1_pending_approval_without_task_id_remains_readable(self) -> None:
        self.client.post(
            "/api/tickets",
            json={
                "ticket_id": "TICKET-API-V1-PENDING",
                "ticket_text": "Please cancel order #1002.",
            },
        )
        legacy_task = make_task()
        legacy_task.pop("task_id")
        legacy_approval_payload = make_approval_payload()
        legacy_approval_payload.pop("task_id")
        legacy_interrupt = SimpleNamespace(
            id="legacy-interrupt",
            value=legacy_approval_payload,
        )
        self.ticket_graph.state_snapshots["TICKET-API-V1-PENDING"] = SimpleNamespace(
            values={"tasks": [legacy_task], "task_results": []},
            interrupts=(legacy_interrupt,),
        )

        response = self.client.get("/api/tickets/TICKET-API-V1-PENDING")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["pending_approvals"][0]["task_id"],
            "task-1",
        )

    def test_completed_parallel_task_is_removed_from_pending_approvals(self) -> None:
        self.client.post(
            "/api/tickets",
            json={
                "ticket_id": "TICKET-API-PARTIAL",
                "ticket_text": "Cancel #1002 and #1003.",
            },
        )
        first_task = make_task()
        second_task = {
            **make_task(),
            "task_id": "task-2",
            "order_reference": "#1003",
        }
        first_result = {
            "task_id": "task-1",
            "task": first_task,
            "outcome": "rejected_by_human",
            "gate_verdict": None,
            "action_result": None,
            "policy_citations": [],
        }
        first_approval = SimpleNamespace(
            id="interrupt-task-1",
            value=make_approval_payload(),
        )
        second_approval_payload = {
            **make_approval_payload(),
            "task_id": "task-2",
            "order": "#1003",
            "requested_reference": "#1003",
        }
        second_approval = SimpleNamespace(
            id="interrupt-task-2",
            value=second_approval_payload,
        )
        self.ticket_graph.state_snapshots["TICKET-API-PARTIAL"] = SimpleNamespace(
            values={
                "tasks": [first_task, second_task],
                "task_results": [first_result],
            },
            interrupts=(first_approval, second_approval),
        )

        response = self.client.get("/api/tickets/TICKET-API-PARTIAL")

        self.assertEqual(response.status_code, 200)
        pending_approvals = response.json()["pending_approvals"]
        self.assertEqual(len(pending_approvals), 1)
        self.assertEqual(pending_approvals[0]["interrupt_id"], "interrupt-task-2")

    def test_missing_ticket_id_is_generated(self) -> None:
        response = self.client.post(
            "/api/tickets",
            json={"ticket_text": "Please cancel order #1002."},
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["ticket_id"].startswith("TICKET-"))

    def test_request_schemas_reject_invalid_values(self) -> None:
        empty_ticket_response = self.client.post(
            "/api/tickets",
            json={"ticket_text": "   "},
        )
        self.client.post(
            "/api/tickets",
            json={
                "ticket_id": "TICKET-API-4",
                "ticket_text": "Please cancel order #1002.",
            },
        )
        invalid_decision_response = self.client.post(
            "/api/tickets/TICKET-API-4/decision",
            json={"interrupt_id": "interrupt-task-1", "decision": "delete"},
        )

        self.assertEqual(empty_ticket_response.status_code, 422)
        self.assertEqual(invalid_decision_response.status_code, 422)


class StaticFrontendTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.temporary_directory_path = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_built_frontend_is_served_without_capturing_api_routes(self) -> None:
        frontend_dist_path = self.temporary_directory_path / "dist"
        frontend_dist_path.mkdir()
        (frontend_dist_path / "index.html").write_text(
            "<html><body>storekeeper console</body></html>",
            encoding="utf-8",
        )
        application = create_app(
            ticket_graph=StubTicketGraph(),
            ticket_database_path=self.temporary_directory_path / "tickets.sqlite",
            frontend_dist_path=frontend_dist_path,
        )

        with TestClient(application) as client:
            frontend_response = client.get("/")
            api_response = client.get("/api/tickets")

        self.assertEqual(frontend_response.status_code, 200)
        self.assertIn("storekeeper console", frontend_response.text)
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.json(), [])

    def test_api_starts_when_frontend_build_is_missing(self) -> None:
        application = create_app(
            ticket_graph=StubTicketGraph(),
            ticket_database_path=self.temporary_directory_path / "tickets.sqlite",
            frontend_dist_path=self.temporary_directory_path / "missing-dist",
        )

        with TestClient(application) as client:
            frontend_response = client.get("/")
            api_response = client.get("/api/tickets")

        self.assertEqual(frontend_response.status_code, 404)
        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.json(), [])


if __name__ == "__main__":
    unittest.main()

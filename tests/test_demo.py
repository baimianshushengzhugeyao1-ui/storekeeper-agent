import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from storekeeper.demo import create_demo_app


class OfflineDemoTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.temporary_directory_path = Path(temporary_directory.name)
        self.demo_directory = self.temporary_directory_path / "offline-demo"
        self.frontend_directory = self.temporary_directory_path / "frontend-dist"
        self.enterContext(
            patch("storekeeper.demo.FRONTEND_DIST_PATH", self.frontend_directory)
        )
        # The actual API and graph run; accidentally falling back to live services fails.
        for target in [
            "langchain_openrouter.ChatOpenRouter.__init__",
            "storekeeper.shopify.client.ShopifyClient.__init__",
            "requests.sessions.Session.request",
        ]:
            self.enterContext(patch(
                target,
                side_effect=AssertionError("Offline demo attempted to use a live service."),
            ))

    def test_built_console_shows_fixture_banner_and_preserves_assets_and_api(self) -> None:
        self.frontend_directory.mkdir()
        (self.frontend_directory / "index.html").write_text(
            '<!doctype html><html><body><div id="root">Console fixture</div>'
            '<script src="/assets/app.js"></script></body></html>',
            encoding="utf-8",
        )
        assets_directory = self.frontend_directory / "assets"
        assets_directory.mkdir()
        (assets_directory / "app.js").write_text("window.fixtureConsole = true;", encoding="utf-8")
        application = create_demo_app(self.demo_directory)

        with TestClient(application) as client:
            index_response = client.get("/")
            asset_response = client.get("/assets/app.js")
            list_response = client.get("/api/tickets")

        self.assertEqual(index_response.status_code, 200)
        self.assertIn("OFFLINE DEMO", index_response.text)
        self.assertIn('data-offline-demo="true"', index_response.text)
        self.assertIn("测试数据", index_response.text)
        self.assertIn("不调用大模型或 Shopify", index_response.text)
        self.assertIn("#1036", index_response.text)
        self.assertIn("#1037", index_response.text)
        self.assertLess(index_response.text.index("OFFLINE DEMO"), index_response.text.index('id="root"'))
        self.assertEqual(asset_response.status_code, 200)
        self.assertIn("window.fixtureConsole = true;", asset_response.text)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json(), [])

    def test_missing_frontend_still_explains_fixture_mode_and_keeps_api_usable(self) -> None:
        application = create_demo_app(self.demo_directory)
        with TestClient(application) as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 503)
            self.assertIn("OFFLINE DEMO", response.text)
            self.assertIn("测试数据", response.text)
            self.assertIn("npm run build", response.text)
            self.assertEqual(client.get("/api/tickets").status_code, 200)

    def test_missing_order_runs_real_clarification_and_approval_with_only_fixture_write(self) -> None:
        application = create_demo_app(self.demo_directory)
        fixture_shopify_client = application.state.demo_shopify_client
        original_message = "帮我取消订单"
        with TestClient(application) as client:
            created_response = client.post(
                "/api/tickets",
                json={"ticket_id": "DEMO-CANCEL", "ticket_text": original_message},
            )
            self.assertEqual(created_response.status_code, 201, created_response.text)
            waiting_ticket = created_response.json()
            self.assertEqual(waiting_ticket["status"], "waiting_for_customer")
            self.assertEqual(waiting_ticket["pending_approvals"], [])
            self.assertEqual(fixture_shopify_client.write_calls, [])
            clarification = waiting_ticket["pending_clarifications"][0]
            self.assertEqual(clarification["kind"], "clarification")

            supplemented_response = client.post(
                "/api/tickets/DEMO-CANCEL/clarification",
                json={"interrupt_id": clarification["interrupt_id"], "order_reference": " 1036 "},
            )
            self.assertEqual(supplemented_response.status_code, 200, supplemented_response.text)
            approval_ticket = supplemented_response.json()
            self.assertEqual(approval_ticket["status"], "pending_approval")
            self.assertEqual(approval_ticket["ticket_text"], original_message)
            self.assertEqual(approval_ticket["pending_clarifications"], [])
            self.assertEqual(approval_ticket["clarification_history"], [
                {"task_id": "task-1", "field": "order_reference", "value": "#1036"}
            ])
            approval = approval_ticket["pending_approvals"][0]
            self.assertEqual(approval["action"], "cancel_order")
            self.assertEqual(approval["order"], "#1036")
            self.assertEqual(approval["gate_rule"], "cancel_order_unfulfilled")
            self.assertEqual(fixture_shopify_client.write_calls, [])

            resolved_response = client.post(
                "/api/tickets/DEMO-CANCEL/decision",
                json={"interrupt_id": approval["interrupt_id"], "decision": "approve"},
            )
            self.assertEqual(resolved_response.status_code, 200, resolved_response.text)
            resolved_ticket = resolved_response.json()
            self.assertEqual(resolved_ticket["status"], "resolved")
            self.assertEqual(resolved_ticket["task_results"][0]["outcome"], "submitted")
            self.assertTrue(resolved_ticket["task_results"][0]["action_result"]["shopify_job_id"].startswith("demo-job-"))
            self.assertIn("OFFLINE DEMO", resolved_ticket["reply_draft"])
            self.assertIn("No real order or payment was changed", resolved_ticket["reply_draft"])
            self.assertEqual(resolved_ticket["pending_approvals"], [])
            self.assertEqual(client.get("/api/tickets/DEMO-CANCEL").json(), resolved_ticket)

        self.assertEqual(len(fixture_shopify_client.write_calls), 1)
        self.assertEqual(fixture_shopify_client.write_calls[0]["orderId"], "gid://shopify/Order/1036")
        self.assertTrue((self.demo_directory / "tickets.sqlite").is_file())
        self.assertTrue((self.demo_directory / "checkpoints.sqlite").is_file())

    def test_fulfilled_fixture_is_denied_by_real_gate_after_supplement(self) -> None:
        application = create_demo_app(self.demo_directory)
        with TestClient(application) as client:
            created_response = client.post(
                "/api/tickets",
                json={"ticket_id": "DEMO-DENIED", "ticket_text": "Please cancel my order."},
            )
            self.assertEqual(created_response.status_code, 201, created_response.text)
            waiting_ticket = created_response.json()
            self.assertEqual(waiting_ticket["status"], "waiting_for_customer")
            clarification = waiting_ticket["pending_clarifications"][0]
            denied_response = client.post(
                "/api/tickets/DEMO-DENIED/clarification",
                json={"interrupt_id": clarification["interrupt_id"], "order_reference": "#1037"},
            )
            self.assertEqual(denied_response.status_code, 200, denied_response.text)
            denied_ticket = denied_response.json()
            self.assertEqual(denied_ticket["status"], "resolved")
            self.assertEqual(denied_ticket["pending_approvals"], [])
            self.assertEqual(denied_ticket["pending_clarifications"], [])
            task_result = denied_ticket["task_results"][0]
            self.assertEqual(task_result["outcome"], "denied_by_policy")
            self.assertFalse(task_result["gate_verdict"]["passed"])
            self.assertEqual(task_result["task"]["order_reference"], "#1037")
            self.assertIn("OFFLINE DEMO", denied_ticket["reply_draft"])
            self.assertIn("fulfilled", denied_ticket["reply_draft"])
        self.assertEqual(application.state.demo_shopify_client.write_calls, [])

    def test_unknown_order_fails_without_live_store_fallback(self) -> None:
        application = create_demo_app(self.demo_directory)
        with TestClient(application) as client:
            response = client.post(
                "/api/tickets",
                json={"ticket_id": "DEMO-UNKNOWN", "ticket_text": "Cancel order #9999."},
            )
            self.assertEqual(response.status_code, 201, response.text)
            ticket = response.json()
            self.assertEqual(ticket["pending_approvals"], [])
            self.assertEqual(ticket["pending_clarifications"], [])
            self.assertEqual(ticket["task_results"][0]["outcome"], "failed")
            self.assertIn("OFFLINE DEMO", ticket["reply_draft"])
            self.assertIn("not found", ticket["reply_draft"])
        self.assertEqual(application.state.demo_shopify_client.write_calls, [])


if __name__ == "__main__":
    unittest.main()

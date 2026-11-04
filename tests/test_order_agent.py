import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from pydantic import ValidationError

from storekeeper.api.app import create_app
from storekeeper.demo import DemoClassifier, DemoReplyModel
from storekeeper.graph.build import build_ticket_graph
from storekeeper.order_agent_fixtures import (
    DEMO_NOW, OrderAgentFixtureClient, ScriptedOrderModel, make_fixture_directory,
)
from storekeeper.order_resolution import (
    TimeRange, build_order_resolution_graph, initial_resolution_state, make_order_tools,
)


def call(name, **args):
    return {"name": name, "args": args}


SEARCH = call("search_orders_by_time", start_time="2026-11-03T12:00:00+08:00",
              end_time="2026-11-03T18:00:00+08:00")


class SequenceModel:
    def __init__(self, actions):
        self.actions, self.inputs = actions, []

    def bind_tools(self, tools, **kwargs):
        self.names = [t.name for t in tools]
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        index = sum(m.type == "tool" for m in messages)
        if index >= len(self.actions):
            return AIMessage(content="Cannot resolve.")
        action = self.actions[index]
        return AIMessage(content="", tool_calls=[{**action, "id": f"call-{index}"}])


class OrderDirectoryTests(unittest.TestCase):
    def test_time_window_end_is_exclusive(self):
        result = make_fixture_directory().search(TimeRange(start_time="2026-11-03T13:00:00+08:00",
                                                           end_time="2026-11-03T13:30:00+08:00"))
        self.assertEqual(result["match_count"], 0)

    def test_time_search_is_scoped_and_returns_both_candidates(self):
        directory = make_fixture_directory()
        result = directory.search(TimeRange.model_validate(SEARCH["args"]))
        self.assertEqual([o["order_reference"] for o in result["orders"]], ["#2036", "#2037"])
        self.assertNotIn("其他客户", json.dumps(result, ensure_ascii=False))
        with self.assertRaises(LookupError):
            directory.get("9036")

    def test_time_ranges_require_offsets_positive_duration_and_bounded_window(self):
        for start, end in [("2026-11-03T12:00:00", "2026-11-03T18:00:00"),
                           ("2026-11-03T18:00:00+08:00", "2026-11-03T12:00:00+08:00"),
                           ("2026-09-01T00:00:00+08:00", "2026-11-04T00:00:00+08:00")]:
            with self.subTest(start=start), self.assertRaises(ValidationError):
                TimeRange(start_time=start, end_time=end)

    def test_model_cannot_supply_customer_identity_or_write_tool(self):
        tools = {t.name: t for t in make_order_tools(make_fixture_directory())}
        self.assertNotIn("cancel_order", tools)
        with self.assertRaises(ValidationError):
            tools["search_orders_by_time"].invoke({**SEARCH["args"], "customer_id": "demo-customer-B"})


class OrderAgentIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = make_fixture_directory()
        self.client = OrderAgentFixtureClient(self.directory)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name)

    @contextmanager
    def api(self, model=None):
        model = model or ScriptedOrderModel()
        with SqliteSaver.from_conn_string(str(self.path / "checkpoints.sqlite")) as saver:
            graph = build_ticket_graph(checkpointer=saver, classifier_model=DemoClassifier(),
                draft_model=DemoReplyModel(), shopify_client=self.client,
                order_agent_model=model, order_directory=self.directory)
            app = create_app(ticket_graph=graph, ticket_database_path=self.path / "tickets.sqlite",
                             frontend_dist_path=self.path / "no-dist")
            with TestClient(app) as http:
                yield http, graph

    def create(self, http, text="帮我取消昨天下午的订单", ticket_id="AGENT-1"):
        response = http.post("/api/tickets", json={"ticket_id": ticket_id, "ticket_text": text})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def supplement(self, http, ticket, reference):
        return http.post(f"/api/tickets/{ticket['ticket_id']}/clarification", json={
            "interrupt_id": ticket["pending_clarifications"][0]["interrupt_id"], "order_reference": reference})

    def test_unique_time_match_binds_order_but_does_not_write_before_approval(self):
        with self.api() as (http, _):
            ticket = self.create(http, "帮我取消昨天下午13点到14点的订单")
            self.assertEqual(ticket["status"], "pending_approval")
            self.assertEqual(ticket["tasks"][0]["order_reference"], "#2036")
            self.assertEqual(self.client.write_calls, [])
            trace = ticket["order_resolution"]
            self.assertEqual([e["name"] for e in trace["events"] if e["kind"] == "tool"],
                             ["search_orders_by_time", "select_order"])
            approval = ticket["pending_approvals"][0]
            result = http.post("/api/tickets/AGENT-1/decision", json={
                "interrupt_id": approval["interrupt_id"], "decision": "approve"})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["task_results"][0]["outcome"], "submitted")
            self.assertEqual(len(self.client.write_calls), 1)

    def test_multiple_matches_offer_choice_and_resume_after_sqlite_restart(self):
        model = SequenceModel([SEARCH, call("request_order_reference", reason="请选择"),
                               call("select_order", order_reference="#2037")])
        with self.api(model) as (http, _):
            ticket = self.create(http)
            self.assertEqual(ticket["status"], "waiting_for_customer")
            question = ticket["pending_clarifications"][0]
            self.assertEqual([o["order_reference"] for o in question["choices"]], ["#2036", "#2037"])
            self.assertEqual(self.client.write_calls, [])
        with self.api(model) as (http, _):
            result = self.supplement(http, ticket, "2037")
            self.assertEqual(result.status_code, 200, result.text)
            restored = result.json()
            self.assertEqual(restored["status"], "pending_approval")
            self.assertEqual(restored["tasks"][0]["order_reference"], "#2037")
            self.assertEqual(restored["tasks"][0]["intent"], "cancel_order")
            self.assertEqual(restored["clarification_history"][0]["value"], "#2037")
            self.assertEqual(len(model.inputs), 3, "Completed search/model nodes must not rerun on resume.")
            self.assertEqual(self.client.write_calls, [])

    def test_choice_not_in_candidates_is_rejected_before_graph_resume(self):
        with self.api() as (http, graph):
            ticket = self.create(http)
            from unittest.mock import patch
            with patch.object(graph, "invoke", wraps=graph.invoke) as invoke:
                for reference in ("2038", "9036"):
                    response = self.supplement(http, ticket, reference)
                    self.assertEqual(response.status_code, 422)
                invoke.assert_not_called()
            self.assertEqual(self.client.write_calls, [])

    def test_model_cannot_choose_one_of_multiple_without_customer_choice(self):
        model = SequenceModel([SEARCH, call("select_order", order_reference="#2036")])
        with self.api(model) as (http, _):
            ticket = self.create(http)
            self.assertEqual(ticket["ticket_outcome"], "escalated_to_human")
            self.assertIsNone(ticket["tasks"][0]["order_reference"])
            tool_events = [e for e in ticket["order_resolution"]["events"] if e["kind"] == "tool"]
            self.assertFalse(tool_events[-1]["result"]["ok"])
            self.assertEqual(self.client.write_calls, [])

    def test_unobserved_selection_and_unknown_write_tool_cannot_bind_or_execute(self):
        for action in [call("select_order", order_reference="#2036"), call("cancel_order", order_reference="#2036")]:
            with self.subTest(action=action), self.api(SequenceModel([action])) as (http, _):
                ticket = self.create(http, ticket_id=action["name"])
                self.assertEqual(ticket["ticket_outcome"], "escalated_to_human")
                self.assertEqual(ticket["pending_approvals"], [])
                self.assertEqual(self.client.write_calls, [])

    def test_missing_time_can_ask_for_explicit_reference_without_search(self):
        with self.api() as (http, _):
            ticket = self.create(http, "帮我取消订单")
            self.assertEqual(ticket["status"], "waiting_for_customer")
            self.assertEqual(ticket["pending_clarifications"][0]["choices"], [])
            response = self.supplement(http, ticket, "2036")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "pending_approval")

    def test_explicit_other_customer_reference_does_not_reach_approval(self):
        with self.api() as (http, _):
            ticket = self.create(http, "帮我取消订单#9036")
            self.assertEqual(ticket["task_results"][0]["outcome"], "failed")
            self.assertEqual(self.client.write_calls, [])

    def test_selected_fulfilled_order_still_fails_original_policy_gate(self):
        with self.api() as (http, _):
            ticket = self.create(http, "帮我取消订单")
            response = self.supplement(http, ticket, "2039")
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["task_results"][0]["outcome"], "denied_by_policy")
            self.assertEqual(response.json()["pending_approvals"], [])
            self.assertEqual(self.client.write_calls, [])

    def test_rejected_approval_never_writes(self):
        with self.api() as (http, _):
            ticket = self.create(http, "帮我取消昨天下午13点到14点的订单")
            response = http.post("/api/tickets/AGENT-1/decision", json={
                "interrupt_id": ticket["pending_approvals"][0]["interrupt_id"], "decision": "reject"})
            self.assertEqual(response.json()["task_results"][0]["outcome"], "rejected_by_human")
            self.assertEqual(self.client.write_calls, [])

    def test_order_agent_cannot_be_enabled_with_a_live_action_client(self):
        with self.assertRaises(ValueError):
            build_ticket_graph(checkpointer=InMemorySaver(), classifier_model=DemoClassifier(),
                draft_model=DemoReplyModel(), shopify_client=object(),
                order_agent_model=ScriptedOrderModel(), order_directory=self.directory)

    def test_budget_limits_repeated_invalid_calls(self):
        model = SequenceModel([call("cancel_order", order_reference="#2036")] * 10)
        with self.api(model) as (http, _):
            ticket = self.create(http)
            trace = ticket["order_resolution"]
            self.assertEqual(trace["status"], "failed")
            self.assertEqual(len([e for e in trace["events"] if e["kind"] == "tool"]), 6)
            self.assertEqual(self.client.write_calls, [])

    def test_model_failure_uses_deterministic_escalation_without_second_draft_call(self):
        class UnavailableModel(SequenceModel):
            def invoke(self, messages):
                raise TimeoutError("private transport details")
        with self.api(UnavailableModel([])) as (http, _):
            ticket = self.create(http)
            self.assertEqual(ticket["ticket_outcome"], "escalated_to_human")
            self.assertIn("未执行", ticket["reply_draft"])
            self.assertNotIn("private transport", json.dumps(ticket))
            self.assertEqual(self.client.write_calls, [])

    def test_empty_search_asks_then_accepts_explicit_customer_reference(self):
        empty_search = call("search_orders_by_time", start_time="2026-11-03T09:00:00+08:00",
                            end_time="2026-11-03T10:00:00+08:00")
        model = SequenceModel([empty_search, call("request_order_reference", reason="未找到"),
                               call("select_order", order_reference="#2036")])
        with self.api(model) as (http, _):
            ticket = self.create(http)
            self.assertEqual(ticket["pending_clarifications"][0]["choices"], [])
            result = self.supplement(http, ticket, "2036").json()
            events = [e for e in result["order_resolution"]["events"] if e["kind"] == "tool"]
            self.assertEqual(events[0]["result"]["match_count"], 0)
            self.assertEqual(result["status"], "pending_approval")
            self.assertEqual(self.client.write_calls, [])

    def test_model_can_inspect_a_discovered_order_then_select(self):
        unique_search = call("search_orders_by_time", start_time="2026-11-03T13:00:00+08:00",
                             end_time="2026-11-03T14:00:00+08:00")
        model = SequenceModel([unique_search, call("get_order_details", order_reference="#2036"),
                               call("select_order", order_reference="#2036")])
        with self.api(model) as (http, _):
            ticket = self.create(http)
            self.assertEqual(ticket["status"], "pending_approval")
            tools = [e for e in ticket["order_resolution"]["events"] if e["kind"] == "tool"]
            self.assertEqual(tools[1]["result"]["order"]["summary"], "无线耳机")
            self.assertEqual(self.client.write_calls, [])

    def test_text_claim_without_selection_tool_is_not_a_success(self):
        with self.api(SequenceModel([])) as (http, _):
            ticket = self.create(http)
            self.assertEqual(ticket["ticket_outcome"], "escalated_to_human")
            self.assertEqual(ticket["pending_approvals"], [])
            self.assertIsNone(ticket["tasks"][0]["order_reference"])

    def test_another_customers_reference_in_free_text_answer_cannot_bind(self):
        with self.api() as (http, _):
            ticket = self.create(http, "帮我取消订单")
            result = self.supplement(http, ticket, "9036")
            self.assertEqual(result.status_code, 200, result.text)
            self.assertIsNone(result.json()["tasks"][0]["order_reference"])
            self.assertEqual(self.client.write_calls, [])


if __name__ == "__main__":
    unittest.main()

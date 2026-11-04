import unittest
from copy import deepcopy
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from storekeeper.order_agent_evaluation import ModelAccounting, assess_discovery


class OrderAgentScoringTests(unittest.TestCase):
    def make_unique(self):
        case = {"status": "pending_approval", "reference": "#2036",
                "search_start": "2026-11-03T13:00:00+08:00", "search_end": "2026-11-03T14:00:00+08:00", "match_count": 1}
        ticket = {"status": "pending_approval", "tasks": [{"intent": "cancel_order", "order_reference": "#2036"}],
                  "order_resolution": {"status": "resolved", "selected_reference": "#2036", "events": [
                      {"kind": "tool", "name": "search_orders_by_time", "arguments": {
                          "start_time": case["search_start"], "end_time": case["search_end"]},
                       "result": {"ok": True, "match_count": 1}},
                      {"kind": "tool", "name": "select_order"}]}}
        return case, ticket

    def test_correct_final_reference_alone_does_not_pass_agent_acceptance(self):
        case, good = self.make_unique()
        self.assertTrue(all(assess_discovery(case, good, good).values()))
        missing_trace = {**good, "order_resolution": None}
        self.assertFalse(all(assess_discovery(case, missing_trace, missing_trace).values()))
        wrong_time = deepcopy(good)
        wrong_time["order_resolution"]["events"][0]["arguments"]["start_time"] = "2026-11-01T13:00:00+08:00"
        self.assertFalse(assess_discovery(case, wrong_time, wrong_time)["time_search_result"])

    def test_ambiguous_case_requires_the_exact_candidate_set_and_customer_choice(self):
        case, current = self.make_unique()
        case.update(status="waiting_for_customer", answer="#2036", choices=["#2036", "#2037"], match_count=2)
        initial = {"status": "waiting_for_customer", "pending_clarifications": [{"choices": [
            {"order_reference": "#2036"}, {"order_reference": "#2037"}]}]}
        checks = assess_discovery(case, initial, current)
        self.assertTrue(checks["choice_set"])
        self.assertFalse(checks["customer_choice_recorded"])
        initial["pending_clarifications"][0]["choices"].pop()
        self.assertFalse(assess_discovery(case, initial, current)["choice_set"])

    def test_usage_counts_once_per_api_call_and_does_not_copy_response_or_error_text(self):
        tracker = ModelAccounting()
        run_id = uuid4()
        tracker.on_chat_model_start({}, [], run_id=run_id)
        message = AIMessage(content="not recorded", additional_kwargs={"reasoning_content": "not recorded"},
                            usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13})
        tracker.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_id)
        tracker.on_llm_error(TimeoutError("sensitive transport detail"), run_id=uuid4())
        summary = tracker.summary()
        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["successful_calls"], 1)
        self.assertEqual(summary["total_tokens"], 13)
        self.assertNotIn("not recorded", str(tracker.records))
        self.assertNotIn("sensitive transport", str(tracker.records))


if __name__ == "__main__":
    unittest.main()

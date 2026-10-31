import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from langgraph.types import Command

from test_api import make_approval_payload


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_ticket.py"
SCRIPT_SPECIFICATION = importlib.util.spec_from_file_location("clarification_run_ticket", SCRIPT_PATH)
run_ticket = importlib.util.module_from_spec(SCRIPT_SPECIFICATION)
SCRIPT_SPECIFICATION.loader.exec_module(run_ticket)


def make_clarification_interrupt():
    return SimpleNamespace(
        id="customer-question-1",
        value={
            "kind": "clarification",
            "task_id": "task-1",
            "field": "order_reference",
            "question": "What is your order number?",
        },
    )


def make_approval_interrupt():
    return SimpleNamespace(id="approval-1", value=make_approval_payload())


class ClarificationCliTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.checkpoint_database_path = Path(temporary_directory.name) / "isolated" / "checkpoints.sqlite"
        self.enterContext(patch.object(run_ticket, "CHECKPOINT_DATABASE_PATH", self.checkpoint_database_path))
        self.checkpoint_factory = self.enterContext(patch.object(run_ticket.SqliteSaver, "from_conn_string"))
        self.ticket_graph = Mock()
        self.ticket_graph.get_state.return_value = SimpleNamespace(
            values={"task_results": []},
            interrupts=(make_clarification_interrupt(),),
        )
        self.ticket_graph.invoke.return_value = {
            "ticket_outcome": "resolved",
            "tasks": [],
            "task_results": [],
            "escalation_reason": None,
            "reply_draft": "The request was reviewed.",
        }
        self.graph_factory = self.enterContext(patch.object(run_ticket, "build_ticket_graph", return_value=self.ticket_graph))
        self.create_ticket = self.enterContext(patch.object(run_ticket, "create_ticket"))

    def run_main(self, arguments: list[str]) -> str:
        output = io.StringIO()
        with patch("sys.argv", ["run_ticket.py", *arguments]), redirect_stdout(output):
            run_ticket.main()
        return output.getvalue()

    def assert_cli_error(self, arguments: list[str]) -> str:
        errors = io.StringIO()
        with patch("sys.argv", ["run_ticket.py", *arguments]), redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                run_ticket.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("error:", errors.getvalue())
        return errors.getvalue()

    def test_print_customer_question_needs_no_approval_fields(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            run_ticket.print_result("CLI-QUESTION", {"__interrupt__": (make_clarification_interrupt(),)})

        self.assertIn("Waiting for customer: What is your order number?", output.getvalue())
        self.assertIn("--clarify customer-question-1 --order-reference '#1036'", output.getvalue())
        self.assertNotIn("--approve", output.getvalue())
        self.assertNotIn("Action:", output.getvalue())

    def test_print_mixed_interrupts_keeps_distinct_question_and_approval_commands(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            run_ticket.print_result("CLI-MIXED", {"__interrupt__": (
                make_clarification_interrupt(), make_approval_interrupt(),
            )})

        self.assertIn("What is your order number?", output.getvalue())
        self.assertIn("--clarify customer-question-1", output.getvalue())
        self.assertIn("Action: cancel_order on order #1002", output.getvalue())
        self.assertIn("--approve approval-1", output.getvalue())
        self.assertIn("--reject approval-1", output.getvalue())

    def test_invalid_flags_and_references_fail_before_checkpoint_or_graph_construction(self) -> None:
        invalid_arguments = [
            ["CLI-INVALID"],
            ["CLI-INVALID", "--clarify", "question-1"],
            ["CLI-INVALID", "--order-reference", "1036"],
            ["CLI-INVALID", "Cancel my order", "--order-reference", "1036"],
            ["CLI-INVALID", "Cancel my order", "--clarify", "question-1", "--order-reference", "1036"],
            ["CLI-INVALID", "--approve", "approval-1", "--order-reference", "1036"],
            ["CLI-INVALID", "--approve", "approval-1", "--clarify", "question-1", "--order-reference", "1036"],
            ["CLI-INVALID", "--reject", "approval-1", "--clarify", "question-1", "--order-reference", "1036"],
        ]
        invalid_arguments.extend(
            ["CLI-INVALID", "--clarify", "question-1", "--order-reference", reference]
            for reference in ["", "   ", "##1036", "1036 and 1037", "１２３４", "١٢٣٤", "12345678901"]
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                self.assert_cli_error(arguments)
        self.checkpoint_factory.assert_not_called()
        self.graph_factory.assert_not_called()
        self.ticket_graph.get_state.assert_not_called()
        self.ticket_graph.invoke.assert_not_called()
        self.create_ticket.assert_not_called()
        self.assertFalse(self.checkpoint_database_path.parent.exists())

    def test_approve_and_reject_cannot_resume_customer_question(self) -> None:
        for decision_flag in ["--approve", "--reject"]:
            with self.subTest(decision_flag=decision_flag):
                errors = self.assert_cli_error(["CLI-QUESTION", decision_flag, "customer-question-1"])
                self.assertIn("Use --clarify", errors)
        self.ticket_graph.invoke.assert_not_called()
        self.create_ticket.assert_not_called()

    def test_clarify_cannot_resume_approval(self) -> None:
        self.ticket_graph.get_state.return_value = SimpleNamespace(
            values={"task_results": []},
            interrupts=(make_approval_interrupt(),),
        )
        errors = self.assert_cli_error([
            "CLI-APPROVAL", "--clarify", "approval-1", "--order-reference", "1036",
        ])
        self.assertIn("--approve/--reject", errors)
        self.ticket_graph.invoke.assert_not_called()
        self.create_ticket.assert_not_called()

    def test_unknown_and_completed_interrupts_cannot_resume(self) -> None:
        for state_snapshot in [
            SimpleNamespace(values={"task_results": []}, interrupts=()),
            SimpleNamespace(
                values={"task_results": [{"task_id": "task-1", "task": {"task_id": "task-1"}}]},
                interrupts=(make_clarification_interrupt(),),
            ),
        ]:
            with self.subTest(state_snapshot=state_snapshot):
                self.ticket_graph.get_state.return_value = state_snapshot
                errors = self.assert_cli_error([
                    "CLI-STALE", "--clarify", "customer-question-1", "--order-reference", "1036",
                ])
                self.assertIn("not pending", errors)
        self.ticket_graph.invoke.assert_not_called()
        self.create_ticket.assert_not_called()

    def test_clarify_normalizes_one_reference_and_resumes_its_interrupt(self) -> None:
        self.ticket_graph.invoke.return_value = {"__interrupt__": (make_approval_interrupt(),)}
        output = self.run_main([
            "CLI-QUESTION", "--clarify", "customer-question-1", "--order-reference", " 1036 ",
        ])

        self.ticket_graph.invoke.assert_called_once()
        graph_input, configuration = self.ticket_graph.invoke.call_args.args
        self.assertIsInstance(graph_input, Command)
        self.assertEqual(graph_input.resume, {"customer-question-1": {"order_reference": "#1036"}})
        self.assertEqual(configuration, {"configurable": {"thread_id": "CLI-QUESTION"}})
        self.assertIn("Action: cancel_order on order #1002", output)
        self.assertIn("--approve approval-1", output)
        self.create_ticket.assert_not_called()

    def test_existing_approval_flags_still_resume_only_the_selected_approval(self) -> None:
        self.ticket_graph.get_state.return_value = SimpleNamespace(
            values={"task_results": []},
            interrupts=(make_approval_interrupt(),),
        )
        for decision in ["approve", "reject"]:
            with self.subTest(decision=decision):
                self.ticket_graph.invoke.reset_mock()
                output = self.run_main(["CLI-APPROVAL", f"--{decision}", "approval-1"])
                self.ticket_graph.invoke.assert_called_once()
                graph_input, configuration = self.ticket_graph.invoke.call_args.args
                self.assertEqual(graph_input.resume, {"approval-1": decision})
                self.assertEqual(configuration, {"configurable": {"thread_id": "CLI-APPROVAL"}})
                self.assertIn("resolved", output)
        self.create_ticket.assert_not_called()


if __name__ == "__main__":
    unittest.main()

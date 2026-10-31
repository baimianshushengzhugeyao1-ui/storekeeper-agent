import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from storekeeper.tickets import (
    DuplicateTicketError,
    create_ticket,
    generate_ticket_id,
    get_ticket,
    get_ticket_status,
    list_tickets,
)


class StubTicketGraph:
    def __init__(self, state_snapshot: SimpleNamespace):
        self.state_snapshot = state_snapshot
        self.received_config: dict | None = None

    def get_state(self, config: dict) -> SimpleNamespace:
        self.received_config = config
        return self.state_snapshot


class TicketRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "tickets.sqlite"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_create_and_list_tickets_newest_first(self) -> None:
        first_ticket = create_ticket(
            "TICKET-1",
            "First ticket",
            database_path=self.database_path,
        )
        second_ticket = create_ticket(
            "TICKET-2",
            "Second ticket",
            database_path=self.database_path,
        )

        tickets = list_tickets(database_path=self.database_path)

        self.assertEqual(
            [ticket["ticket_id"] for ticket in tickets],
            ["TICKET-2", "TICKET-1"],
        )
        self.assertEqual(first_ticket["ticket_text"], "First ticket")
        self.assertEqual(second_ticket["ticket_text"], "Second ticket")

    def test_duplicate_ticket_id_raises_specific_error(self) -> None:
        create_ticket(
            "TICKET-1",
            "Original ticket",
            database_path=self.database_path,
        )

        with self.assertRaisesRegex(DuplicateTicketError, "already been used"):
            create_ticket(
                "TICKET-1",
                "Different ticket",
                database_path=self.database_path,
            )

    def test_get_ticket_returns_one_record_or_none(self) -> None:
        create_ticket(
            "TICKET-1",
            "Original ticket",
            database_path=self.database_path,
        )

        ticket = get_ticket("TICKET-1", database_path=self.database_path)
        missing_ticket = get_ticket("UNKNOWN", database_path=self.database_path)

        assert ticket is not None
        self.assertEqual(ticket["ticket_text"], "Original ticket")
        self.assertIsNone(missing_ticket)

    def test_generated_ticket_ids_are_unique(self) -> None:
        fixed_timestamp = datetime(
            2026,
            7,
            14,
            8,
            30,
            tzinfo=timezone.utc,
        ).timestamp()
        with (
            patch("storekeeper.tickets.time.time", return_value=fixed_timestamp),
            patch(
                "storekeeper.tickets._last_ticket_id_timestamp_seconds",
                int(fixed_timestamp) - 1,
            ),
        ):
            generated_ticket_ids = [generate_ticket_id() for _ in range(3)]

        self.assertEqual(
            generated_ticket_ids,
            [
                "TICKET-20260714-083000",
                "TICKET-20260714-083001",
                "TICKET-20260714-083002",
            ],
        )


class TicketStatusTests(unittest.TestCase):
    def test_status_is_not_found_when_no_checkpoint_values_exist(self) -> None:
        ticket_graph = StubTicketGraph(
            SimpleNamespace(values={}, interrupts=())
        )

        status = get_ticket_status("TICKET-1", ticket_graph)

        self.assertEqual(status, "not_found")
        self.assertEqual(
            ticket_graph.received_config,
            {"configurable": {"thread_id": "TICKET-1"}},
        )

    def test_status_is_pending_when_checkpoint_is_interrupted(self) -> None:
        ticket_graph = StubTicketGraph(
            SimpleNamespace(
                values={"ticket_text": "Cancel order #1001."},
                interrupts=(object(),),
            )
        )

        status = get_ticket_status("TICKET-2", ticket_graph)

        self.assertEqual(status, "pending_approval")

    def test_status_distinguishes_clarification_from_legacy_approval(self) -> None:
        for payload, expected_status in [
            ({"kind": "clarification", "field": "order_reference"}, "waiting_for_customer"),
            ({"action": "cancel_order"}, "pending_approval"),
        ]:
            with self.subTest(payload=payload):
                ticket_graph = StubTicketGraph(
                    SimpleNamespace(
                        values={"ticket_text": "Cancel my order."},
                        interrupts=(SimpleNamespace(value=payload),),
                    )
                )

                self.assertEqual(get_ticket_status("TICKET-2", ticket_graph), expected_status)

    def test_status_is_resolved_when_checkpoint_has_no_interrupt(self) -> None:
        ticket_graph = StubTicketGraph(
            SimpleNamespace(
                values={"ticket_outcome": "resolved"},
                interrupts=(),
            )
        )

        status = get_ticket_status("TICKET-3", ticket_graph)

        self.assertEqual(status, "resolved")


if __name__ == "__main__":
    unittest.main()

"""Ticket registry and checkpoint-derived ticket status."""

import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict

from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from storekeeper.graph.clarification import is_clarification_payload

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CHECKPOINT_DATABASE_PATH = REPOSITORY_ROOT / "var" / "checkpoints.sqlite"
TICKET_DATABASE_PATH = REPOSITORY_ROOT / "var" / "tickets.sqlite"

TicketStatus = Literal["waiting_for_customer", "pending_approval", "resolved", "not_found"]


class TicketRecord(TypedDict):
    ticket_id: str
    ticket_text: str
    created_at: str


class DuplicateTicketError(ValueError):
    """Raised when a ticket id is already present in the registry."""


_ticket_id_lock = threading.Lock()
_last_ticket_id_timestamp_seconds = 0


def generate_ticket_id() -> str:
    """Return a timestamp-based id that stays unique within this process."""
    global _last_ticket_id_timestamp_seconds

    with _ticket_id_lock:
        current_timestamp_seconds = int(time.time())
        unique_timestamp_seconds = max(
            current_timestamp_seconds,
            _last_ticket_id_timestamp_seconds + 1,
        )
        _last_ticket_id_timestamp_seconds = unique_timestamp_seconds

    readable_timestamp = datetime.fromtimestamp(
        unique_timestamp_seconds,
        timezone.utc,
    ).strftime("%Y%m%d-%H%M%S")
    return f"TICKET-{readable_timestamp}"


def create_ticket(
    ticket_id: str,
    ticket_text: str,
    *,
    database_path: Path = TICKET_DATABASE_PATH,
) -> TicketRecord:
    cleaned_ticket_id = ticket_id.strip()
    cleaned_ticket_text = ticket_text.strip()
    if not cleaned_ticket_id:
        raise ValueError("Ticket id cannot be empty.")
    if not cleaned_ticket_text:
        raise ValueError("Ticket text cannot be empty.")

    created_at = datetime.now(timezone.utc).isoformat()
    _initialize_ticket_database(database_path)
    try:
        with closing(sqlite3.connect(database_path)) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO tickets (ticket_id, ticket_text, created_at)
                    VALUES (?, ?, ?)
                    """,
                    (cleaned_ticket_id, cleaned_ticket_text, created_at),
                )
    except sqlite3.IntegrityError as error:
        raise DuplicateTicketError(
            f"Ticket id {cleaned_ticket_id!r} has already been used."
        ) from error

    return {
        "ticket_id": cleaned_ticket_id,
        "ticket_text": cleaned_ticket_text,
        "created_at": created_at,
    }


def list_tickets(
    *,
    database_path: Path = TICKET_DATABASE_PATH,
) -> list[TicketRecord]:
    _initialize_ticket_database(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        rows = connection.execute(
            """
            SELECT ticket_id, ticket_text, created_at
            FROM tickets
            ORDER BY created_at DESC, rowid DESC
            """
        ).fetchall()
    return [
        {
            "ticket_id": row[0],
            "ticket_text": row[1],
            "created_at": row[2],
        }
        for row in rows
    ]


def get_ticket(
    ticket_id: str,
    *,
    database_path: Path = TICKET_DATABASE_PATH,
) -> TicketRecord | None:
    _initialize_ticket_database(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        row = connection.execute(
            """
            SELECT ticket_id, ticket_text, created_at
            FROM tickets
            WHERE ticket_id = ?
            """,
            (ticket_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "ticket_id": row[0],
        "ticket_text": row[1],
        "created_at": row[2],
    }


def get_ticket_status(
    ticket_id: str,
    ticket_graph: CompiledStateGraph,
) -> TicketStatus:
    state_snapshot = ticket_graph.get_state(
        {"configurable": {"thread_id": ticket_id}}
    )
    return derive_ticket_status(state_snapshot)


def derive_ticket_status(state_snapshot: StateSnapshot) -> TicketStatus:
    if not state_snapshot.values:
        return "not_found"
    if any(
        is_clarification_payload(getattr(pending_interrupt, "value", None))
        for pending_interrupt in state_snapshot.interrupts
    ):
        return "waiting_for_customer"
    if state_snapshot.interrupts:
        return "pending_approval"
    return "resolved"


def _initialize_ticket_database(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database_path)) as connection:
        with connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    ticket_id TEXT PRIMARY KEY,
                    ticket_text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

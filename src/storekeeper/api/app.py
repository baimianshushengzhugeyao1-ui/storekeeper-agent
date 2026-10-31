"""FastAPI wrapper around the existing ticket graph."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    HTTPException,
    Path as PathParameter,
    Request,
)
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from storekeeper.api.schemas import (
    ApprovalPayloadResponse,
    ClarificationPayloadResponse,
    CreateTicketRequest,
    TicketClarificationRequest,
    TicketDecisionRequest,
    TicketDetailResponse,
    TicketSummaryResponse,
)
from storekeeper.graph.build import build_ticket_graph
from storekeeper.graph.clarification import is_clarification_payload
from storekeeper.tickets import (
    CHECKPOINT_DATABASE_PATH,
    TICKET_DATABASE_PATH,
    DuplicateTicketError,
    TicketRecord,
    create_ticket,
    derive_ticket_status,
    generate_ticket_id,
    get_ticket,
    get_ticket_status,
    list_tickets,
)

router = APIRouter(prefix="/api/tickets", tags=["tickets"])
FRONTEND_DIST_PATH = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def get_request_ticket_graph(request: Request) -> CompiledStateGraph:
    return request.app.state.ticket_graph


def get_request_ticket_database_path(request: Request) -> Path:
    return request.app.state.ticket_database_path


TicketGraphDependency = Annotated[CompiledStateGraph, Depends(get_request_ticket_graph)]
TicketDatabasePathDependency = Annotated[Path, Depends(get_request_ticket_database_path)]
TicketIdPath = Annotated[str, PathParameter(min_length=1)]


@router.post("", status_code=201)
def create_ticket_endpoint(
    request: Request,
    request_body: CreateTicketRequest,
    ticket_graph: TicketGraphDependency,
    ticket_database_path: TicketDatabasePathDependency,
) -> TicketDetailResponse:
    with request.app.state.ticket_graph_lock:
        ticket_id = request_body.ticket_id or generate_ticket_id()
        if get_ticket_status(ticket_id, ticket_graph) != "not_found":
            raise HTTPException(
                status_code=409,
                detail=f"Ticket id {ticket_id!r} already has saved graph state.",
            )

        try:
            ticket_record = create_ticket(
                ticket_id,
                request_body.ticket_text,
                database_path=ticket_database_path,
            )
        except DuplicateTicketError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        ticket_graph.invoke(
            {
                "ticket_text": ticket_record["ticket_text"],
                "tasks": [],
                "task_results": [],
                "reply_draft": None,
                "ticket_outcome": None,
                "escalation_reason": None,
                "plan_conflict_reason": None,
                "clarification_history": [],
            },
            _thread_config(ticket_id),
        )
        return _build_ticket_detail_response(ticket_record, ticket_graph)


@router.get("")
def list_tickets_endpoint(
    request: Request,
    ticket_graph: TicketGraphDependency,
    ticket_database_path: TicketDatabasePathDependency,
) -> list[TicketSummaryResponse]:
    with request.app.state.ticket_graph_lock:
        return [
            TicketSummaryResponse(
                **ticket_record,
                status=get_ticket_status(ticket_record["ticket_id"], ticket_graph),
            )
            for ticket_record in list_tickets(database_path=ticket_database_path)
        ]


@router.get("/{ticket_id}")
def get_ticket_endpoint(
    request: Request,
    ticket_id: TicketIdPath,
    ticket_graph: TicketGraphDependency,
    ticket_database_path: TicketDatabasePathDependency,
) -> TicketDetailResponse:
    with request.app.state.ticket_graph_lock:
        ticket_record = _require_ticket(ticket_id, ticket_database_path)
        return _build_ticket_detail_response(ticket_record, ticket_graph)


@router.post("/{ticket_id}/decision")
def decide_ticket_endpoint(
    request: Request,
    ticket_id: TicketIdPath,
    request_body: TicketDecisionRequest,
    ticket_graph: TicketGraphDependency,
    ticket_database_path: TicketDatabasePathDependency,
) -> TicketDetailResponse:
    with request.app.state.ticket_graph_lock:
        ticket_record = _require_ticket(ticket_id, ticket_database_path)
        if get_ticket_status(ticket_id, ticket_graph) != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Ticket {ticket_id!r} is not awaiting approval.",
            )

        state_snapshot = ticket_graph.get_state(_thread_config(ticket_id))
        state_values = state_snapshot.values
        tasks = _normalize_legacy_tasks(state_values.get("tasks", []))
        task_results = _normalize_legacy_task_results(
            state_values.get("task_results", []),
            tasks,
        )
        pending_interrupt_ids = {
            pending_approval.interrupt_id
            for pending_approval in _get_pending_approvals(
                state_snapshot,
                tasks,
                task_results,
            )
        }
        if request_body.interrupt_id not in pending_interrupt_ids:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Interrupt {request_body.interrupt_id!r} is not pending on "
                    f"ticket {ticket_id!r}."
                ),
            )

        ticket_graph.invoke(
            Command(
                resume={request_body.interrupt_id: request_body.decision},
            ),
            _thread_config(ticket_id),
        )
        return _build_ticket_detail_response(ticket_record, ticket_graph)


@router.post("/{ticket_id}/clarification")
def clarify_ticket_endpoint(
    request: Request,
    ticket_id: TicketIdPath,
    request_body: TicketClarificationRequest,
    ticket_graph: TicketGraphDependency,
    ticket_database_path: TicketDatabasePathDependency,
) -> TicketDetailResponse:
    # Keep the pending-state check and resume atomic in this single-process app.
    # A customer answer must never be accepted by the approval endpoint.
    with request.app.state.ticket_graph_lock:
        ticket_record = _require_ticket(ticket_id, ticket_database_path)
        state_snapshot = ticket_graph.get_state(_thread_config(ticket_id))
        if derive_ticket_status(state_snapshot) != "waiting_for_customer":
            raise HTTPException(status_code=409, detail="This ticket is not awaiting customer information.")
        pending_ids = {
            clarification.interrupt_id
            for clarification in _get_pending_clarifications(state_snapshot)
        }
        if request_body.interrupt_id not in pending_ids:
            raise HTTPException(status_code=409, detail="This clarification is no longer pending on this ticket.")
        ticket_graph.invoke(
            Command(resume={request_body.interrupt_id: {"order_reference": request_body.order_reference}}),
            _thread_config(ticket_id),
        )
        return _build_ticket_detail_response(ticket_record, ticket_graph)


def create_app(
    *,
    ticket_graph: CompiledStateGraph | None = None,
    ticket_database_path: Path = TICKET_DATABASE_PATH,
    checkpoint_database_path: Path = CHECKPOINT_DATABASE_PATH,
    frontend_dist_path: Path = FRONTEND_DIST_PATH,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.ticket_database_path = ticket_database_path
        if ticket_graph is not None:
            application.state.ticket_graph = ticket_graph
            yield
            return

        checkpoint_database_path.parent.mkdir(parents=True, exist_ok=True)
        with SqliteSaver.from_conn_string(str(checkpoint_database_path)) as checkpointer:
            application.state.ticket_graph = build_ticket_graph(checkpointer=checkpointer)
            yield

    application = FastAPI(title="storekeeper operator API", lifespan=lifespan)
    # The app shares one SQLite checkpointer; run one worker. This lock also
    # rejects double-clicked resumes after the first request changes the state.
    application.state.ticket_graph_lock = RLock()
    application.include_router(router)
    if frontend_dist_path.is_dir():
        application.mount(
            "/",
            StaticFiles(directory=frontend_dist_path, html=True),
            name="operator-console",
        )
    return application


def _require_ticket(ticket_id: str, ticket_database_path: Path) -> TicketRecord:
    ticket_record = get_ticket(ticket_id, database_path=ticket_database_path)
    if ticket_record is None:
        raise HTTPException(
            status_code=404,
            detail=f"Ticket {ticket_id!r} was not found.",
        )
    return ticket_record


def _build_ticket_detail_response(
    ticket_record: TicketRecord,
    ticket_graph: CompiledStateGraph,
) -> TicketDetailResponse:
    state_snapshot = ticket_graph.get_state(
        _thread_config(ticket_record["ticket_id"])
    )
    state_values = state_snapshot.values
    tasks = _normalize_legacy_tasks(state_values.get("tasks", []))
    task_results = _normalize_legacy_task_results(
        state_values.get("task_results", []),
        tasks,
    )
    pending_approvals = _get_pending_approvals(
        state_snapshot,
        tasks,
        task_results,
    )
    task_order = {
        task["task_id"]: task_number
        for task_number, task in enumerate(tasks)
    }
    ordered_task_results = sorted(
        task_results,
        key=lambda task_result: task_order.get(
            task_result["task_id"],
            len(task_order),
        ),
    )
    return TicketDetailResponse(
        **ticket_record,
        status=derive_ticket_status(state_snapshot),
        pending_approvals=pending_approvals,
        pending_clarifications=_get_pending_clarifications(state_snapshot),
        clarification_history=state_values.get("clarification_history", []),
        tasks=tasks,
        task_results=ordered_task_results,
        reply_draft=state_values.get("reply_draft"),
        ticket_outcome=state_values.get("ticket_outcome"),
        escalation_reason=state_values.get("escalation_reason"),
    )


def _get_pending_approvals(
    state_snapshot: StateSnapshot,
    tasks: list[dict],
    task_results: list[dict],
) -> list[ApprovalPayloadResponse]:
    completed_task_ids = {
        task_result["task_id"] for task_result in task_results
    }
    pending_approvals = []
    for interrupt_number, pending_interrupt in enumerate(
        state_snapshot.interrupts,
        start=1,
    ):
        if is_clarification_payload(pending_interrupt.value):
            continue
        approval_payload = dict(pending_interrupt.value)
        approval_payload.setdefault(
            "task_id",
            _find_approval_task_id(approval_payload, tasks)
            or f"task-{interrupt_number}",
        )
        if approval_payload["task_id"] in completed_task_ids:
            continue
        pending_approvals.append(
            ApprovalPayloadResponse.model_validate(
                {
                    "interrupt_id": pending_interrupt.id,
                    **approval_payload,
                }
            )
        )
    return pending_approvals


def _get_pending_clarifications(state_snapshot: StateSnapshot) -> list[ClarificationPayloadResponse]:
    return [
        ClarificationPayloadResponse.model_validate(
            {**pending_interrupt.value, "interrupt_id": pending_interrupt.id}
        )
        for pending_interrupt in state_snapshot.interrupts
        if is_clarification_payload(pending_interrupt.value)
    ]


def _normalize_legacy_tasks(tasks: list[dict]) -> list[dict]:
    return [
        {
            **task,
            "task_id": task.get("task_id") or f"task-{task_number}",
        }
        for task_number, task in enumerate(tasks, start=1)
    ]


def _normalize_legacy_task_results(
    task_results: list[dict],
    tasks: list[dict],
) -> list[dict]:
    normalized_task_results = []
    for task_result_number, task_result in enumerate(task_results, start=1):
        result_task = dict(task_result["task"])
        fallback_task_id = (
            tasks[task_result_number - 1]["task_id"]
            if task_result_number <= len(tasks)
            else f"task-{task_result_number}"
        )
        task_id = (
            task_result.get("task_id")
            or result_task.get("task_id")
            or fallback_task_id
        )
        result_task["task_id"] = task_id
        normalized_task_results.append(
            {
                **task_result,
                "task_id": task_id,
                "task": result_task,
            }
        )
    return normalized_task_results


def _find_approval_task_id(
    approval_payload: dict,
    tasks: list[dict],
) -> str | None:
    for task in tasks:
        if (
            task["requested_action"] == approval_payload.get("action")
            and task["order_reference"] == approval_payload.get("requested_reference")
        ):
            return task["task_id"]
    if len(tasks) == 1:
        return tasks[0]["task_id"]
    return None


def _thread_config(ticket_id: str) -> dict:
    return {"configurable": {"thread_id": ticket_id}}


app = create_app()

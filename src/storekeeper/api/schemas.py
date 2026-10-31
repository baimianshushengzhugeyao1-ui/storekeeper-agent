"""Validated request and response models for the operator API."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from storekeeper.domain import Intent, RequestedAction, TaskOutcome
from storekeeper.graph.state import TicketOutcome
from storekeeper.graph.clarification import normalize_clarification_order_reference
from storekeeper.tickets import TicketStatus

NonEmptyText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
]


class CreateTicketRequest(BaseModel):
    ticket_text: NonEmptyText
    ticket_id: NonEmptyText | None = None


class TicketDecisionRequest(BaseModel):
    interrupt_id: NonEmptyText
    decision: Literal["approve", "reject"]


class TicketClarificationRequest(BaseModel):
    """Accept only the missing order reference, never a replacement action."""

    model_config = ConfigDict(extra="forbid")
    interrupt_id: NonEmptyText
    order_reference: NonEmptyText

    @field_validator("order_reference")
    @classmethod
    def validate_order_reference(cls, value: str) -> str:
        return normalize_clarification_order_reference(value)


class ShippingAddressResponse(BaseModel):
    first_name: str | None
    last_name: str | None
    company: str | None
    address1: str | None
    address2: str | None
    city: str | None
    province: str | None
    zip: str | None
    country: str | None
    phone: str | None


class TaskResponse(BaseModel):
    task_id: str
    intent: Intent
    order_reference: str | None
    requested_action: RequestedAction | None
    new_shipping_address: ShippingAddressResponse | None
    confidence: float


class GateVerdictResponse(BaseModel):
    passed: bool
    rule: str
    reason: str
    flags: list[str] = Field(default_factory=list)


class TaskResultResponse(BaseModel):
    task_id: str
    task: TaskResponse
    outcome: TaskOutcome
    gate_verdict: GateVerdictResponse | None
    action_result: dict[str, Any] | None
    policy_citations: list[str] = Field(default_factory=list)


class ApprovalPayloadResponse(BaseModel):
    interrupt_id: str
    task_id: str
    question: str
    action: RequestedAction
    order: str
    requested_reference: str | None
    amount: str
    gate_rule: str
    gate_reason: str
    flags: list[str] = Field(default_factory=list)
    current_shipping_address: ShippingAddressResponse | None
    new_shipping_address: ShippingAddressResponse | None


class TicketSummaryResponse(BaseModel):
    ticket_id: str
    ticket_text: str
    created_at: datetime
    status: TicketStatus


class ClarificationPayloadResponse(BaseModel):
    interrupt_id: str
    kind: Literal["clarification"]
    task_id: str
    field: Literal["order_reference"]
    question: str


class ClarificationHistoryResponse(BaseModel):
    task_id: str
    field: Literal["order_reference"]
    value: str


class TicketDetailResponse(TicketSummaryResponse):
    pending_approvals: list[ApprovalPayloadResponse] = Field(default_factory=list)
    pending_clarifications: list[ClarificationPayloadResponse] = Field(default_factory=list)
    clarification_history: list[ClarificationHistoryResponse] = Field(default_factory=list)
    tasks: list[TaskResponse] = Field(default_factory=list)
    task_results: list[TaskResultResponse] = Field(default_factory=list)
    reply_draft: str | None
    ticket_outcome: TicketOutcome | None
    escalation_reason: str | None

"""Ticket classification: turns customer text into validated domain Tasks."""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openrouter import ChatOpenRouter
from pydantic import BaseModel, Field

from storekeeper.config import load_classifier_settings
from storekeeper.domain import Intent, RequestedAction, ShippingAddress, Task

# The policy gate consumes requested_action, but its value follows mechanically
# from the intent, so code derives it here. The model is never asked to pick an
# action — one less thing a ticket can talk the system into.
INTENT_TO_REQUESTED_ACTION: dict[Intent, RequestedAction | None] = {
    "cancel_order": "cancel_order",
    "refund_request": "issue_refund",
    "address_change": "update_shipping_address",
    "policy_question": None,
    "other": None,
}

CLASSIFIER_SYSTEM_PROMPT = """\
You classify customer support tickets for a Shopify store.

Read the customer's message and extract every distinct request as a task.

Intents:
- cancel_order: the customer wants an order cancelled.
- refund_request: the customer wants money back for an order (refund or return).
- address_change: the customer wants the shipping address on an order changed.
- policy_question: the customer asks about store policy (returns, shipping,
  warranty) without asking for an action on a specific order.
- other: anything that fits none of the intents above, or is too unclear to
  classify.

Rules:
- Create one task per distinct request; most tickets contain exactly one.
- Copy the order reference exactly as the customer wrote it, keeping the
  leading #. Use null when the request names no specific order.
- Never invent an order reference that is not in the message.
- For address_change, extract the new shipping address into its structured
  fields. Use null for every field the customer did not explicitly provide;
  never infer or copy missing address details.
- For every other intent, new_shipping_address must be null.
- Confidence is your certainty in the chosen intent, from 0.0 to 1.0. Use a
  low value when the message is ambiguous.
"""


class ClassifiedShippingAddress(BaseModel):
    """Shipping-address fields extracted only from the customer's message."""

    first_name: str | None = Field(description="Recipient first name, or null if omitted.")
    last_name: str | None = Field(description="Recipient last name, or null if omitted.")
    company: str | None = Field(description="Company name, or null if omitted.")
    address1: str | None = Field(description="Street address, or null if omitted.")
    address2: str | None = Field(
        description="Apartment, suite, or unit, or null if omitted."
    )
    city: str | None = Field(description="City, or null if omitted.")
    province: str | None = Field(
        description="State, province, or region, or null if omitted."
    )
    zip: str | None = Field(description="ZIP or postal code, or null if omitted.")
    country: str | None = Field(description="Country, or null if omitted.")
    phone: str | None = Field(description="Recipient phone number, or null if omitted.")


class ClassifiedTask(BaseModel):
    """One customer request extracted from a support ticket."""

    intent: Intent = Field(
        description="The kind of request the customer is making."
    )
    order_reference: str | None = Field(
        description=(
            "The order number exactly as the customer wrote it, for example "
            "'#1036'. Null when the request names no specific order."
        )
    )
    new_shipping_address: ClassifiedShippingAddress | None = Field(
        description=(
            "The requested new address for address_change, containing only details "
            "the customer supplied. Null for every other intent."
        )
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Certainty in the chosen intent, from 0.0 to 1.0.",
    )


class TicketClassification(BaseModel):
    """Every distinct customer request found in one ticket."""

    tasks: list[ClassifiedTask] = Field(
        description=(
            "One entry per distinct request in the ticket, in the order the "
            "requests appear."
        )
    )


def convert_classification_to_tasks(classification: TicketClassification) -> list[Task]:
    domain_tasks: list[Task] = []
    for task_number, classified_task in enumerate(classification.tasks, start=1):
        new_shipping_address: ShippingAddress | None = None
        if classified_task.new_shipping_address is not None:
            new_shipping_address = ShippingAddress(
                **classified_task.new_shipping_address.model_dump()
            )
        domain_tasks.append(
            Task(
                task_id=f"task-{task_number}",
                intent=classified_task.intent,
                order_reference=classified_task.order_reference,
                requested_action=INTENT_TO_REQUESTED_ACTION[classified_task.intent],
                new_shipping_address=new_shipping_address,
                confidence=classified_task.confidence,
            )
        )
    return domain_tasks


def classify_ticket(ticket_text: str, classifier_model: BaseChatModel | None = None) -> list[Task]:
    if not ticket_text.strip():
        raise ValueError("Ticket text cannot be empty.")

    if classifier_model is None:
        classifier_settings = load_classifier_settings()
        classifier_model = ChatOpenRouter(model=classifier_settings.openrouter_model)

    structured_classifier = classifier_model.with_structured_output(TicketClassification)
    classification = structured_classifier.invoke(
        [
            SystemMessage(content=CLASSIFIER_SYSTEM_PROMPT),
            HumanMessage(content=ticket_text),
        ]
    )
    return convert_classification_to_tasks(classification)

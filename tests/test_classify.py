import unittest

from pydantic import ValidationError

from storekeeper.classify import (
    ClassifiedShippingAddress,
    ClassifiedTask,
    TicketClassification,
    classify_ticket,
    convert_classification_to_tasks,
)
from storekeeper.domain import Intent, RequestedAction


class FakeClassifierModel:
    """Stands in for ChatOpenRouter; returns a preset classification."""

    def __init__(self, classification: TicketClassification):
        self.classification = classification
        self.requested_schema: type | None = None
        self.received_messages: list | None = None

    def with_structured_output(self, schema: type) -> "FakeClassifierModel":
        self.requested_schema = schema
        return self

    def invoke(self, messages: list) -> TicketClassification:
        self.received_messages = messages
        return self.classification


def make_complete_classified_shipping_address() -> ClassifiedShippingAddress:
    return ClassifiedShippingAddress(
        first_name=None,
        last_name=None,
        company=None,
        address1="20 Lake Road",
        address2="Apartment 4B",
        city="Dhaka",
        province="Dhaka",
        zip="1205",
        country="Bangladesh",
        phone=None,
    )


def make_classification(
    intent: Intent = "cancel_order",
    order_reference: str | None = "#1036",
    new_shipping_address: ClassifiedShippingAddress | None = None,
) -> TicketClassification:
    return TicketClassification(
        tasks=[
            ClassifiedTask(
                intent=intent,
                order_reference=order_reference,
                new_shipping_address=new_shipping_address,
                confidence=0.95,
            )
        ]
    )


class ClassifyTicketTests(unittest.TestCase):
    def test_classifies_ticket_into_domain_task(self) -> None:
        fake_model = FakeClassifierModel(make_classification())

        tasks = classify_ticket(
            "Please cancel order #1036. I ordered it by mistake.",
            classifier_model=fake_model,
        )

        self.assertEqual(fake_model.requested_schema, TicketClassification)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["intent"], "cancel_order")
        self.assertEqual(tasks[0]["order_reference"], "#1036")
        self.assertEqual(tasks[0]["requested_action"], "cancel_order")
        self.assertIsNone(tasks[0]["new_shipping_address"])
        self.assertEqual(tasks[0]["confidence"], 0.95)

    def test_ticket_text_reaches_the_model(self) -> None:
        fake_model = FakeClassifierModel(make_classification())
        ticket_text = "Please cancel order #1036."

        classify_ticket(ticket_text, classifier_model=fake_model)

        assert fake_model.received_messages is not None
        message_contents = [message.content for message in fake_model.received_messages]
        self.assertIn(ticket_text, message_contents)

    def test_empty_ticket_text_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            classify_ticket("   ")


class ConvertClassificationTests(unittest.TestCase):
    def test_action_intents_derive_their_requested_action(self) -> None:
        intent_to_expected_action: dict[Intent, RequestedAction] = {
            "cancel_order": "cancel_order",
            "refund_request": "issue_refund",
            "address_change": "update_shipping_address",
        }

        for intent, expected_action in intent_to_expected_action.items():
            with self.subTest(intent=intent):
                tasks = convert_classification_to_tasks(make_classification(intent=intent))
                self.assertEqual(tasks[0]["requested_action"], expected_action)

    def test_non_action_intents_have_no_requested_action(self) -> None:
        non_action_intents: list[Intent] = ["policy_question", "other"]
        for intent in non_action_intents:
            with self.subTest(intent=intent):
                tasks = convert_classification_to_tasks(
                    make_classification(intent=intent, order_reference=None)
                )
                self.assertIsNone(tasks[0]["requested_action"])

    def test_multiple_tasks_keep_their_order(self) -> None:
        classification = TicketClassification(
            tasks=[
                ClassifiedTask(
                    intent="cancel_order",
                    order_reference="#1023",
                    new_shipping_address=None,
                    confidence=0.9,
                ),
                ClassifiedTask(
                    intent="policy_question",
                    order_reference=None,
                    new_shipping_address=None,
                    confidence=0.8,
                ),
            ]
        )

        tasks = convert_classification_to_tasks(classification)

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["intent"], "cancel_order")
        self.assertEqual(tasks[1]["intent"], "policy_question")

    def test_address_change_keeps_the_structured_new_address(self) -> None:
        tasks = convert_classification_to_tasks(
            make_classification(
                intent="address_change",
                new_shipping_address=make_complete_classified_shipping_address(),
            )
        )

        assert tasks[0]["new_shipping_address"] is not None
        self.assertEqual(tasks[0]["new_shipping_address"]["address1"], "20 Lake Road")
        self.assertEqual(tasks[0]["new_shipping_address"]["country"], "Bangladesh")


class ClassificationSchemaTests(unittest.TestCase):
    def test_unknown_intent_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TicketClassification.model_validate(
                {
                    "tasks": [
                        {
                            "intent": "delete_store",
                            "order_reference": None,
                            "new_shipping_address": None,
                            "confidence": 0.9,
                        }
                    ]
                }
            )

    def test_confidence_outside_range_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            TicketClassification.model_validate(
                {
                    "tasks": [
                        {
                            "intent": "cancel_order",
                            "order_reference": "#1036",
                            "new_shipping_address": None,
                            "confidence": 1.5,
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()

import unittest
from pathlib import Path
from unittest.mock import patch

from storekeeper.domain import Intent, Task
from storekeeper.policy_docs import (
    POLICY_DOCS_DIRECTORY,
    chunk_policy_document,
    find_policy_context,
)


def make_task(intent: Intent = "refund_request") -> Task:
    return {
        "intent": intent,
        "order_reference": "#1036",
        "requested_action": None,
        "new_shipping_address": None,
        "confidence": 0.9,
    }


class FindPolicyContextTests(unittest.TestCase):
    def test_refund_intent_gets_the_returns_document(self) -> None:
        policy_extracts = find_policy_context(make_task("refund_request"))

        self.assertEqual(len(policy_extracts), 1)
        self.assertEqual(policy_extracts[0]["document_name"], "returns-and-refunds.md")
        self.assertIn("30 days", policy_extracts[0]["document_text"])

    def test_policy_question_gets_the_whole_corpus(self) -> None:
        policy_extracts = find_policy_context(make_task("policy_question"))

        document_names = [extract["document_name"] for extract in policy_extracts]
        self.assertEqual(
            document_names,
            [
                "address-changes.md",
                "cancellations.md",
                "returns-and-refunds.md",
                "shipping.md",
                "warranty.md",
            ],
        )

    @patch("storekeeper.policy_docs.search_policy_chunks")
    def test_policy_question_with_ticket_text_uses_chunk_search(
        self, mock_search_policy_chunks
    ) -> None:
        mock_search_policy_chunks.return_value = [
            {
                "chunk_id": "warranty.md#coverage",
                "document_name": "warranty.md",
                "heading": "Coverage",
                "document_text": "# Warranty\n\n## Coverage\n\nTwelve months.",
                "distance": 0.12,
            }
        ]

        policy_extracts = find_policy_context(
            make_task("policy_question"), ticket_text="How long is the warranty?"
        )

        mock_search_policy_chunks.assert_called_once_with("How long is the warranty?")
        self.assertEqual(
            policy_extracts,
            [
                {
                    "document_name": "warranty.md",
                    "document_text": "# Warranty\n\n## Coverage\n\nTwelve months.",
                }
            ],
        )

    @patch("storekeeper.policy_docs.search_policy_chunks")
    def test_action_intent_keeps_whole_document_when_ticket_text_is_present(
        self, mock_search_policy_chunks
    ) -> None:
        policy_extracts = find_policy_context(
            make_task("refund_request"), ticket_text="Refund order #1036."
        )

        mock_search_policy_chunks.assert_not_called()
        self.assertEqual(len(policy_extracts), 1)
        self.assertEqual(policy_extracts[0]["document_name"], "returns-and-refunds.md")
        self.assertIn("30 days", policy_extracts[0]["document_text"])

    def test_corpus_numbers_match_the_gate(self) -> None:
        # The gate enforces 30 days / $100; the customer-facing docs must say
        # the same, or replies will contradict decisions.
        returns_text = (POLICY_DOCS_DIRECTORY / "returns-and-refunds.md").read_text(encoding="utf-8")
        self.assertIn("30 days", returns_text)
        self.assertIn("$100", returns_text)

    def test_missing_directory_raises(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "not found"):
            find_policy_context(make_task(), policy_docs_directory=Path("does/not/exist"))


class ChunkPolicyDocumentTests(unittest.TestCase):
    def test_splits_level_two_sections_into_stable_chunks(self) -> None:
        document_text = """\
# Returns & Refunds

Intro text is outside the indexed sections.

## Refund Window

Requests qualify for 30 days.

## High-value orders

Orders over $100 receive more review.
"""

        policy_chunks = chunk_policy_document("returns-and-refunds.md", document_text)

        self.assertEqual(
            [policy_chunk["chunk_id"] for policy_chunk in policy_chunks],
            [
                "returns-and-refunds.md#refund-window",
                "returns-and-refunds.md#high-value-orders",
            ],
        )
        self.assertEqual(policy_chunks[0]["heading"], "Refund Window")
        self.assertIn("# Returns & Refunds", policy_chunks[0]["document_text"])
        self.assertIn("Requests qualify for 30 days.", policy_chunks[0]["document_text"])
        self.assertNotIn("High-value orders", policy_chunks[0]["document_text"])

    def test_duplicate_heading_ids_are_rejected(self) -> None:
        document_text = """\
# Warranty

## Coverage

First section.

## Coverage

Second section.
"""

        with self.assertRaisesRegex(ValueError, "duplicate heading id"):
            chunk_policy_document("warranty.md", document_text)


if __name__ == "__main__":
    unittest.main()

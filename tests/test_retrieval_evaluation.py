import json
import unittest
from copy import deepcopy
from pathlib import Path

from storekeeper.policy_docs import load_policy_chunks
from storekeeper.retrieval_evaluation import score_retrieval, summarize_retrieval, validate_dataset


class RetrievalEvaluationTests(unittest.TestCase):
    def test_precision_and_recall_have_different_denominators(self):
        score = score_retrieval(["a", "b", "c"], ["a", "d"])
        self.assertAlmostEqual(score["precision"], 1 / 3)
        self.assertEqual(score["recall"], 0.5)
        self.assertEqual(score["missing_ids"], ["d"])
        self.assertFalse(score["all_references_found"])

    def test_no_results_for_answerable_question_is_zero_not_skipped(self):
        score = score_retrieval([], ["a"])
        self.assertEqual(score["precision"], 0)
        self.assertEqual(score["recall"], 0)

    def test_unanswerable_recall_is_undefined_not_perfect(self):
        score = score_retrieval(["a", "b", "c"], [])
        self.assertEqual(score["precision"], 0)
        self.assertIsNone(score["recall"])
        self.assertIsNone(summarize_retrieval([score])["macro_recall_at_3"])

    def test_duplicate_retrieved_ids_cannot_inflate_scores(self):
        with self.assertRaises(ValueError):
            score_retrieval(["a", "a"], ["a"])

    def test_macro_and_micro_recall_remain_distinct(self):
        rows = [score_retrieval(["a", "x", "y"], ["a"]),
                score_retrieval(["b", "x", "y"], ["b", "c"])]
        summary = summarize_retrieval(rows)
        self.assertEqual(summary["macro_recall_at_3"], 0.75)
        self.assertAlmostEqual(summary["micro_recall_at_3"], 2 / 3)
        self.assertEqual(summary["all_evidence_rate_at_3"], 0.5)

    def test_reference_validation_catches_missing_chunk(self):
        dataset = {"top_k":3,"cases":[{"id":"a","group":"english_answerable","question":"Q?","reference_context_ids":["missing"]}]}
        with self.assertRaises(ValueError):
            validate_dataset(dataset, {"present"})

    def test_frozen_dataset_covers_corpus_and_keeps_language_groups_separate(self):
        root = Path(__file__).resolve().parents[1]
        dataset = json.loads((root / "evaluation/retrieval_v1.json").read_text(encoding="utf-8"))
        known = {chunk["chunk_id"] for chunk in load_policy_chunks()}
        validate_dataset(dataset, known)
        english = [case for case in dataset["cases"] if case["group"] == "english_answerable"]
        self.assertEqual(len(english), 40)
        self.assertEqual({ref for case in english for ref in case["reference_context_ids"]}, known)
        broken = deepcopy(dataset)
        next(case for case in broken["cases"] if case["group"] == "chinese_stress")["reference_context_ids"] = ["shipping.md#tracking"]
        with self.assertRaises(ValueError):
            validate_dataset(broken, known)


if __name__ == "__main__":
    unittest.main()

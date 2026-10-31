"""Exact chunk-ID retrieval metrics, independent of an LLM judge."""

from statistics import mean


def score_retrieval(retrieved_ids: list[str], reference_ids: list[str]) -> dict:
    if len(retrieved_ids) != len(set(retrieved_ids)):
        raise ValueError("Duplicate retrieved IDs would distort the precision denominator.")
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("Reference IDs must be unique.")
    references = set(reference_ids)
    hits = [chunk_id for chunk_id in retrieved_ids if chunk_id in references]
    first_hit_rank = next((rank for rank, chunk_id in enumerate(retrieved_ids, 1)
                           if chunk_id in references), None)
    return {
        "relevant_retrieved": len(hits),
        "retrieved_count": len(retrieved_ids),
        "reference_count": len(references),
        "precision": len(hits) / len(retrieved_ids) if retrieved_ids else 0.0,
        "recall": len(hits) / len(references) if references else None,
        "any_hit": bool(hits) if references else None,
        "all_references_found": references.issubset(retrieved_ids) if references else None,
        "reciprocal_rank": 1 / first_hit_rank if first_hit_rank else 0.0,
        "missing_ids": sorted(references - set(retrieved_ids)),
        "irrelevant_ids": [chunk_id for chunk_id in retrieved_ids if chunk_id not in references],
    }


def summarize_retrieval(rows: list[dict]) -> dict:
    if not rows:
        raise ValueError("Cannot summarize an empty evaluation group.")
    answerable = [row for row in rows if row["reference_count"] > 0]
    reference_count = sum(row["reference_count"] for row in rows)
    retrieved_count = sum(row["retrieved_count"] for row in rows)
    relevant_retrieved = sum(row["relevant_retrieved"] for row in rows)
    return {
        "cases": len(rows),
        "query_failures": sum("error_type" in row for row in rows),
        "relevant_retrieved": relevant_retrieved,
        "retrieved_count": retrieved_count,
        "reference_count": reference_count,
        "macro_precision_at_3": mean(row["precision"] for row in rows),
        "macro_recall_at_3": mean(row["recall"] for row in answerable) if answerable else None,
        "micro_precision_at_3": relevant_retrieved / retrieved_count if retrieved_count else 0.0,
        "micro_recall_at_3": relevant_retrieved / reference_count if reference_count else None,
        "hit_rate_at_3": mean(row["any_hit"] for row in answerable) if answerable else None,
        "all_evidence_rate_at_3": mean(row["all_references_found"] for row in answerable) if answerable else None,
        "mrr_at_3": mean(row["reciprocal_rank"] for row in answerable) if answerable else None,
        "queries_returning_context": sum(row["retrieved_count"] > 0 for row in rows),
    }


def validate_dataset(dataset: dict, known_chunk_ids: set[str]) -> None:
    if dataset["top_k"] != 3:
        raise ValueError("This evaluation matches the production top-three retrieval contract.")
    cases = dataset["cases"]
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("Case IDs must be unique.")
    if len({case["question"] for case in cases}) != len(cases):
        raise ValueError("Questions must be unique.")
    cases_by_id = {case["id"]: case for case in cases}
    for case in cases:
        references = case["reference_context_ids"]
        if not case["question"].strip():
            raise ValueError("Questions cannot be empty.")
        if len(references) != len(set(references)) or not set(references) <= known_chunk_ids:
            raise ValueError(f"Invalid reference IDs in {case['id']}.")
        if (case["group"] == "unanswerable") != (not references):
            raise ValueError(f"Only unanswerable cases can have empty references: {case['id']}.")
        if case["group"] not in ("english_answerable", "chinese_stress", "unanswerable"):
            raise ValueError("Unknown evaluation group.")
        if case["group"] == "chinese_stress":
            paired = cases_by_id[case["paired_english_id"]]
            if paired["group"] != "english_answerable" or paired["reference_context_ids"] != references:
                raise ValueError(f"Chinese pair labels differ for {case['id']}.")

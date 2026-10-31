"""Freeze evidence, run the original Chroma retrieval, and score exact chunk IDs.

No chat model, API key, Shopify, or paid LLM judge is used. First run downloads
the existing Chroma default embedding model into this project's var/ cache.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from storekeeper.policy_docs import (
    POLICY_DOCS_DIRECTORY, load_policy_chunks, rebuild_policy_index, search_policy_chunks,
)
from storekeeper.retrieval_evaluation import score_retrieval, summarize_retrieval, validate_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = PROJECT_ROOT / "evaluation/retrieval_v1.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze-only", action="store_true", help="Save hashes and evidence before running retrieval.")
    parser.add_argument("--run-directory", type=Path)
    arguments = parser.parse_args()
    dataset_bytes = DEFAULT_DATASET.read_bytes()
    dataset = json.loads(dataset_bytes)
    chunks = load_policy_chunks()
    validate_dataset(dataset, {chunk["chunk_id"] for chunk in chunks})
    manifest = {
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "policy_file_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                               for path in sorted(POLICY_DOCS_DIRECTORY.glob("*.md"))},
        "retriever_sha256": hashlib.sha256((PROJECT_ROOT / "src/storekeeper/policy_docs.py").read_bytes()).hexdigest(),
        "chunks": chunks,
        "dataset": dataset,
    }
    run_directory = arguments.run_directory or PROJECT_ROOT / "var/evaluations" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ_retrieval_v1")
    run_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = run_directory / "frozen_manifest.json"
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        if any(existing[key] != manifest[key] for key in manifest):
            raise ValueError("Frozen data, labels, corpus or retriever changed. Use a new version/run.")
    else:
        manifest["frozen_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Frozen manifest: {manifest_path}", flush=True)
    if arguments.freeze_only:
        return 0
    if (run_directory / "report.json").exists():
        raise FileExistsError("Completed run already exists; do not overwrite historical scores.")

    os.environ["ANONYMIZED_TELEMETRY"] = "False"
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = PROJECT_ROOT / "var/embedding_cache/all-MiniLM-L6-v2"
    report = {
        "status": "running", "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "manifest": str(manifest_path), "dataset_sha256": manifest["dataset_sha256"],
        "configuration": {"top_k": 3, "chunking": "level-two Markdown headings", "distance": "cosine",
                          "embedding": "all-MiniLM-L6-v2 (Chroma DefaultEmbeddingFunction)",
                          "embedding_archive_sha256": ONNXMiniLM_L6_V2._MODEL_SHA256,
                          "chromadb_version": importlib.metadata.version("chromadb"),
                          "scorer": "exact chunk ID set intersection; Ragas ID-based formulas, no Ragas package or LLM judge"},
        "scope": "Locally authored synthetic retrieval-only baseline. No answer generation, end-to-end Agent accuracy, production users or retriever optimization. Language and unanswerable groups are separate.",
        "cases": [],
    }
    report_path = run_directory / "report.json"
    def save():
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        started = time.perf_counter()
        index_directory = run_directory / "chroma_index"
        report["indexed_chunks"] = rebuild_policy_index(policy_index_directory=index_directory)
        report["index_build_seconds"] = round(time.perf_counter() - started, 3)
        report["embedding_onnx_sha256"] = hashlib.sha256((ONNXMiniLM_L6_V2.DOWNLOAD_PATH / "onnx/model.onnx").read_bytes()).hexdigest()
        search_policy_chunks("Store policy warm-up", policy_index_directory=index_directory)
        for case in dataset["cases"]:
            started = time.perf_counter()
            row = dict(case)
            try:
                results = search_policy_chunks(case["question"], result_count=3, policy_index_directory=index_directory)
                row["retrieved_contexts"] = results
                row.update(score_retrieval([result["chunk_id"] for result in results], case["reference_context_ids"]))
            except Exception as error:
                row.update(score_retrieval([], case["reference_context_ids"]))
                row.update(error_type=type(error).__name__, error_message=str(error))
            row["query_seconds"] = round(time.perf_counter() - started, 4)
            report["cases"].append(row)
            print(json.dumps({"id":case["id"], "precision":row["precision"], "recall":row["recall"]}), flush=True)
            save()
        report["summary"] = {group: summarize_retrieval([row for row in report["cases"] if row["group"] == group])
                             for group in ("english_answerable", "chinese_stress", "unanswerable")}
        report["status"] = "complete" if not any("error_type" in row for row in report["cases"]) else "completed_with_query_failures"
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error_message=str(error))
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({"status":report["status"], "summary":report.get("summary"), "report":str(report_path)}, ensure_ascii=True), flush=True)
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())

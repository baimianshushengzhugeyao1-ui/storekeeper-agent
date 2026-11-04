"""Rebuild the local Chroma index from the markdown policy corpus."""

from storekeeper.policy_docs import POLICY_INDEX_DIRECTORY, rebuild_policy_index
from pathlib import Path


def main() -> None:
    # Reuse the project-local model cache used by the retrieval evaluators.
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH = Path(__file__).resolve().parents[1] / 'var/embedding_cache/all-MiniLM-L6-v2'
    indexed_chunk_count = rebuild_policy_index()
    print(f"Indexed {indexed_chunk_count} policy chunks.")
    print(f"Index: {POLICY_INDEX_DIRECTORY}")


if __name__ == "__main__":
    main()

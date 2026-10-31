"""Rebuild the local Chroma index from the markdown policy corpus."""

from storekeeper.policy_docs import POLICY_INDEX_DIRECTORY, rebuild_policy_index


def main() -> None:
    indexed_chunk_count = rebuild_policy_index()
    print(f"Indexed {indexed_chunk_count} policy chunks.")
    print(f"Index: {POLICY_INDEX_DIRECTORY}")


if __name__ == "__main__":
    main()

"""Store policy documents: the retrieval seam between the corpus and the LLM.

Policy questions retrieve relevant heading chunks from a local Chroma index.
Action intents still read their mapped whole document so policy-denial citations
remain deterministic. Policy text feeds answering and drafting LLMs only,
never the policy gate.
"""

import re
from pathlib import Path
from typing import TypedDict

from storekeeper.domain import Intent, Task

# Repo-root paths, resolved relative to this file so scripts work from any
# working directory.
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
POLICY_DOCS_DIRECTORY = REPOSITORY_ROOT / "policies"
POLICY_INDEX_DIRECTORY = REPOSITORY_ROOT / "var" / "policy_index"
POLICY_COLLECTION_NAME = "storekeeper-policies"
POLICY_SEARCH_RESULT_COUNT = 3

LEVEL_ONE_HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
LEVEL_TWO_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class PolicyExtract(TypedDict):
    document_name: str
    document_text: str


class PolicyChunk(TypedDict):
    chunk_id: str
    document_name: str
    heading: str
    document_text: str


class PolicySearchResult(TypedDict):
    chunk_id: str
    document_name: str
    heading: str
    document_text: str
    distance: float


# Which whole documents matter for each action intent. None means the whole
# corpus when no policy-question text is available.
INTENT_TO_POLICY_DOCUMENT_NAMES: dict[Intent, list[str] | None] = {
    "cancel_order": ["cancellations.md"],
    "refund_request": ["returns-and-refunds.md"],
    "address_change": ["address-changes.md"],
    "policy_question": None,
    "other": None,
}


def chunk_policy_document(document_name: str, document_text: str) -> list[PolicyChunk]:
    """Split one markdown policy into chunks at each level-two heading."""
    document_title_match = LEVEL_ONE_HEADING_PATTERN.search(document_text)
    if document_title_match is None:
        raise ValueError(f"Policy document {document_name} needs a level-one title.")
    document_title = document_title_match.group(1).strip()

    heading_matches = list(LEVEL_TWO_HEADING_PATTERN.finditer(document_text))
    if not heading_matches:
        raise ValueError(f"Policy document {document_name} needs a level-two heading.")

    policy_chunks: list[PolicyChunk] = []
    seen_chunk_ids: set[str] = set()
    for heading_number, heading_match in enumerate(heading_matches):
        heading = heading_match.group(1).strip()
        body_start = heading_match.end()
        body_end = (
            heading_matches[heading_number + 1].start()
            if heading_number + 1 < len(heading_matches)
            else len(document_text)
        )
        heading_body = document_text[body_start:body_end].strip()
        chunk_id = f"{document_name}#{_slugify_heading(heading)}"
        if chunk_id in seen_chunk_ids:
            raise ValueError(
                f"Policy document {document_name} has duplicate heading id {chunk_id}."
            )
        seen_chunk_ids.add(chunk_id)

        chunk_text = f"# {document_title}\n\n## {heading}"
        if heading_body:
            chunk_text = f"{chunk_text}\n\n{heading_body}"
        policy_chunks.append(
            {
                "chunk_id": chunk_id,
                "document_name": document_name,
                "heading": heading,
                "document_text": chunk_text,
            }
        )
    return policy_chunks


def load_policy_chunks(
    policy_docs_directory: Path | None = None,
) -> list[PolicyChunk]:
    directory = (
        policy_docs_directory if policy_docs_directory is not None else POLICY_DOCS_DIRECTORY
    )
    document_paths = _get_policy_document_paths(directory)

    policy_chunks: list[PolicyChunk] = []
    for document_path in document_paths:
        policy_chunks.extend(
            chunk_policy_document(
                document_name=document_path.name,
                document_text=document_path.read_text(encoding="utf-8"),
            )
        )
    return policy_chunks


def rebuild_policy_index(
    policy_docs_directory: Path | None = None,
    policy_index_directory: Path | None = None,
) -> int:
    """Replace the Chroma policy collection with current heading chunks."""
    import chromadb
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    policy_chunks = load_policy_chunks(policy_docs_directory)
    index_directory = (
        policy_index_directory
        if policy_index_directory is not None
        else POLICY_INDEX_DIRECTORY
    )
    chroma_client = chromadb.PersistentClient(path=str(index_directory))

    collection_exists = any(
        collection.name == POLICY_COLLECTION_NAME
        for collection in chroma_client.list_collections()
    )
    if collection_exists:
        chroma_client.delete_collection(POLICY_COLLECTION_NAME)

    policy_collection = chroma_client.create_collection(
        name=POLICY_COLLECTION_NAME,
        embedding_function=DefaultEmbeddingFunction(),
        configuration={"hnsw": {"space": "cosine"}},
    )
    policy_collection.add(
        ids=[policy_chunk["chunk_id"] for policy_chunk in policy_chunks],
        documents=[policy_chunk["document_text"] for policy_chunk in policy_chunks],
        metadatas=[
            {
                "document_name": policy_chunk["document_name"],
                "heading": policy_chunk["heading"],
            }
            for policy_chunk in policy_chunks
        ],
    )
    return len(policy_chunks)


def search_policy_chunks(
    question: str,
    result_count: int = POLICY_SEARCH_RESULT_COUNT,
    policy_index_directory: Path | None = None,
) -> list[PolicySearchResult]:
    """Return the nearest indexed policy chunks with cosine distances."""
    import chromadb
    from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

    if not question.strip():
        raise ValueError("Policy search question cannot be empty.")
    if result_count <= 0:
        raise ValueError("Policy search result_count must be greater than zero.")

    index_directory = (
        policy_index_directory
        if policy_index_directory is not None
        else POLICY_INDEX_DIRECTORY
    )
    if not index_directory.is_dir():
        raise FileNotFoundError(
            f"Policy index not found at {index_directory}. "
            "Run scripts/index_policies.py first."
        )

    chroma_client = chromadb.PersistentClient(path=str(index_directory))
    collection_exists = any(
        collection.name == POLICY_COLLECTION_NAME
        for collection in chroma_client.list_collections()
    )
    if not collection_exists:
        raise FileNotFoundError(
            f"Policy collection {POLICY_COLLECTION_NAME!r} not found at "
            f"{index_directory}. Run scripts/index_policies.py first."
        )

    policy_collection = chroma_client.get_collection(
        name=POLICY_COLLECTION_NAME,
        embedding_function=DefaultEmbeddingFunction(),
    )
    indexed_chunk_count = policy_collection.count()
    if indexed_chunk_count == 0:
        raise RuntimeError("Policy index contains no chunks. Rebuild it before searching.")

    query_result = policy_collection.query(
        query_texts=[question],
        n_results=min(result_count, indexed_chunk_count),
        include=["documents", "metadatas", "distances"],
    )
    documents = query_result["documents"]
    metadatas = query_result["metadatas"]
    distances = query_result["distances"]
    if documents is None or metadatas is None or distances is None:
        raise RuntimeError("Policy search did not return documents, metadata, and distances.")

    policy_search_results: list[PolicySearchResult] = []
    for chunk_id, document_text, metadata, distance in zip(
        query_result["ids"][0],
        documents[0],
        metadatas[0],
        distances[0],
    ):
        if document_text is None or metadata is None:
            raise RuntimeError(f"Policy search returned incomplete chunk {chunk_id}.")
        policy_search_results.append(
            {
                "chunk_id": chunk_id,
                "document_name": str(metadata["document_name"]),
                "heading": str(metadata["heading"]),
                "document_text": document_text,
                "distance": float(distance),
            }
        )
    return policy_search_results


def find_policy_context(
    task: Task,
    ticket_text: str | None = None,
    policy_docs_directory: Path | None = None,
) -> list[PolicyExtract]:
    directory = (
        policy_docs_directory if policy_docs_directory is not None else POLICY_DOCS_DIRECTORY
    )

    if task["intent"] == "policy_question" and ticket_text is not None:
        return [
            {
                "document_name": search_result["document_name"],
                "document_text": search_result["document_text"],
            }
            for search_result in search_policy_chunks(ticket_text)
        ]

    wanted_document_names = INTENT_TO_POLICY_DOCUMENT_NAMES[task["intent"]]
    if wanted_document_names is None:
        document_paths = _get_policy_document_paths(directory)
    else:
        if not directory.is_dir():
            raise FileNotFoundError(f"Policy corpus directory not found: {directory}")
        document_paths = [directory / document_name for document_name in wanted_document_names]

    policy_extracts: list[PolicyExtract] = [
        {
            "document_name": document_path.name,
            "document_text": document_path.read_text(encoding="utf-8"),
        }
        for document_path in document_paths
    ]
    if not policy_extracts:
        raise FileNotFoundError(f"No policy documents found in {directory}")
    return policy_extracts


def format_policy_extracts(policy_extracts: list[PolicyExtract]) -> str:
    """Render extracts as one prompt block, each headed by its file name."""
    return "\n\n".join(
        f"[{extract['document_name']}]\n{extract['document_text']}"
        for extract in policy_extracts
    )


def _get_policy_document_paths(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"Policy corpus directory not found: {directory}")
    document_paths = sorted(directory.glob("*.md"))
    if not document_paths:
        raise FileNotFoundError(f"No policy documents found in {directory}")
    return document_paths


def _slugify_heading(heading: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")
    return slug if slug else "section"

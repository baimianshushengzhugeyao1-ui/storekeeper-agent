"""Search the local policy index and show the nearest heading chunks."""

import argparse

from storekeeper.policy_docs import search_policy_chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", help="Customer policy question to search for")
    arguments = parser.parse_args()

    policy_search_results = search_policy_chunks(arguments.question)
    print("Cosine distance: lower means more similar.\n")
    for result_number, search_result in enumerate(policy_search_results, start=1):
        print(
            f"{result_number}. {search_result['document_name']} "
            f"-- {search_result['heading']} "
            f"(distance {search_result['distance']:.4f})"
        )
        for document_line in search_result["document_text"].splitlines():
            print(f"   {document_line}")
        print()


if __name__ == "__main__":
    main()

"""Classify one support ticket from the command line."""

import argparse
import json

from storekeeper.classify import classify_ticket


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ticket_text", help='The customer message, quoted, e.g. "Please cancel order #1036."')
    arguments = parser.parse_args()

    tasks = classify_ticket(arguments.ticket_text)

    print(f"Tasks found: {len(tasks)}")
    for task_number, task in enumerate(tasks, start=1):
        print(f"\nTask {task_number}:")
        print(f"  Intent: {task['intent']}")
        print(f"  Order reference: {task['order_reference'] or '(none)'}")
        print(f"  Requested action: {task['requested_action'] or '(none)'}")
        if task["new_shipping_address"] is not None:
            print(
                "  New shipping address: "
                f"{json.dumps(task['new_shipping_address'], ensure_ascii=False)}"
            )
        print(f"  Confidence: {task['confidence']:.2f}")


if __name__ == "__main__":
    main()

"""Small labeled live-model evaluation; order effects remain simulated."""

import argparse
import hashlib
import json
import math
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient
from langchain_core.callbacks import BaseCallbackHandler
from langsmith import tracing_context

from storekeeper.classify import CLASSIFIER_SYSTEM_PROMPT, classify_ticket
from storekeeper.demo import DemoClassifier
from storekeeper.live_model import PROJECT_ROOT, create_live_model
from storekeeper.live_preview import create_live_preview

# Labels are fixed before calling the model. Exact matching includes task order.
CASES = [
    ('cancel_missing', '帮我取消订单。', [('cancel_order', None)]),
    ('cancel_paraphrase', '这单我不想要了，帮我撤掉吧。', [('cancel_order', None)]),
    ('cancel_en', 'Please cancel order #1036.', [('cancel_order', '#1036')]),
    ('cancel_zh', '麻烦取消 #1036，谢谢。', [('cancel_order', '#1036')]),
    ('cancel_indirect', 'I changed my mind. Could you cancel my purchase?', [('cancel_order', None)]),
    ('cancel_1037', '取消订单 #1037。', [('cancel_order', '#1037')]),
    ('cancel_polite', 'Would you mind cancelling #1036 for me?', [('cancel_order', '#1036')]),
    ('cancel_no_invention', '取消我的订单，号码我忘了，别替我猜。', [('cancel_order', None)]),
    ('refund_zh', '我要退回 #1036 的货款。', [('refund_request', '#1036')]),
    ('refund_en', 'Please refund order #1037.', [('refund_request', '#1037')]),
    ('return_zh', '我想退掉 #1036 这件商品，申请退款。', [('refund_request', '#1036')]),
    ('address_missing', '请帮我修改 #1036 的收货地址。', [('address_change', '#1036')]),
    ('address_en', 'Change the shipping address for #1037, please.', [('address_change', '#1037')]),
    ('policy_returns', 'What is your return policy?', [('policy_question', None)]),
    ('policy_shipping', '你们通常几天发货？', [('policy_question', None)]),
    ('policy_warranty', 'Does your store offer a warranty?', [('policy_question', None)]),
    ('other_weather', '今天天气怎么样？', [('other', None)]),
    ('other_greeting', '你好。', [('other', None)]),
    ('ambiguous', '帮我处理一下订单。', [('other', None)]),
    ('multi_cancel', 'Cancel #1036 and cancel #1037.', [('cancel_order', '#1036'), ('cancel_order', '#1037')]),
    ('multi_distinct', '取消 #1036，然后给 #1037 退款。', [('cancel_order', '#1036'), ('refund_request', '#1037')]),
    ('multi_order', 'Refund #1037, then cancel #1036.', [('refund_request', '#1037'), ('cancel_order', '#1036')]),
    ('leading_zero', 'Please cancel order #0001.', [('cancel_order', '#0001')]),
    ('large_reference', 'Cancel #1234567890.', [('cancel_order', '#1234567890')]),
]


class UsageRecorder(BaseCallbackHandler):
    def __init__(self):
        self.calls = []

    def on_llm_end(self, response, **kwargs):
        for generations in response.generations:
            for generation in generations:
                message = getattr(generation, 'message', None)
                if message is not None:
                    metadata = message.response_metadata or {}
                    self.calls.append({'usage': message.usage_metadata,
                                       'model': metadata.get('model_name'),
                                       'token_usage': metadata.get('token_usage')})


def prediction(tasks):
    return [(task['intent'], task['order_reference']) for task in tasks]


def run_classification(model, rows, limit):
    for case_id, text, expected in CASES[:limit]:
        baseline = prediction(classify_ticket(text, DemoClassifier()))
        start = time.perf_counter()
        tasks = classify_ticket(text, model)
        actual = prediction(tasks)
        row = {'case_id': case_id, 'text': text, 'expected': expected,
               'actual': actual, 'passed': actual == expected,
               'baseline': baseline, 'baseline_passed': baseline == expected,
               'intent_passed': [task[0] for task in actual] == [task[0] for task in expected],
               'reference_passed': [task[1] for task in actual] == [task[1] for task in expected],
               'duration_seconds': round(time.perf_counter() - start, 3), 'tasks': tasks}
        rows.append(row)
        print(json.dumps({'case': case_id, 'passed': row['passed'],
                          'duration_seconds': row['duration_seconds']}, ensure_ascii=True), flush=True)


def run_workflows(model, output_directory):
    scenarios = [
        ('clarify_approve', '这单我不想要了，帮我取消。', '1036', 'approve', 'submitted'),
        ('clarify_reject', 'Please cancel my order.', '1036', 'reject', 'rejected_by_human'),
        ('fulfilled_denial', '取消 #1037。', None, None, 'denied_by_policy'),
        ('explicit_reject', 'Please cancel #1036.', None, 'reject', 'rejected_by_human'),
    ]
    rows = []
    for case_id, text, supplement, decision, expected_outcome in scenarios:
        application = create_live_preview(output_directory / case_id, model=model)
        start = time.perf_counter()
        with TestClient(application) as client:
            response = client.post('/api/tickets', json={'ticket_id': case_id, 'ticket_text': text})
            response.raise_for_status()
            ticket = response.json()
            original_tasks = ticket['tasks']
            observations = {'no_write_before_approval': not application.state.fixture_shopify_client.write_calls}
            if supplement is not None:
                questions = ticket['pending_clarifications']
                observations['clarification_requested'] = len(questions) == 1
                if questions:
                    response = client.post(f'/api/tickets/{case_id}/clarification', json={
                        'interrupt_id': questions[0]['interrupt_id'], 'order_reference': supplement})
                    response.raise_for_status()
                    ticket = response.json()
                    observations['task_identity_preserved'] = (
                        len(ticket['tasks']) == 1
                        and ticket['tasks'][0]['task_id'] == original_tasks[0]['task_id']
                        and ticket['tasks'][0]['intent'] == original_tasks[0]['intent'])
            if decision:
                approvals = ticket['pending_approvals']
                observations['approval_requested'] = len(approvals) == 1
                if approvals:
                    response = client.post(f'/api/tickets/{case_id}/decision', json={
                        'interrupt_id': approvals[0]['interrupt_id'], 'decision': decision})
                    response.raise_for_status()
                    ticket = response.json()
            outcomes = [result['outcome'] for result in ticket['task_results']]
            observations['expected_outcome'] = outcomes == [expected_outcome]
            observations['expected_simulated_writes'] = len(application.state.fixture_shopify_client.write_calls) == (1 if decision == 'approve' else 0)
            rows.append({'case_id': case_id, 'passed': all(observations.values()),
                         'observations': observations, 'ticket': ticket,
                         'duration_seconds': round(time.perf_counter() - start, 3)})
            print(json.dumps({'workflow': case_id, 'passed': rows[-1]['passed']}), flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--limit', type=int, choices=range(1, len(CASES) + 1), default=len(CASES))
    parser.add_argument('--workflows', action='store_true')
    arguments = parser.parse_args()
    recorder = UsageRecorder()
    model = create_live_model(callbacks=[recorder])
    output_directory = PROJECT_ROOT / 'var' / 'evaluations' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
    output_directory.mkdir(parents=True)
    report = {'status': 'running', 'model': model.model_name,
              'scope': 'Real DeepSeek; fixture orders only. Hand-labeled small development set, not production or held-out evaluation.',
              'prompt_sha256': hashlib.sha256(CLASSIFIER_SYSTEM_PROMPT.encode()).hexdigest(),
              'settings': {'temperature': 0, 'max_tokens': 2048, 'max_retries': 0, 'thinking': 'disabled'},
              'classification': [], 'workflows': [], 'calls': recorder.calls,
              'billed_cost': None, 'cost_note': 'No verified invoice or price schedule; token counts are recorded. Unknown cost is not zero.'}
    try:
        with tracing_context(enabled=False):
            run_classification(model, report['classification'], arguments.limit)
            if arguments.workflows:
                report['workflows'] = run_workflows(model, output_directory)
        report['status'] = 'complete'
    except Exception as error:
        # Do not print exception bodies: provider requests can carry credentials.
        report['status'] = 'failed'
        report['error_type'] = type(error).__name__
        report['http_status'] = getattr(error, 'status_code', None)
    finally:
        rows = report['classification']
        timings = sorted(row['duration_seconds'] for row in rows)
        report['summary'] = {'samples_completed': len(rows), 'samples_planned': arguments.limit,
                             'exact_matches': sum(row['passed'] for row in rows),
                             'baseline_exact_matches': sum(row['baseline_passed'] for row in rows),
                             'median_seconds': statistics.median(timings) if timings else None,
                             'p95_seconds': timings[max(0, math.ceil(len(timings) * .95) - 1)] if timings else None,
                             'completed_model_calls': len(recorder.calls)}
        path = output_directory / 'report.json'
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
        print(json.dumps({'status': report['status'], 'summary': report['summary'],
                          'report': str(path), 'error_type': report.get('error_type'),
                          'http_status': report.get('http_status')}, ensure_ascii=True), flush=True)
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())

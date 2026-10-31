"""One frozen synthetic test run, separate from the original development set."""
import hashlib
import json
import math
import statistics
import sys
import time
from datetime import datetime, timezone

from langsmith import tracing_context
from storekeeper.classify import CLASSIFIER_SYSTEM_PROMPT, classify_ticket
from storekeeper.live_model import PROJECT_ROOT, create_live_model
from evaluate_live_model import UsageRecorder, run_workflows


def grade_case(case, tasks):
    single = len(tasks) == 1
    intent = single and tasks[0]['intent'] == case['expected_intent']
    reference = single and tasks[0]['order_reference'] == case['expected_reference']
    address = None
    if 'expected_address' in case:
        actual = (tasks[0].get('new_shipping_address') or {}) if single else {}
        expected = case['expected_address']
        fields = ['first_name','last_name','company','address1','address2','city','province','zip','country','phone']
        address = single and all(actual.get(field) == expected.get(field) for field in fields)
    return {'intent_passed': intent, 'reference_passed': reference,
            'address_passed': address, 'passed': intent and reference and address is not False}


def main():
    dataset_path = PROJECT_ROOT / 'evaluation/test_v1.json'
    dataset_bytes = dataset_path.read_bytes()
    dataset = json.loads(dataset_bytes)
    assert len(dataset['cases']) == 40
    recorder = UsageRecorder()
    model = create_live_model(callbacks=[recorder])
    run_directory = PROJECT_ROOT / 'var/evaluations' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ_test_v1')
    run_directory.mkdir(parents=True)
    report = {'status': 'running', 'model': model.model_name, 'dataset': dataset,
              'dataset_sha256': hashlib.sha256(dataset_bytes).hexdigest(),
              'prompt_sha256': hashlib.sha256(CLASSIFIER_SYSTEM_PROMPT.encode()).hexdigest(),
              'classification': [], 'workflows': [], 'calls': recorder.calls}
    try:
        with tracing_context(enabled=False):
            for case in dataset['cases']:
                started = time.perf_counter()
                row = {'id': case['id'], 'text': case['text']}
                try:
                    tasks = classify_ticket(case['text'], model)
                    row.update(tasks=tasks, **grade_case(case, tasks))
                except Exception as error:
                    row.update(passed=False, intent_passed=False, reference_passed=False,
                               address_passed=False if 'expected_address' in case else None,
                               error_type=type(error).__name__)
                    if getattr(error, 'status_code', None) in (401, 402, 403, 429):
                        raise
                row['duration_seconds'] = round(time.perf_counter() - started, 3)
                report['classification'].append(row)
                (run_directory / 'partial.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
                print(json.dumps({'case': row['id'], 'passed': row['passed'],
                                  'seconds': row['duration_seconds']}), flush=True)
            report['workflows'] = run_workflows(model, run_directory)
        report['status'] = 'complete'
    except Exception as error:
        report.update(status='failed', error_type=type(error).__name__, http_status=getattr(error,'status_code',None))
    rows = report['classification']
    timings = sorted(row['duration_seconds'] for row in rows)
    report['summary'] = {
        'samples_completed': len(rows), 'samples_planned': 40,
        'strict_matches': sum(row['passed'] for row in rows),
        'intent_matches': sum(row['intent_passed'] for row in rows),
        'reference_matches': sum(row['reference_passed'] for row in rows),
        'partial_address_cases': sum(row['address_passed'] is not None for row in rows),
        'partial_address_matches': sum(row['address_passed'] is True for row in rows),
        'median_seconds': statistics.median(timings) if timings else None,
        'p95_seconds': timings[math.ceil(len(timings)*.95)-1] if timings else None,
        'workflow_passes': sum(row['passed'] for row in report['workflows']),
        'workflow_cases': len(report['workflows']), 'completed_model_calls':len(recorder.calls)}
    report['token_summary'] = {key:sum((call.get('usage') or {}).get(key,0) for call in recorder.calls)
                               for key in ['input_tokens','output_tokens','total_tokens']}
    report['cost_note'] = 'Actual billing unavailable. Reported usage is not an invoice. No automatic paid retries.'
    report['scope'] = 'One pre-frozen synthetic test set, not an external blind benchmark or production accuracy. Order APIs are simulated. Exact matching, not full language quality.'
    report_path = run_directory / 'report.json'
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'status':report['status'],'summary':report['summary'],'report':str(report_path)},ensure_ascii=True),flush=True)
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    sys.exit(main())

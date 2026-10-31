import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from evaluate_reliability import grade_case


class EvaluationGradingTests(unittest.TestCase):
    def test_frozen_test_set_is_distinct_from_development(self):
        from evaluate_live_model import CASES
        dataset = json.loads((ROOT / 'evaluation/test_v1.json').read_text(encoding='utf-8'))
        self.assertEqual(len(dataset['cases']), 40)
        self.assertFalse({row[1] for row in CASES} & {case['text'] for case in dataset['cases']})

    def test_invented_address_field_fails_even_when_intent_matches(self):
        case = {'expected_intent':'address_change','expected_reference':'#1','expected_address':{'city':'Shanghai'}}
        tasks = [{'intent':'address_change','order_reference':'#1','new_shipping_address':{'city':'Shanghai','country':'China'}}]
        result = grade_case(case, tasks)
        self.assertTrue(result['intent_passed'])
        self.assertFalse(result['address_passed'])
        self.assertFalse(result['passed'])

    def test_wrong_order_and_extra_tasks_do_not_count_as_success(self):
        case = {'expected_intent':'cancel_order','expected_reference':'#1'}
        self.assertFalse(grade_case(case,[{'intent':'cancel_order','order_reference':'#2'}])['passed'])
        task = {'intent':'cancel_order','order_reference':'#1'}
        self.assertFalse(grade_case(case,[task,task])['passed'])

import unittest

from langgraph.types import Command

from storekeeper.shopify.writes import cancel_order, ShopifyWriteError
from test_shopify_writes import FakeWriteClient, make_cancel_success_response
from test_graph import build_test_graph, make_classification, make_live_shopify_order, make_ticket_input, thread_config


class SubmissionReceiptTests(unittest.TestCase):
    def test_pending_job_is_not_a_completed_refund(self):
        result = cancel_order('order-id', FakeWriteClient([make_cancel_success_response()]))
        self.assertEqual(result['operation_status'], 'submitted')
        self.assertFalse(result['job_reported_done'])
        self.assertEqual(result['refund_status'], 'unverified')
        self.assertNotIn('payment is refunded', result['summary'])

    def test_done_job_alone_does_not_verify_payment_settlement(self):
        response = make_cancel_success_response()
        response['orderCancel']['job']['done'] = True
        result = cancel_order('order-id', FakeWriteClient([response]))
        self.assertTrue(result['job_reported_done'])
        self.assertEqual(result['operation_status'], 'submitted')
        self.assertEqual(result['refund_status'], 'unverified')

    def test_missing_receipt_fails_instead_of_claiming_success(self):
        response = make_cancel_success_response()
        response['orderCancel']['job'] = None
        with self.assertRaises(ShopifyWriteError):
            cancel_order('order-id', FakeWriteClient([response]))

    def test_receipt_bypasses_unverified_model_completion_text(self):
        graph, client, model = build_test_graph(make_classification(),
            [make_live_shopify_order('UNFULFILLED')], write_responses=[make_cancel_success_response()])
        model.reply_text = 'Your order is cancelled and your money is refunded.'
        config = thread_config('receipt-do-not-paraphrase')
        graph.invoke(make_ticket_input(), config)
        result = graph.invoke(Command(resume='approve'), config)
        self.assertEqual(result['task_results'][0]['outcome'], 'submitted')
        self.assertIn('have not been verified', result['reply_draft'])
        self.assertNotIn('money is refunded', result['reply_draft'])
        self.assertIsNone(model.received_messages)
        self.assertEqual(len(client.write_calls), 1)

import unittest
from unittest.mock import Mock
from unittest.mock import patch
from pydantic import ValidationError
from langchain_core.messages import AIMessage
from storekeeper.external_rates import ConversionRequest, convert_currency
from storekeeper.tool_agent import run_tool_agent


def rate_session():
    response=Mock(status_code=200)
    response.json.return_value={'date':'2026-11-04','base':'USD','quote':'CNY','rate':6.6578}
    return Mock(get=Mock(return_value=response))


class FakeModel:
    def __init__(self,calls):
        self.calls=calls
        self.messages=[]
    def bind_tools(self,tools,**kwargs):
        self.registered=[t.name for t in tools]
        return self
    def invoke(self,messages):
        self.messages.append(messages)
        return AIMessage(content='' if len(self.messages)==1 else 'Done.',
                         tool_calls=self.calls if len(self.messages)==1 else [])


class ToolAgentTests(unittest.TestCase):
    def test_policy_handler_uses_agentic_mode_and_only_lists_policy_citations(self):
        from storekeeper.graph.nodes import answer_policy_task
        result={'answer':'Verified result','had_error':False,'events':[],
                'sources':['warranty.md','https://api.frankfurter.dev/v2/rate/usd/cny']}
        with patch.dict('os.environ',{'POLICY_RETRIEVAL_MODE':'agentic'}), \
             patch('storekeeper.tool_agent.run_tool_agent',return_value=result):
            task=answer_policy_task('rules and rates',{'task_id':'task-1'},object())
        self.assertEqual(task['outcome'],'answered')
        self.assertEqual(task['policy_citations'],['warranty.md'])
        self.assertEqual(task['action_result']['sources'],result['sources'])

    def test_policy_handler_escalates_a_tool_loop_failure(self):
        from storekeeper.graph.nodes import answer_policy_task
        with patch.dict('os.environ',{'POLICY_RETRIEVAL_MODE':'agentic'}), \
             patch('storekeeper.tool_agent.run_tool_agent',side_effect=TimeoutError('details')):
            task=answer_policy_task('question',{'task_id':'task-1'},object())
        self.assertEqual(task['outcome'],'escalated_to_human')
        self.assertEqual(task['action_result']['error_type'],'TimeoutError')

    def test_external_conversion_checks_pair_and_uses_decimal_arithmetic(self):
        client=rate_session()
        value=convert_currency(ConversionRequest(amount='100',base_currency='USD',quote_currency='CNY'),session=client)
        self.assertEqual(value['converted_amount'],'665.78')
        self.assertEqual(value['as_of'],'2026-11-04')
        self.assertEqual(client.get.call_args.args[0],'https://api.frankfurter.dev/v2/rate/usd/cny')
        self.assertFalse(client.get.call_args.kwargs['allow_redirects'])

    def test_wrong_pair_and_invalid_amount_are_rejected(self):
        client=rate_session()
        with self.assertRaises(ValueError):
            convert_currency(ConversionRequest(amount=1,base_currency='EUR',quote_currency='CNY'),session=client)
        for amount in ('-1','0','1.234','NaN'):
            with self.assertRaises(ValidationError):
                ConversionRequest(amount=amount,base_currency='USD',quote_currency='CNY')

    def test_model_chooses_both_tools_and_gets_tool_messages(self):
        model=FakeModel([
            {'name':'convert_currency','args':{'amount':100,'base_currency':'USD','quote_currency':'CNY'},'id':'rate'},
            {'name':'search_store_policy','args':{'question':'Refund review over $100?'},'id':'rules'}])
        retriever=lambda text:{'trace':{'status':'supported'},'results':[{
            'chunk_id':'refund#review','document_name':'returns-and-refunds.md',
            'document_text':'Over $100 needs review.','evidence_quote':'Over $100 needs review.'}]}
        result=run_tool_agent('Convert and check rules.',model=model,rate_client=rate_session(),retriever=retriever)
        self.assertFalse(result['had_error'])
        self.assertEqual(result['tool_calls'],2)
        self.assertEqual(len([m for m in model.messages[-1] if m.type=='tool']),2)
        self.assertIn('returns-and-refunds.md',result['sources'])

    def test_unknown_tool_is_not_executed(self):
        model=FakeModel([{'name':'cancel_order','args':{'id':'1036'},'id':'bad'}])
        result=run_tool_agent('Cancel.',model=model)
        self.assertTrue(result['had_error'])
        self.assertEqual(result['events'][1]['result']['error_type'],'ValueError')

    def test_budget_prevents_extra_http_calls(self):
        call=lambda i:{'name':'convert_currency','args':{'amount':1,'base_currency':'USD','quote_currency':'CNY'},'id':str(i)}
        model=FakeModel([call(1),call(2)])
        client=rate_session()
        result=run_tool_agent('Convert.',model=model,max_tool_calls=1,rate_client=client)
        self.assertEqual(client.get.call_count,1)
        self.assertTrue(result['had_error'])

    def test_bad_parameters_and_network_errors_return_tool_errors(self):
        for amount in ('bad',100):
            model=FakeModel([{'name':'convert_currency','args':{'amount':amount,'base_currency':'USD','quote_currency':'CNY'},'id':'x'}])
            client=rate_session()
            client.get.side_effect=TimeoutError('private network details')
            result=run_tool_agent('Convert.',model=model,rate_client=client)
            self.assertTrue(result['had_error'])
            self.assertNotIn('private network details',str(result))
            if amount=='bad':
                client.get.assert_not_called()


if __name__=='__main__':
    unittest.main()

import unittest
from unittest.mock import patch
from storekeeper.advanced_retrieval import (
    bm25_search, reciprocal_rank_fusion, validate_evidence,
    EvidenceGrade, EvidenceSelection, retrieve_policy_chunks,
)
from storekeeper.graph.nodes import answer_policy_task


class AdvancedRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.chunks = [dict(chunk_id='a#one', document_name='a.md', heading='Refunds',
                            document_text='Refunds within 30 days.', rrf_score=0),
                       dict(chunk_id='b#two', document_name='b.md', heading='Shipping',
                            document_text='Shipping within 5 days.', rrf_score=0)]

    def test_lexical_matches_precise_term_and_no_overlap_is_empty(self):
        self.assertEqual(bm25_search('refund', self.chunks)[0]['chunk_id'], 'a#one')
        self.assertEqual(bm25_search('loyalty', self.chunks), [])

    def test_rrf_deduplicates_and_fuses_by_rank_not_incompatible_scores(self):
        a,b = self.chunks
        fused = reciprocal_rank_fusion([[a,b,a], [b]])
        self.assertEqual([r['chunk_id'] for r in fused], ['b#two', 'a#one'])
        self.assertAlmostEqual(fused[0]['rrf_score'], 1/62 + 1/61)

    def test_unknown_or_fabricated_quote_fails_closed(self):
        for chunk_id, quote in [('unknown', 'Refunds'), ('a#one', 'Refunds within 60 days.')]:
            grade = EvidenceGrade(sufficient=True, reason='test', evidence=[
                EvidenceSelection(chunk_id=chunk_id, quote=quote, relevance=3)])
            rows, invalid = validate_evidence(grade, self.chunks, 3)
            self.assertEqual(rows, [])
            self.assertEqual(invalid, [chunk_id])

    def test_insufficient_and_too_many_evidence_are_not_answered(self):
        evidence = [EvidenceSelection(chunk_id=r['chunk_id'], quote=r['document_text'], relevance=3)
                    for r in self.chunks]
        grade = EvidenceGrade(sufficient=False, evidence=evidence, reason='partial')
        self.assertEqual(validate_evidence(grade,self.chunks,3)[0], [])
        grade.sufficient=True
        self.assertEqual(validate_evidence(grade,self.chunks,1)[0], [])

    def test_markdown_line_wrap_is_not_a_fabricated_quote(self):
        row={**self.chunks[0], 'document_text':'Refunds within\n30 days.'}
        grade=EvidenceGrade(sufficient=True, reason='rule', evidence=[
            EvidenceSelection(chunk_id='a#one', quote='Refunds within 30 days.', relevance=3)])
        self.assertEqual(validate_evidence(grade,[row],3)[0][0]['chunk_id'],'a#one')

    def test_only_transport_failures_get_one_bounded_retry(self):
        class Model:
            attempts=0
            def with_structured_output(inner,schema,**kwargs):
                inner.schema=schema
                return inner
            def invoke(inner,messages):
                inner.attempts+=1
                if inner.attempts==1:
                    raise ConnectionError('synthetic transport failure')
                return {'raw':object(),'parsed':inner.schema(sufficient=False,evidence=[],reason='not covered'), 'parsing_error':None}
        model=Model()
        with patch('storekeeper.advanced_retrieval.load_policy_chunks',return_value=self.chunks):
            result=retrieve_policy_chunks('gift?',model=model,rewrite=False,dense_search=lambda q,n:self.chunks)
        self.assertEqual(model.attempts,2)
        self.assertEqual(len(result['trace']['transport_errors']),1)
        self.assertEqual(result['results'],[])

    def test_full_subgraph_preserves_original_question_and_bounded_calls(self):
        class Model:
            calls=[]
            def with_structured_output(inner,schema,**kwargs):
                inner.schema=schema
                return inner
            def invoke(inner,messages):
                inner.calls.append(messages[1].content)
                if inner.schema.__name__ == 'SearchRewrite':
                    parsed=inner.schema(english_query='refund 30 days')
                else:
                    parsed=inner.schema(sufficient=True, reason='Explicit rule.', evidence=[
                        dict(chunk_id='a#one', quote='Refunds within 30 days.', relevance=3)])
                return {'raw': object(), 'parsed': parsed, 'parsing_error': None}
        model=Model()
        with patch('storekeeper.advanced_retrieval.load_policy_chunks', return_value=self.chunks):
            result=retrieve_policy_chunks('退款期限？',model=model,
                    dense_search=lambda q,n:self.chunks)
        self.assertEqual(len(model.calls),2)
        self.assertIn('退款期限',model.calls[1])
        self.assertEqual(result['results'][0]['chunk_id'],'a#one')

    def test_no_evidence_escalates_without_calling_answer_model(self):
        task=dict(intent='policy_question',task_id='task-1')
        with patch.dict('os.environ', {'POLICY_RETRIEVAL_MODE':'enhanced'}), \
             patch('storekeeper.advanced_retrieval.retrieve_policy_chunks',return_value={'results':[], 'trace':{'status':'insufficient_evidence'}}):
            result=answer_policy_task('gift wrapping?',task,object())
        self.assertEqual(result['outcome'],'escalated_to_human')
        self.assertEqual(result['policy_citations'],[])

    def test_retrieval_failure_is_not_silent_baseline_fallback(self):
        with patch.dict('os.environ',{'POLICY_RETRIEVAL_MODE':'enhanced'}), \
             patch('storekeeper.advanced_retrieval.retrieve_policy_chunks',side_effect=TimeoutError('redacted')):
            result=answer_policy_task('refund?',dict(intent='policy_question',task_id='task-1'),object())
        self.assertEqual(result['action_result']['retrieval_trace']['error_type'],'TimeoutError')


if __name__=='__main__':
    unittest.main()

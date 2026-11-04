"""Real-model smoke of the application policy handler; fixture-free, no Shopify."""
import json
import os
from pathlib import Path


def main():
    root=Path(__file__).resolve().parents[1]
    os.environ['LANGSMITH_TRACING']='false'
    os.environ['ANONYMIZED_TELEMETRY']='False'
    os.environ['POLICY_RETRIEVAL_MODE']='enhanced'
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH=root/'var/embedding_cache/all-MiniLM-L6-v2'
    from storekeeper.policy_docs import rebuild_policy_index
    from storekeeper.live_model import create_live_model
    from storekeeper.graph.nodes import answer_policy_task
    rebuild_policy_index()
    model=create_live_model()
    task=dict(task_id='task-1',intent='policy_question',requested_action=None,
              order_reference=None,new_shipping_address=None,confidence=1.0)
    rows=[]
    for question in ('咖啡机的保修从哪一天开始算，一共多久？','Can I pay using six monthly installments?'):
        result=answer_policy_task(question,task,model)
        rows.append({'question':question,'result':result})
        print(json.dumps({'outcome':result['outcome'],'citations':result['policy_citations']},ensure_ascii=True),flush=True)
    destination=root/'var/evaluations/advanced_rag_v1/application_smoke.json'
    destination.write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    assert rows[0]['result']['outcome']=='answered'
    assert rows[0]['result']['policy_citations']
    assert rows[1]['result']['outcome']=='escalated_to_human'


if __name__=='__main__':
    main()

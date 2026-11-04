"""Append a transport-only recovery report; never replace first-run evidence."""
import argparse
import hashlib
import json
import os
import time
from pathlib import Path
from datetime import datetime, timezone

from storekeeper.advanced_retrieval import retrieve_policy_chunks
from storekeeper.retrieval_evaluation import score_retrieval, summarize_retrieval


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--live',action='store_true',required=True)
    args=parser.parse_args()
    original=json.loads(args.report.read_text(encoding='utf-8'))
    if original['status']=='running':
        raise RuntimeError('Wait for the first run to finish.')
    destination=args.report.with_name('transport_recovery.json')
    if destination.exists():
        raise FileExistsError('Do not overwrite a completed recovery run.')
    os.environ['LANGSMITH_TRACING']='false'
    os.environ['ANONYMIZED_TELEMETRY']='False'
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    root=Path(__file__).resolve().parents[1]
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH=root/'var/embedding_cache/all-MiniLM-L6-v2'
    from storekeeper.live_model import create_live_model
    model=create_live_model()
    report={'source_report_sha256':hashlib.sha256(args.report.read_bytes()).hexdigest(),
            'source_report':str(args.report),'scope':'Only transport failures retried serially with <=1 retry per call; no semantic failures retuned or rerun. First report retained.',
            'new_retriever_sha256':hashlib.sha256((root/'src/storekeeper/advanced_retrieval.py').read_bytes()).hexdigest(),
            'started_at_utc':datetime.now(timezone.utc).isoformat(),'status':'running','cases':[]}
    def save():
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for old in original['cases']:
        row=dict(old)
        error=old.get('error_type','')
        if 'Connection' in error or 'Timeout' in error:
            row={key:value for key,value in old.items() if key not in ('error_type','trace')}
            started=time.perf_counter()
            try:
                result=retrieve_policy_chunks(old['question'],model=model,
                    index_directory=args.report.parent/'chroma_index',
                    rewrite=old['variant'] in ('translated_hybrid','enhanced'),rerank=old['variant']=='enhanced')
                row.update(trace=result['trace'],retrieved_contexts=result['results'],abstained=not result['results'])
                row.update(score_retrieval([r['chunk_id'] for r in result['results']],old['reference_context_ids']))
            except Exception as error:
                row.update(error_type=type(error).__name__,abstained=False,retrieved_contexts=[])
                row.update(score_retrieval([],old['reference_context_ids']))
            row.update(recovered_transport=True,first_error_type=old['error_type'],query_seconds=round(time.perf_counter()-started,4))
            print(json.dumps({'id':row['id'],'variant':row['variant'],'recall':row['recall'],'error':row.get('error_type')}),flush=True)
        else:
            row['recovered_transport']=False
        report['cases'].append(row)
        save()
    report['summary']={}
    for name in original['summary']:
        report['summary'][name]={}
        for variant in original['summary'][name]:
            report['summary'][name][variant]={}
            for group in original['summary'][name][variant]:
                rows=[r for r in report['cases'] if (r['dataset'],r['variant'],r['group'])==(name,variant,group)]
                summary=summarize_retrieval(rows)
                summary['successful_abstentions']=sum(r['abstained'] for r in rows)
                summary['mean_seconds']=sum(r['query_seconds'] for r in rows)/len(rows)
                report['summary'][name][variant][group]=summary
    report['status']='complete' if not any('error_type' in r for r in report['cases']) else 'complete_with_errors'
    report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    save()
    print(str(destination),flush=True)


if __name__=='__main__':
    main()

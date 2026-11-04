"""Compare fixed retrieval variants. Paid model calls are explicit with --live.

Scores use evidence IDs, NOT the evidence-grading model as metric judge.
Old v1 is regression data; v2 is additional synthetic data, not real users.
"""
import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from storekeeper.policy_docs import load_policy_chunks, rebuild_policy_index, search_policy_chunks
from storekeeper.retrieval_evaluation import validate_dataset, score_retrieval, summarize_retrieval
from storekeeper.advanced_retrieval import retrieve_policy_chunks

ROOT=Path(__file__).resolve().parents[1]
VARIANTS=('dense','hybrid','translated_hybrid','enhanced')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--freeze-only',action='store_true')
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--run-directory',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=3)
    args=parser.parse_args()
    if not 1<=args.workers<=4:
        parser.error('workers must be between 1 and 4')
    datasets={name:json.loads((ROOT/f'evaluation/{name}.json').read_text(encoding='utf-8'))
              for name in ('retrieval_v1','retrieval_v2_holdout')}
    chunks=load_policy_chunks()
    for dataset in datasets.values():
        validate_dataset(dataset,{r['chunk_id'] for r in chunks})
    frozen={'datasets':datasets,'chunks':chunks,
            'source_sha256':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [ROOT/'src/storekeeper/advanced_retrieval.py',ROOT/'src/storekeeper/policy_docs.py']},
            'variants':VARIANTS,'top_k':3,'candidates':8,'rrf_constant':60,'grade_minimum':2}
    run=args.run_directory
    run.mkdir(parents=True,exist_ok=True)
    manifest=run/'frozen_manifest.json'
    serialized=json.dumps(frozen,ensure_ascii=False,indent=2)
    if manifest.exists():
        if manifest.read_text(encoding='utf-8')!=serialized:
            raise ValueError('Frozen configuration changed; use a new run directory.')
    else:
        manifest.write_text(serialized,encoding='utf-8')
    print('Manifest frozen; sha256='+hashlib.sha256(serialized.encode()).hexdigest(),flush=True)
    if args.freeze_only:
        return 0
    if not args.live:
        parser.error('Use --live to authorize actual model requests, or --freeze-only.')
    report_path=run/'report.json'
    if report_path.exists():
        raise FileExistsError('Do not overwrite an existing evaluation report.')
    os.environ['ANONYMIZED_TELEMETRY']='False'
    os.environ['LANGSMITH_TRACING']='false'
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH=ROOT/'var/embedding_cache/all-MiniLM-L6-v2'
    from storekeeper.live_model import create_live_model
    model=create_live_model()
    report={'started_at_utc':datetime.now(timezone.utc).isoformat(),'status':'running','cases':[],
            'scope':'Synthetic retrieval-only comparison; old v1 is known regression, v2 additional pre-run frozen data. No production or final-answer accuracy claim.',
            'model':str(model.model_name),'manifest_sha256':hashlib.sha256(serialized.encode()).hexdigest()}
    def save():
        report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    index=run/'chroma_index'
    report['indexed_chunks']=rebuild_policy_index(policy_index_directory=index)
    search_policy_chunks('warm up',policy_index_directory=index)
    def run_case(dataset_name,case):
        rows=[]
        for variant in VARIANTS:
            started=time.perf_counter()
            row={**case,'dataset':dataset_name,'variant':variant}
            try:
                if variant=='dense':
                    results=search_policy_chunks(case['question'],policy_index_directory=index)
                    trace={'calls':[]}
                else:
                    response=retrieve_policy_chunks(case['question'],model=model,index_directory=index,
                        rewrite=variant in ('translated_hybrid','enhanced'),rerank=variant=='enhanced')
                    results,trace=response['results'],response['trace']
                row['trace']=trace
                row['retrieved_contexts']=results
                row.update(score_retrieval([r['chunk_id'] for r in results],case['reference_context_ids']))
                row['abstained']=not results
            except Exception as error:
                row.update(score_retrieval([],case['reference_context_ids']))
                # Error text could contain provider payloads. Record type only.
                row['error_type']=type(error).__name__
                row['abstained']=False
            row['query_seconds']=round(time.perf_counter()-started,4)
            rows.append(row)
        return rows
    save()
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        jobs=[executor.submit(run_case,name,case) for name,data in datasets.items() for case in data['cases']]
        for number,job in enumerate(as_completed(jobs),1):
            rows=job.result()
            report['cases'].extend(rows)
            save()
            print(json.dumps({'completed':number,'id':rows[0]['id'],
                 'recall':{r['variant']:r['recall'] for r in rows},
                 'errors':[r.get('error_type') for r in rows if 'error_type' in r]}),flush=True)
    report['summary']={}
    for name in datasets:
        report['summary'][name]={}
        for variant in VARIANTS:
            report['summary'][name][variant]={}
            for group in ('english_answerable','chinese_stress','unanswerable'):
                rows=[r for r in report['cases'] if (r['dataset'],r['variant'],r['group'])==(name,variant,group)]
                summary=summarize_retrieval(rows)
                summary['successful_abstentions']=sum(r['abstained'] for r in rows)
                summary['mean_seconds']=sum(r['query_seconds'] for r in rows)/len(rows)
                calls=[c for r in rows for c in r.get('trace',{}).get('calls',[])]
                summary['model_calls']=len(calls)
                summary['total_tokens']=sum(c['usage'].get('total_tokens',0) for c in calls)
                report['summary'][name][variant][group]=summary
    report['status']='complete' if not any('error_type' in r for r in report['cases']) else 'complete_with_errors'
    report['finished_at_utc']=datetime.now(timezone.utc).isoformat()
    save()
    print('Report: '+str(report_path),flush=True)
    return 0 if report['status']=='complete' else 1


if __name__=='__main__':
    sys.exit(main())

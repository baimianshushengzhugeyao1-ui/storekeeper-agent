"""Offline replay of recorded grading outputs after whitespace-check fix.

No provider calls or semantic label tuning. This is not an independent live run.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
from storekeeper.advanced_retrieval import EvidenceGrade, validate_evidence
from storekeeper.retrieval_evaluation import score_retrieval, summarize_retrieval


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report',required=True,type=Path)
    args=parser.parse_args()
    original=json.loads(args.report.read_text(encoding='utf-8'))
    report=copy.deepcopy(original)
    destination=args.report.with_name('evidence_replay.json')
    if destination.exists():
        raise FileExistsError('Do not overwrite prior evidence replay.')
    report['replay']={'source_sha256':hashlib.sha256(args.report.read_bytes()).hexdigest(),
        'scope':'Offline replay of saved model evidence with whitespace-only quote normalization; no new model calls. v2 exposed the line-wrap bug and is no longer untouched validation data.'}
    report['changed_cases']=[]
    for row in report['cases']:
        trace=row.get('trace',{})
        if row['variant']=='enhanced' and 'grade' in trace and 'error_type' not in row:
            results,rejected=validate_evidence(EvidenceGrade(**trace['grade']),trace['candidates'],3)
            if [r['chunk_id'] for r in results] != [r['chunk_id'] for r in row['retrieved_contexts']]:
                report['changed_cases'].append(row['id'])
            row['retrieved_contexts']=results
            row['abstained']=not results
            trace['rejected_evidence_ids']=rejected
            trace['status']='supported' if results else 'insufficient_evidence'
            row.update(score_retrieval([r['chunk_id'] for r in results],row['reference_context_ids']))
    for name in report['summary']:
        for variant in report['summary'][name]:
            for group in report['summary'][name][variant]:
                rows=[r for r in report['cases'] if (r['dataset'],r['variant'],r['group'])==(name,variant,group)]
                summary=summarize_retrieval(rows)
                summary['successful_abstentions']=sum(r['abstained'] for r in rows)
                summary['recorded_mean_seconds']=sum(r['query_seconds'] for r in rows)/len(rows)
                report['summary'][name][variant][group]=summary
    destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'path':str(destination),'changed_cases':report['changed_cases']}))


if __name__=='__main__':
    main()

"""Let the real model load an external Skill and query public commerce data."""
import argparse
import json
import os
from pathlib import Path
from datetime import datetime,timezone


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question',default='请找两款可以送到美国、价格不超过100美元的咖啡机，给出商家、当前售价、可售状态和商品链接。只查询，不下单。')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    os.environ['LANGSMITH_TRACING']='false'
    from storekeeper.live_model import create_live_model
    from storekeeper.tool_agent import run_tool_agent
    result=run_tool_agent(args.question,model=create_live_model(),toolset='catalog')
    directory=root/'var/catalog_skill'
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json')
    path.write_text(json.dumps({'question':args.question,**result},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'answer':result['answer'],'had_error':result['had_error'],
        'tool_names':[e['name'] for e in result['events'] if e['kind']=='tool'],
        'sources':result['sources']},ensure_ascii=True),flush=True)
    print(str(path),flush=True)


if __name__=='__main__':
    main()

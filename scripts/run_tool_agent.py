"""Run the real model with policy retrieval and an external reference-rate tool."""
import argparse
import json
import os
from datetime import datetime,timezone
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--question',default='请查一下店铺退款超过100美元需要什么额外审核，并按最新参考汇率算一下100美元约合多少人民币。')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    os.environ['LANGSMITH_TRACING']='false'
    os.environ['ANONYMIZED_TELEMETRY']='False'
    from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2
    ONNXMiniLM_L6_V2.DOWNLOAD_PATH=root/'var/embedding_cache/all-MiniLM-L6-v2'
    from storekeeper.live_model import create_live_model
    from storekeeper.tool_agent import run_tool_agent
    result=run_tool_agent(args.question,model=create_live_model())
    directory=root/'var/tool_agent'
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'.json')
    path.write_text(json.dumps({'question':args.question,**result},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True),flush=True)
    print(str(path),flush=True)


if __name__=='__main__':
    main()

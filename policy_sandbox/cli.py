from __future__ import annotations
import argparse
import json
from pathlib import Path
from .reporting import Reporter
from .service import ApplicationService
from .pair_analysis import PairAnalysisError, PairAnalysisService
from .storage import Database

def load_json(path:str):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def main(argv=None):
    parser=argparse.ArgumentParser(prog='policy_sandbox')
    parser.add_argument('--database',default=':memory:')
    sub=parser.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run'); run.add_argument('--fixture',required=True); run.add_argument('--output',required=True)
    report=sub.add_parser('report'); report.add_argument('--kind')
    pair=sub.add_parser('pair-analysis'); pair.add_argument('--fixture',required=True); pair.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    if args.command=='pair-analysis':
        payload=load_json(args.fixture)
        try:
            result=PairAnalysisService().run_verified(payload)
        except PairAnalysisError as exc:
            parser.exit(2,json.dumps({'error':str(exc)},ensure_ascii=False)+'\n')
        text=json.dumps(result,ensure_ascii=False,indent=2,default=str)
        Path(args.output).parent.mkdir(parents=True,exist_ok=True)
        Path(args.output).write_text(text,encoding='utf-8')
        print(json.dumps({'analysis_id':result['analysis_id'],'snapshot_fingerprint':result['snapshot_fingerprint'],'movement_counts':result['movement_counts']},ensure_ascii=False,sort_keys=True)); return 0
    database=Database(args.database)
    if args.command=='run':
        fixture=load_json(args.fixture); service=ApplicationService(database); outputs=[]
        for index,item in enumerate(fixture.get('items',[])):
            outputs.append(service.process(item,fixture.get('context',{}),item.get('request_key',f'item-{index}')))
        payload={'product':'青年人才安居就业政策资格模拟与差异解释器','processed':len(outputs),'outputs':outputs,'report':Reporter(database).overview()}
        Path(args.output).parent.mkdir(parents=True,exist_ok=True); Path(args.output).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(payload,ensure_ascii=False,sort_keys=True)); return 0
    payload=Reporter(database).owner_breakdown(args.kind) if args.kind else Reporter(database).overview(); print(json.dumps(payload,ensure_ascii=False,sort_keys=True)); return 0

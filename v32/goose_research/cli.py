"""Explicit, local-only orchestration. Output is confined to ignored v32/work."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
import time
import uuid

from .market import validate_dataset, fetch_history, xshg_sessions, etf_anomaly_diagnostics
from .backtest import run_comparison
from .comparison import compare_sources
from .reports import build_reports, blocked_report, canonical_bytes, pre1445_health, read_v1_status

ROOT=Path(__file__).resolve().parents[1]
WORK=ROOT/'work'


def strict_json(text):
    def pairs(items):
        value={}
        for key,item in items:
            if key in value or key in {'__proto__','prototype','constructor'}:
                raise ValueError('DUPLICATE_OR_UNSAFE_JSON_KEY')
            value[key]=item
        return value
    return json.loads(text,object_pairs_hook=pairs,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('NONFINITE_JSON')))


def read_dataset(path):
    p=Path(path).resolve(strict=True)
    if not p.is_file() or p.stat().st_size>8*1024*1024:raise ValueError('DATASET_FILE_LIMIT')
    return validate_dataset(strict_json(p.read_text(encoding='utf-8-sig')))


def output_directory(request=None):
    WORK.mkdir(exist_ok=True)
    if WORK.is_symlink() or WORK.is_junction():raise ValueError('OUTPUT_REPARSE_NOT_ALLOWED')
    path=Path(request).absolute() if request else WORK/(datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
    resolved=path.resolve()
    if not resolved.is_relative_to(WORK.resolve()) or resolved==WORK.resolve():raise ValueError('OUTPUT_MUST_BE_NEW_SUBDIRECTORY_OF_V32_WORK')
    for ancestor in [path,*path.parents]:
        if ancestor.exists() and (ancestor.is_symlink() or ancestor.is_junction()):raise ValueError('OUTPUT_REPARSE_NOT_ALLOWED')
        if ancestor==WORK:break
    resolved.mkdir(parents=True,exist_ok=False)
    return resolved


def write_json(path,doc):
    # Never replace a user file or follow a pre-existing output link.
    with Path(path).open('x',encoding='utf-8',newline='\n') as stream:
        json.dump(doc,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.write('\n')


def synthetic_dataset(symbol='600900.SH',kind='stock'):
    sessions=xshg_sessions('2025-01-02','2025-12-31')
    bars=[]
    for index,day in enumerate(sessions):
        # Deterministic invention, not a distorted real security price series.
        base=Decimal('20') if kind=='stock' else Decimal('1.2')
        scale=Decimal('0.1') if kind=='stock' else Decimal('0.003')
        opening=base+scale*Decimal((index%45)-20)+scale*Decimal(index//50)
        close=opening+scale*Decimal(((index*7)%9)-4)/10
        bars.append({'date':day,'open':str(opening),'close':str(close),
            'high':str(max(opening,close)+scale),'low':str(min(opening,close)-scale),
            'volume':100000+index*100,'publishedAt':day+'T16:00:00+08:00'})
    return validate_dataset({'format':'goose-market-dataset','version':1,'symbol':symbol,'kind':kind,
        'source':'SYNTHETIC / 完全虚构离线测试样本','sourceUrl':'https://example.invalid/goose-synthetic',
        'adjustment':'none','currency':'CNY','priceUnit':'CNY-per-share','fetchedAt':'2026-01-02T00:00:00Z',
        'calendar':{'name':'XSHG','sessions':sessions,'supportedThrough':'2026-12-31'},'bars':bars,
        'quality':{'status':'synthetic','crossValidated':False,'unitVerified':True,'corporateActionsVerified':False,
            'unitEvidence':'Synthetic generator defines all values in CNY; not evidence about real ETF prices.',
            'warnings':['完全虚构价格，不可解释为600900或515450真实历史收益。','公司行为未建模，非含息收益。']}})


def publish_reports(folder):
    web=ROOT.parent/'v3/web'
    for ancestor in [web,*web.parents]:
        if ancestor.is_symlink() or ancestor.is_junction():raise ValueError('PUBLISH_REPARSE_REJECTED')
        if ancestor==ROOT.parent:break
    if 'V3.2' not in (web/'index.html').read_text(encoding='utf-8'):raise ValueError('PUBLISH_ONLY_V32_DEVELOPMENT_SHELL')
    for version in ['v31','v32']:
        source=folder/('goose-report-'+version+'.json')
        if not source.exists():source=folder/'goose-report-v31.json'
        target=web/('research-report-'+version+'.json')
        if target.is_symlink() or target.is_junction():raise ValueError('PUBLISH_REPARSE_REJECTED')
        temporary=web/('research-report-'+uuid.uuid4().hex+'.pending')
        with temporary.open('xb') as stream:stream.write(source.read_bytes())
        os.replace(temporary,target)


def run_research(dataset,*,config=None,out=None,pre1445=False,v1_db=None,now=None,publish=False):
    start=time.perf_counter()
    dataset=validate_dataset(dataset)
    folder=output_directory(out)
    write_json(folder/'dataset.json',dataset)
    digest=sha256(canonical_bytes(dataset)).hexdigest()
    quality={'symbol':dataset['symbol'],'source':dataset['source'],'sourceUrl':dataset['sourceUrl'],
        'through':dataset['bars'][-1]['date'],'fetchedAt':dataset['fetchedAt'],'quality':dataset['quality'],
        'sha256':digest,'adjustment':dataset['adjustment'],'priceUnit':dataset['priceUnit']}
    if dataset['kind']=='etf':quality['etfDiagnostics']=etf_anomaly_diagnostics(dataset)
    write_json(folder/'quality.json',quality)
    blocked=dataset['quality']['status']=='blocked'
    comparison=None
    try:
        if blocked:raise ValueError('DATASET_BLOCKED_NO_RETURN_CALCULATION')
        comparison=run_comparison(dataset,config)
        report,extended=build_reports(dataset,comparison,now=now,kind='pre1445-test' if pre1445 else 'historical-research')
        write_json(folder/'backtest.json',comparison)
        write_json(folder/'goose-report-v31.json',report)
        write_json(folder/'goose-report-v32.json',extended)
    except ValueError as error:
        # Quality failures never turn into zero-return charts or successful signals.
        write_json(folder/'blocked.json',{'status':'blocked','reason':str(error),'returnsPublished':False,'calculationCompleted':comparison is not None})
        write_json(folder/'goose-report-v31.json',blocked_report(dataset['symbol'],str(error),now=now))
        blocked=True
    elapsed=round((time.perf_counter()-start)*1000)
    if pre1445:
        status=read_v1_status(v1_db) if v1_db else None
        health=pre1445_health(dataset,now=now,v1_status=status,elapsed_ms=elapsed)
        if blocked:health['backtestResearch']['included']=False
        write_json(folder/'pre1445-test.json',health)
        write_json(folder/'bark-dry-run.json',health['barkPreview'])
    write_json(folder/'run.json',{'version':1,'mode':'pre1445-test' if pre1445 else 'historical-research',
        'output':str(folder),'datasetSha256':digest,'blocked':blocked,'elapsedMs':elapsed,'elapsedScope':'validate-backtest-export core; excludes process startup and input load',
        'notificationSent':False,'realTrading':False,'v1DatabaseWritten':False})
    if publish:publish_reports(folder)
    return {'output':str(folder),'blocked':blocked,'elapsedMs':elapsed,'datasetSha256':digest,'publishedLocally':publish}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Goose V3.2 isolated research; no real trades or notification sending')
    sub=parser.add_subparsers(dest='command',required=True)
    manual=sub.add_parser('manual-bark',help='one explicitly human-authorized test only; never scheduled or called by research')
    manual.add_argument('--health',required=True);manual.add_argument('--config',required=True);manual.add_argument('--confirm-one-test',action='store_true',required=True)
    compare=sub.add_parser('compare',help='compare two explicit provider datasets without unit promotion')
    compare.add_argument('--left',required=True);compare.add_argument('--right',required=True);compare.add_argument('--out')
    sample=sub.add_parser('sample',help='write clearly fictional historical replay data')
    sample.add_argument('--symbol',default='600900.SH');sample.add_argument('--kind',choices=['stock','etf'],default='stock');sample.add_argument('--out')
    fetch=sub.add_parser('fetch',help='explicit HTTPS source request with OS proxy and certificates')
    fetch.add_argument('--symbol',required=True);fetch.add_argument('--kind',choices=['stock','etf']);fetch.add_argument('--source',choices=['eastmoney','tencent'],required=True)
    fetch.add_argument('--start',required=True);fetch.add_argument('--end',required=True);fetch.add_argument('--out')
    for name in ['backtest','pre1445']:
        cmd=sub.add_parser(name)
        cmd.add_argument('--dataset',required=True);cmd.add_argument('--config');cmd.add_argument('--out');cmd.add_argument('--publish',action='store_true',help='publish only research report JSON to this separate V3.2 private shell')
        if name=='pre1445':cmd.add_argument('--test',action='store_true',required=True);cmd.add_argument('--v1-db')
    args=parser.parse_args(argv)
    try:
        if args.command=='manual-bark':
            from .notify import send_manual_test
            health_path=Path(args.health)
            if health_path.stat().st_size>8*1024*1024:raise ValueError('HEALTH_FILE_TOO_LARGE')
            result=send_manual_test(strict_json(health_path.read_text(encoding='utf-8-sig')),args.config,confirmed=args.confirm_one_test)
            print(json.dumps(result,ensure_ascii=True));return 0 if result['status']=='service-accepted' else 2
        elif args.command=='compare':
            result=compare_sources(read_dataset(args.left),read_dataset(args.right));folder=output_directory(args.out);write_json(folder/'source-comparison.json',result);result={'output':str(folder),**result}
        elif args.command=='sample':
            result=run_research(synthetic_dataset(args.symbol,args.kind),out=args.out)
        elif args.command=='fetch':
            dataset=fetch_history(args.symbol,args.start,args.end,args.source,kind=args.kind)
            folder=output_directory(args.out);write_json(folder/'dataset.json',dataset)
            result={'output':str(folder),'quality':dataset['quality'],'bars':len(dataset['bars'])}
        else:
            dataset=read_dataset(args.dataset)
            config=strict_json(Path(args.config).read_text(encoding='utf-8-sig')) if args.config else None
            result=run_research(dataset,config=config,out=args.out,pre1445=args.command=='pre1445',v1_db=getattr(args,'v1_db',None),publish=args.publish)
        print(json.dumps(result,ensure_ascii=True));return 2 if result.get('blocked') is True or result.get('quality',{}).get('status')=='blocked' else 0
    except (ValueError,OSError,json.JSONDecodeError) as error:
        print(json.dumps({'status':'failed','code':str(error) if isinstance(error,ValueError) else type(error).__name__,'notificationSent':False},ensure_ascii=True));return 2

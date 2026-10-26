"""Version-one phone exports and separate 14:45 test health reports."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from time import perf_counter
import uuid

BJ = timezone(timedelta(hours=8))
NAMES = {'buy_hold':'买入并持有', 'v1_ma20':'V1 前收盘与20日均线比较', 'ma20_cross':'20日均线交叉对照', 'periodic':'定期投入'}


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def utc_stamp(value=None):
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')


def _basis(dataset, result, strategy):
    signal = result['strategies'][strategy]['signal']
    return signal.get('basis', '研究信号缺少依据，观察'), signal.get('risk', '历史结果不等于实时交易建议')


def build_reports(dataset, result, *, now=None, kind='historical-research'):
    """Produce a strict V3.1-compatible report plus a richer V3.2 report.

    Price-only historical results never assert an intraday executable decision.
    Fractional-cent ETF trades remain in backtest.json, not rounded phone fills.
    """
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:raise ValueError('AWARE_CLOCK_REQUIRED')
    if any(type(result['partitions'].get(key)) is not dict or not result['partitions'][key].get('from') or not result['partitions'][key].get('to') for key in ['train','validation','test']):
        raise ValueError('INSUFFICIENT_SESSIONS_FOR_THREE_RESEARCH_PARTITIONS')
    data_date = dataset['bars'][-1]['date']
    q = dataset['quality']
    synthetic = q['status'] == 'synthetic'
    reference = result['strategies']['v1_ma20']
    metrics = reference['metrics']
    for item in result['strategies'].values():
        if any(type(item['metrics'][key]) is not int for key in ['totalReturnBps','annualizedReturnBps','maxDrawdownBps','volatilityBps','tradeCount']):
            raise ValueError('RESEARCH_METRICS_UNAVAILABLE_DO_NOT_INVENT_ZERO')
        if any(abs(item['metrics'][key])>1000000000 for key in ['totalReturnBps','annualizedReturnBps','maxDrawdownBps','volatilityBps']):
            raise ValueError('RESEARCH_METRICS_EXCEED_PHONE_SCHEMA_RANGE')
    basis, risk = _basis(dataset, result, 'v1_ma20')
    stance = reference['signal'].get('stance','watch')
    if stance not in {'hold','watch','accumulate','reduce'}:
        stance = 'watch'
    if synthetic or kind == 'pre1445-test':
        stance = 'watch'
    label = ('完全虚构离线样本 / ' if synthetic else '单源历史价格研究 / ') + dataset['source']
    limitations = list(dict.fromkeys([*q.get('warnings',[]), *result.get('limitations',[]),
        '历史日线研究，未采集当日盘中报价，不构成实时交易建议。',
        '逐笔模拟执行保存在独立backtest.json，未将分以下ETF价格四舍五入伪装为手机成交。',
        '当前费用参数为统一研究假设，不保证覆盖历史税制、全部规费或真实券商费率。']))
    config = result['config']
    report = {
        'format':'goose-investment-report','version':1,'id':str(uuid.uuid4()),
        'source':label,'asOf':data_date,'generatedAt':utc_stamp(clock),
        'strategy':'V3.2 / V1 previous-close-vs-MA20 / '+kind,
        'symbols':[dataset['symbol']],
        'signals':[{'symbol':dataset['symbol'],'stance':stance,
            'basis':basis + ('；完全虚构测试，不属于真实行情。' if synthetic else '；仅供截至数据日的历史研究。'),
            'risk':risk + '；' + '；'.join(limitations[:3]),
            'quality':'low' if synthetic or kind=='pre1445-test' else reference['signal'].get('quality','low')}],
        'backtest':{'from':result['from'],'to':result['to'],
            'returnBps':metrics['totalReturnBps'],'maxDrawdownBps':metrics['maxDrawdownBps'],
            'commissionBps':config['commissionBps'],'slippageBps':config['slippageBps'],
            'tradeCount':metrics['tradeCount'],'limitations':'；'.join(limitations)[:4000]},
        'paperTrades':[], 'notes':'；'.join(limitations)[:10000],
    }
    comparison=[]
    for name in NAMES:
        m=result['strategies'][name]['metrics']
        comparison.append({'strategy':name, **{k:m[k] for k in ['totalReturnBps','annualizedReturnBps','maxDrawdownBps','volatilityBps','tradeCount','cost']}})
    split={key:{'from':value['from'],'to':value['to']} for key,value in result['partitions'].items()}
    extended={**report,'id':str(uuid.uuid4()),'research':{
        'version':1,'dataset':{'source':dataset['source'],'sourceUrl':dataset['sourceUrl'],'through':data_date,
            'adjustment':dataset['adjustment'],'priceUnit':dataset['priceUnit'],'quality':q['status'],
            'crossValidated':q['crossValidated'],'unitVerified':q['unitVerified'],
            'sha256':sha256(canonical_bytes(dataset)).hexdigest()},
        'comparison':comparison,'split':split,'limitations':limitations,
        'reportKind':kind,'generatedBeforeCutoff':clock.astimezone(BJ).time().replace(tzinfo=None)<time(14,45),
        'decisionAllowed':False,'health':{'status':'synthetic' if synthetic else 'historical',
            'codes':['SYNTHETIC_NOT_MARKET_DATA'] if synthetic else ['NO_INTRADAY_QUOTE','SINGLE_SOURCE_NOT_INDEPENDENTLY_CROSS_VALIDATED']},
    }}
    return report, extended


def blocked_report(symbol, code, *, now=None):
    clock=now or datetime.now(timezone.utc)
    return {'format':'goose-investment-report','version':1,'id':str(uuid.uuid4()),
        'source':'V3.2 数据健康检查 / 未生成收益或今日交易信号',
        'asOf':clock.astimezone(BJ).date().isoformat(),'generatedAt':utc_stamp(clock),
        'strategy':'观察 / 不操作 / 数据不足','symbols':[symbol],
        'signals':[{'symbol':symbol,'stance':'watch','basis':f'核验未通过：{code}。此日期为诊断生成日期，并非行情日期。',
            'risk':'禁止用未核验行情计算收益、盈亏或当前买卖建议。','quality':'low'}],
        'backtest':None,'paperTrades':[],'notes':'未执行回测；无盘中数据；没有真实成交；未发送Bark；未运行或修改V1任务。'}


def read_v1_status(database):
    """Only aggregate timestamps/counts via SQLite ro, without opening Store.

    Active journal/WAL is rejected rather than making an incomplete immutable read.
    No position, cash, price, key, account ID or raw event text leaves this function.
    """
    path=Path(database).resolve(strict=True)
    if not path.is_file() or any(Path(str(path)+suffix).exists() for suffix in ['-wal','-shm','-journal']):
        raise ValueError('V1_SQLITE_BUSY_OR_JOURNAL_PRESENT')
    before=sha256(path.read_bytes()).hexdigest()
    connection=None
    try:
        connection=sqlite3.connect(path.as_uri()+'?mode=ro&immutable=1',uri=True)
        connection.execute('PRAGMA query_only=ON')
        tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        summary={'readOnly':True,'importedIntoBacktest':False,'lastRunAt':None,'lastFillAt':None}
        for table,key in [('events','lastRunAt'),('fills','lastFillAt')]:
            if table not in tables:continue
            columns={row[1] for row in connection.execute(f'PRAGMA table_info({table})')}
            column=next((name for name in ['created_at','ts','timestamp','time'] if name in columns),None)
            if column:summary[key]=connection.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
    except sqlite3.Error:
        raise ValueError('V1_SQLITE_READ_FAILED') from None
    finally:
        if connection is not None:connection.close()
    if any(Path(str(path)+suffix).exists() for suffix in ['-wal','-shm','-journal']):
        raise ValueError('V1_SQLITE_BUSY_OR_JOURNAL_PRESENT')
    if sha256(path.read_bytes()).hexdigest()!=before:
        raise ValueError('V1_DATABASE_CHANGED_DURING_READ_STOP')
    return summary


def pre1445_health(dataset, *, now=None, v1_status=None, elapsed_ms=0):
    clock=now or datetime.now(timezone.utc)
    if clock.tzinfo is None:raise ValueError('AWARE_CLOCK_REQUIRED')
    local=clock.astimezone(BJ)
    before=local.time().replace(tzinfo=None)<time(14,45)
    codes=['TEST_ONLY_NO_BARK_SENT','NO_INTRADAY_QUOTE','NO_TODAY_EXECUTABLE_SIGNAL']
    if not before:codes.append('AFTER_1445_NOT_A_PRE_CUTOFF_REPORT')
    if local.time().replace(tzinfo=None)<time(9,30) or local.time().replace(tzinfo=None)>time(15):codes.append('OUTSIDE_MARKET_OBSERVATION_WINDOW')
    from .market import xshg_sessions
    try:
        if local.date().isoformat() not in xshg_sessions(local.date().isoformat(),local.date().isoformat()):codes.append('NOT_AN_XSHG_SESSION')
    except ValueError:codes.append('CURRENT_CALENDAR_NOT_VERIFIED')
    if dataset['quality']['status']=='synthetic':codes.append('SYNTHETIC_NOT_MARKET_DATA')
    if dataset['quality']['status']=='blocked':codes.append('DATASET_BLOCKED')
    last=dataset['bars'][-1]['date']
    return {'format':'goose-pre1445-test','version':1,'generatedAt':utc_stamp(clock),
        'localDate':local.date().isoformat(),'generatedBeforeCutoff':before,'elapsedMs':elapsed_ms,'elapsedScope':'validate-backtest-export core; excludes process startup and input load',
        'dataHealth':{'symbol':dataset['symbol'],'source':dataset['source'],'through':last,
            'fetchedAt':dataset['fetchedAt'],'sourcePublishedAt':dataset['bars'][-1]['publishedAt'],
            'quality':dataset['quality'],'intradayQuoteAvailable':False},
        'strategyObservation':{'stance':'watch','decisionAllowed':False,'basis':'历史日线研究与盘中可执行信号分开；没有通过验证的当日分钟行情。'},
        'backtestResearch':{'included':dataset['quality']['status']!='blocked','separateAccount':True},
        'v1PaperStatus':v1_status or {'readOnly':True,'lastRunAt':None,'lastFillAt':None,'status':'not-requested'},
        'codes':codes,'notification':{'mode':'dry-run','sent':False,'phoneArrivalVerified':False},
        'barkPreview':{'title':'养鹅计划 / 测试模式','body':f"{dataset['symbol']} 数据截至{last}；观察、不操作；"+' / '.join(codes)},
        'scheduleChanged':False}

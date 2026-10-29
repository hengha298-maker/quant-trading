"""Explanatory opportunity watchlists, never order recommendations or sizing."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal as D,localcontext
from pathlib import Path
import json,re
from .radar_market import collect,catalog,complete_session
from .strategy_lab import cached_suite,indicators,pct,stats

def correlation(a,b):
    left={a[i]['date']:D(a[i]['close'])/D(a[i-1]['close'])-1 for i in range(1,len(a))}
    right={b[i]['date']:D(b[i]['close'])/D(b[i-1]['close'])-1 for i in range(1,len(b))}
    days=sorted(left.keys()&right.keys())[-60:]
    if len(days)<60:return None
    u=[left[d] for d in days];v=[right[d] for d in days];mu=sum(u)/len(u);mv=sum(v)/len(v)
    den=(sum((x-mu)**2 for x in u)*sum((y-mv)**2 for y in v)).sqrt()
    return sum((x-mu)*(y-mv) for x,y in zip(u,v))/den if den else None

def features(history,benchmark,held_histories):
    rows=history['adjusted']['bars'];raw=history['raw']['bars'];bench=benchmark['adjusted']['bars']
    if len(rows)<126 or [r['date'] for r in rows]!=[r['date'] for r in bench] or history['raw']['quality']['status']!='validated':raise ValueError('RADAR_FEATURE_HISTORY_INSUFFICIENT')
    signal=indicators(rows,bench,len(rows));close=D(rows[-1]['close']);ma5=sum(D(r['close']) for r in rows[-25:-5])/20
    peak=D(rows[-126]['close']);draw=D(0)
    for row in rows[-126:]:p=D(row['close']);peak=max(peak,p);draw=max(draw,(peak-p)/peak)
    prior_volume=sum(D(r['volume']) for r in raw[-20:-1])/19
    turnover=sum(D(r['close'])*r['volume'] for r in raw[-20:])/20
    correlations={s:correlation(rows,h['adjusted']['bars']) for s,h in held_histories.items() if h is not None}
    missing=[s for s in held_histories if s not in correlations or correlations[s] is None]
    maximum=max((v for v in correlations.values() if v is not None),default=None)
    raw20=sum(D(r['close']) for r in raw[-20:])/20;raw60=sum(D(r['close']) for r in raw[-60:])/60
    raw_bull=D(raw[-1]['close'])>raw20>raw60;adjusted_bull=close>signal['ma20']>signal['ma60']
    return {**signal,'momentum60':close/D(rows[-61]['close'])-1,'momentum120':close/D(rows[-121]['close'])-1,
      'slope20':signal['ma20']/ma5-1,'drawdown126':draw,'volumeRatio':D(raw[-1]['volume'])/prior_volume if prior_volume else None,
      'estimatedTurnover20':turnover,'rawClose':D(raw[-1]['close']),'rawMa20':raw20,'rawMa60':raw60,
      'adjustmentDirectionConflict':raw_bull!=adjusted_bull,'correlations':correlations,'missingHoldingCorrelations':missing,'maximumHoldingCorrelation':maximum}

def score(f,spec):
    parts=[]
    def add(name,maximum,earned,basis):parts.append({'factor':name,'maxPoints':maximum,'points':earned,'basis':basis})
    add('趋势',25,(10 if f['close']>f['ma20'] else 0)+(10 if f['ma20']>f['ma60'] else 0)+(5 if f['slope20']>0 else 0),f"前复权收盘{f['close']} / MA20 {f['ma20']:.6f} / MA60 {f['ma60']:.6f}；MA20五日变化{pct(f['slope20'])}%")
    add('动量与相对强弱',20,(5 if f['momentum20']>0 else 0)+(10 if f['relative60']>0 else 0)+(5 if f['momentum120']>0 else 0),f"20日{pct(f['momentum20'])}%，120日{pct(f['momentum120'])}%，60日相对沪深300ETF价格代理{pct(f['relative60'])}%")
    near=f['close']/f['prior20High']-1
    add('突破位置',10,10 if near>0 else 5 if near>=D('-.02') else 0,f"相对前20日最高{pct(near)}%；当日最高窗口排除信号日，不把临近高点称已突破")
    vr=f['volumeRatio'];add('量能',10,10 if vr is not None and D('.8')<=vr<=2 else 5 if vr is not None and D('.5')<=vr<=3 else 0,f"完整日成交量 / 前19日均量 {vr:.2f}" if vr is not None else '成交量不足')
    low=D('.25') if spec['kind']=='etf' else D('.35');high=D('.40') if spec['kind']=='etf' else D('.50')
    add('波动及回撤',15,(8 if f['vol60']<=low else 4 if f['vol60']<=high else 0)+(7 if f['drawdown126']<=D('.10') else 3 if f['drawdown126']<=D('.20') else 0),f"60日年化前复权价格波动{pct(f['vol60'])}%；126日最大回撤{pct(f['drawdown126'])}%")
    minimum=D(50000000) if spec['kind']=='etf' else D(100000000)
    add('流动性',10,10 if f['estimatedTurnover20']>=minimum*2 else 5 if f['estimatedTurnover20']>=minimum else 0,f"20日收盘×股/份成交量估算日成交额{f['estimatedTurnover20']:.2f}元，非实际成交额；门槛{minimum}元")
    c=f['maximumHoldingCorrelation'];add('组合分散',10,10 if c is not None and c<D('.60') else 5 if c is not None and c<D('.85') else 0,'与已确认持仓60共同日最大价格相关性 '+(format(c,'.3f') if c is not None else '未知，不加分'))
    return {'value':sum(p['points'] for p in parts),'maximum':100,'parts':parts,'meaning':'固定规则研究排序，不是未来收益概率；股票与ETF分开排序，未按留出收益调权。'}

def _values(f):
    return {k:format(v,'.6f') if isinstance(v,D) else {s:format(c,'.6f') if c is not None else None for s,c in v.items()} if k=='correlations' else v for k,v in f.items()}

def build(root,market,portfolio,*,deadline=None):
    if market.get('synthetic') is not False:raise ValueError('RADAR_SYNTHETIC_MARKET_NOT_PRODUCTION')
    cat=market['catalog'];histories=market['histories'];benchmark=histories.get(cat['benchmark']);held={p['symbol'] for p in portfolio.get('positions',[])}
    held_histories={s:histories.get(s) for s in held};items=[];quality_inputs=[]
    from .corporate_actions import catalog as event_catalog,evidence_status
    from .trust_research import research as trust_research
    from .strategy_quality import assess
    evidence_doc=event_catalog()
    for spec in cat['instruments']:
        symbol=spec['symbol'];history=histories.get(symbol);q=market['quality'][symbol]
        item={**{k:spec[k] for k in ('symbol','name','kind','category')},'through':market['through'],'isHeldInSnapshot':symbol in held,'quality':q,'eligible':False,'rejections':[],'score':None,'lab':None}
        item['trustResearch']={'status':'unavailable','dataTrustGrade':'C' if history else 'D','dataTrustLabel':'当前历史研究证据不完整' if history else '行情隔离，不参与排名',
          'sourceEvidence':evidence_status(evidence_doc,symbol,'2024-09-02',market['through'],root),'advantageConclusion':'尚未证明优于买入持有'}
        item['quarantineReview']=next((r for r in evidence_doc.get('quarantineReviews',[]) if r['symbol']==symbol),None)
        if not history or not benchmark:
            item['rejections'].append('历史、单位、复权或比较基准未通过核验，暂停排名。')
            # A dated raw-only study is not a substitute qfq dataset or a
            # restoration to today's watchlist. Require an audited interval.
            if benchmark and any(c['symbol']==symbol for c in evidence_doc['coverage']):
                try:
                    from .quarantine_review import archived_raw
                    archived=archived_raw(root,spec)
                    item['trustResearch'],_=trust_research(root,archived,benchmark,spec,deadline=deadline)
                    item['trustResearch']['dataTrustLabel']='仅供明确日期的未复权历史研究；当前行情/复权隔离未解除。'
                    item['archiveProvenance']=archived['archiveProvenance']
                except (ValueError,OSError,KeyError):item['trustResearch']['codes']=['ISOLATED_RAW_STUDY_NOT_AVAILABLE']
            items.append(item);continue
        try:
            with localcontext() as ctx:ctx.prec=45;f=features(history,benchmark,held_histories);s=score(f,spec)
            item.update(features=_values(f),score=s)
            minimum=D(50000000) if spec['kind']=='etf' else D(100000000)
            if s['value']<65:item['rejections'].append('研究分不足65。')
            if not f['close']>f['ma20']>f['ma60']:item['rejections'].append('完整日趋势未形成收盘高于MA20高于MA60。')
            if f['momentum20']<=0 or f['relative60']<0:item['rejections'].append('短期动量或相对市场强弱未满足。')
            if f['drawdown126']>(D('.25') if spec['kind']=='etf' else D('.35')) or f['vol60']>(D('.40') if spec['kind']=='etf' else D('.50')):item['rejections'].append('波动或回撤超过研究门槛。')
            if f['estimatedTurnover20']<minimum:item['rejections'].append('估算历史成交额不足，不保证可成交。')
            if f['adjustmentDirectionConflict']:item['rejections'].append('原始价与前复权趋势方向冲突，需核对企业行为。')
            if f['missingHoldingCorrelations'] or not held:item['rejections'].append('持仓相关性证据不完整，组合适配待核对。')
            if symbol in held:item['rejections'].append('已在确认持仓中，列入持仓研究，不冒充新增机会。')
            if f['maximumHoldingCorrelation'] is not None and f['maximumHoldingCorrelation']>=D('.90') and portfolio.get('concentration') is not None and D(portfolio['concentration'])>=50:item['rejections'].append('现有证券快照较集中且同向相关性高，优先核对重复暴露。')
            item['lab']=cached_suite(root,history,benchmark,spec,deadline=deadline)
            try:
                item['trustResearch'],study=trust_research(root,history,benchmark,spec,deadline=deadline)
                if study:quality_inputs.append({'symbol':symbol,'study':study})
            except (ValueError,OSError,KeyError):item['trustResearch']['codes']=['TRUST_RESEARCH_UNAVAILABLE_NO_COMPLETE_RETURN_CLAIM']
            item['eligible']=not item['rejections']
            item['reasons']=[p['basis'] for p in s['parts'] if p['factor'] in {'趋势','动量与相对强弱','量能','组合分散'}]
            item['trigger']='下一完整交易日继续保持同口径MA20/60趋势与相对强弱；当日盘中报价、资金、目标仓位和实际成本另行核对后才研究方案。'
            item['invalidation']='跌回MA20、60日相对动量转负、异常复权/单位/缺失日线，或账户证据改变时，原观察条件失效。'
            item['risks']=['排名仅用完整日历史；即使本次公开报价字段通过检查，也不是盘中可执行信号，持续可靠性未经验证。','前复权是供应商可修订价格序列；回测未计完整分红税，不是完整含息收益。','当前观察池有选择/幸存者偏差；历史留出不是前瞻验收，不按最佳历史策略直接实盘。']
        except (ValueError,OSError,KeyError) as exc:
            item['eligible']=False;item['rejections'].append('策略证据或计算未就绪：'+(str(exc) if re.fullmatch(r'[A-Z0-9_]{1,100}',str(exc)) else 'RADAR_COMPUTATION_INVALID'))
        items.append(item)
    expected_family=sum(4-(s['symbol']==cat['benchmark']) for s in cat['instruments'] if histories.get(s['symbol']) is not None)
    quality_result=assess(quality_inputs,deadline=deadline-timedelta(seconds=1) if deadline else None,expected_family_size=expected_family) if deadline is None or (deadline-datetime.now(timezone.utc)).total_seconds()>3 else {'status':'not-demonstrated','conclusion':'尚未证明优于买入持有','comparisons':[],'reason':'REPORT_TIME_RESERVED','familyComplete':False}
    for item in items:item['strategyQuality']=[r for r in quality_result['comparisons'] if r['symbol']==item['symbol']]
    groups={};coverage={}
    for kind,minimum in [('etf',5),('stock',4)]:
        valid=[i for i in items if i['kind']==kind and i['score'] is not None and i['lab'] is not None]
        ready=len(valid)>=minimum
        if not ready:
            for item in items:
                if item['kind']==kind:item['eligible']=False;item['rejections'].append('同类有效样本不足，拒绝生成不可靠排名。')
        candidates=sorted([i for i in valid if i['eligible']],key=lambda x:(-x['score']['value'],x['symbol']))[:5] if ready else []
        groups[kind]={'status':'ready' if ready else 'insufficient-valid-members','candidates':candidates,'message':'暂无符合条件的机会' if not candidates else '条件研究观察名单，非交易指令'}
        coverage[kind]={'configured':sum(s['kind']==kind for s in cat['instruments']),'historyValidated':sum(i['kind']==kind and histories.get(i['symbol']) is not None for i in items),'rankable':len(valid),'candidates':len(candidates)}
    benchmark_item=next((i for i in items if i['symbol']==cat['benchmark']),None)
    return {'format':'goose-opportunity-radar','version':1,'generatedAt':datetime.now(timezone.utc).isoformat(),'through':market['through'],
      'status':'ready' if all(g['status']=='ready' for g in groups.values()) and all(histories.get(s['symbol']) is not None for s in cat['instruments']) else 'partial','synthetic':False,'coverage':coverage,'groups':groups,'items':items,
      'strategyQuality':quality_result,
      'benchmark':{'symbol':cat['benchmark'],'basis':'沪深300ETF未复权价格路径代理，含基金跟踪费用影响，不等于指数总回报','comparison':benchmark_item['lab']['strategies']['buy_hold'] if benchmark_item and benchmark_item['lab'] else None},
      'accountConstraints':{'snapshotAsOf':portfolio.get('asOf'),'snapshotStatus':portfolio.get('status'),'securitiesConcentrationPct':portfolio.get('concentration'),
        'cashVerifiedCurrent':portfolio.get('status')=='current-user-confirmed' and portfolio.get('availableCash') is not None,'buyQuantity':None,'singleTradeBudget':None,
        'message':'资金、实际费率、近期现金需求及目标仓位未经核验，不计算可买数量；证券内部集中度不是家庭总资产仓位，基金成分重叠未核验。'},
      'catalogVersion':cat['version'],'catalogReviewedOn':cat['reviewedOn'],'selectionBias':cat['selection'],'independentFeedsVerified':False,
      'limitations':['只在维护的观察池中筛选，不是全市场扫描。','因子用最近完整交易日前复权价格；成交及回测估值严格用原始价，未混用复权。','固定权重与门槛非概率模型；无通过标的时不凑足5只。','新候选仅历史条件观察，不把上一收盘因子称为当日盘中可执行信号。']}

def generate(root,portfolio,*,now=None,deadline=None):
    now=now or datetime.now(timezone.utc)
    market=collect(root,now=now,deadline=deadline)
    return build(root,market,portfolio,deadline=deadline)

def unavailable(code):
    return {'format':'goose-opportunity-radar','version':1,'status':'unavailable','through':None,'generatedAt':datetime.now(timezone.utc).isoformat(),'synthetic':False,
      'groups':{k:{'candidates':[],'message':'本次雷达未完成，暂无经核验候选；未沿用旧名单。'} for k in ('etf','stock')},'items':[],'codes':[code]}

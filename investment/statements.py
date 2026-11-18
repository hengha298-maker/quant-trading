"""Explicit end-of-day broker statements; originals are never adjusted to fit."""
from pathlib import Path
import hashlib,json,uuid
from .holdings import _csv_rows,_xlsx_rows,_header,_decimal,_quantity,_symbol,_safe_path,_canonical,_now
from .ledger import day,fail,BROKERS,digest
from .private_store import private,record_paths,store_lock

FIELDS={'symbol':{'证券代码','股票代码','symbol'},'quantity':{'持仓数量','股票余额','证券数量','quantity'},
 'availableQuantity':{'可卖数量','可用数量','availablequantity'},'carryingCost':{'持仓成本金额','购入成本金额','carryingcost'},
 'marketValue':{'证券市值','持仓市值','市值','marketvalue'},'cashBalance':{'账面现金','资金余额','cashbalance'},
 'availableCash':{'可用资金','availablecash'},'frozenCash':{'冻结资金','frozencash'},
 'dividendReceivable':{'应收红利','dividendreceivable'},'reportedPnl':{'浮动盈亏','未实现盈亏','reportedpnl'}}
COST_BASES={'unknown','moving-average-acquisition-including-buy-fees'}

def parse_statement(raw,suffix,*,broker,as_of,cost_basis='unknown',complete_account=False):
    if broker not in BROKERS or cost_basis not in COST_BASES or type(complete_account) is not bool:fail('STATEMENT_OPTIONS','请核对券商及成本口径。')
    stamp=day(as_of)
    if suffix not in {'.csv','.xlsx'} or not isinstance(raw,bytes) or not 0<len(raw)<=5*1024*1024:fail('STATEMENT_FILE','请选择不超过5MiB的CSV/XLSX。')
    rows=_csv_rows(raw) if suffix=='.csv' else _xlsx_rows(raw);mapping=None
    for n,row in enumerate(rows[:20]):
        m={}
        for i,v in enumerate(row):
            for k,aliases in FIELDS.items():
                if _header(v) in aliases:
                    if k in m:fail('STATEMENT_COLUMNS','对账列重复。')
                    m[k]=i
        if {'symbol','quantity'}<=m.keys() or 'cashBalance' in m:mapping=m;offset=n+1;break
    if mapping is None:fail('STATEMENT_COLUMNS','需要持仓数量/代码或账面现金列；可用资金不能替代总现金。')
    positions=[];cash={k:None for k in ('cashBalance','availableCash','frozenCash','dividendReceivable')};seen=set()
    for row in rows[offset:]:
        if not any(str(v).strip() for v in row):continue
        get=lambda k:str(row[mapping[k]]).strip() if k in mapping and mapping[k]<len(row) else ''
        for k in cash:
            v=_decimal(get(k),k,1)
            if v is not None:
                if cash[k] is not None and cash[k]!=v:fail('STATEMENT_CASH_CONFLICT','同一快照现金金额冲突，不能自动求和。')
                cash[k]=v
        if not get('symbol'):continue
        s=_symbol(get('symbol'),1)
        if s in seen:fail('STATEMENT_DUPLICATE','对账快照证券重复。')
        seen.add(s);q=_quantity(get('quantity'),1);available=None
        if get('availableQuantity'):
            available=_quantity(get('availableQuantity'),1)
            if available>q:fail('STATEMENT_AVAILABLE','可卖数量大于持仓。')
        positions.append({'symbol':s,'quantity':q,'availableQuantity':available,
                          'carryingCost':_decimal(get('carryingCost'),'成本金额',1),
                          'reportedPnl':_decimal(get('reportedPnl'),'原报浮动盈亏',1,nonnegative=False),
                          'marketValue':_decimal(get('marketValue'),'市值',1)})
    if not positions and cash['cashBalance'] is None:fail('STATEMENT_EMPTY','没有可核对的持仓或账面现金。')
    doc={'format':'goose-ledger-statement','version':1,'broker':broker,'asOf':stamp,'asOfBasis':'end-of-day',
         'costBasis':cost_basis,'positions':positions,**cash,'generatedAt':_now().isoformat(),
         'completeAccountDeclared':complete_account,'accountScope':'ordinary-cash-listed-securities-no-margin-or-derivatives',
         'sourceSha256':hashlib.sha256(raw).hexdigest(),'sourceKind':suffix[1:],
         'fieldMapping':{k:str(rows[offset-1][i]) for k,i in mapping.items()},
         'warnings':['这是人工核对的期末对账证据，不覆盖原持仓；资金余额、可用资金与冻结资金分开。','除非明确确认购入成本含买入费口径，否则不据成本金额推算已实现盈亏。']}
    doc['sha256']=digest(doc);return doc

def validate(doc):
    if type(doc) is not dict or doc.get('format')!='goose-ledger-statement' or doc.get('version')!=1 or doc.get('sha256')!=digest({k:v for k,v in doc.items() if k not in {'sha256','statementId','confirmedAt'}}):fail('STATEMENT_INVALID','对账快照校验失败。')
    if doc.get('broker') not in BROKERS or doc.get('costBasis') not in COST_BASES or doc.get('asOfBasis')!='end-of-day':fail('STATEMENT_INVALID','对账口径无效。')
    if type(doc.get('completeAccountDeclared')) is not bool or doc.get('accountScope')!='ordinary-cash-listed-securities-no-margin-or-derivatives':fail('STATEMENT_INVALID','账户覆盖范围声明无效。')
    day(doc['asOf']);seen=set()
    if type(doc.get('positions')) is not list or len(doc['positions'])>5000:fail('STATEMENT_INVALID','持仓结构无效。')
    for p in doc['positions']:
        s=_symbol(p['symbol'],1);q=_quantity(p['quantity'],1)
        if s!=p['symbol'] or s in seen:fail('STATEMENT_INVALID','证券重复。')
        seen.add(s)
        if p['availableQuantity'] is not None and _quantity(p['availableQuantity'],1)>q:fail('STATEMENT_INVALID','可卖数量无效。')
        for k in ('carryingCost','marketValue'):
            if p.get(k) is not None:_decimal(p[k],k,1)
        if p.get('reportedPnl') is not None:_decimal(p['reportedPnl'],'原报浮动盈亏',1,nonnegative=False)
    for k in ('cashBalance','availableCash','frozenCash','dividendReceivable'):
        if doc.get(k) is not None:_decimal(doc[k],k,1)
    return doc

def load(root):
    result=[]
    for p in record_paths(private(root)/'ledger-statements'):
        try:doc=validate(json.loads(p.read_text(encoding='utf8')))
        except (KeyError,TypeError,ValueError):fail('STATEMENT_INVALID','已保存对账快照损坏，停止计算。')
        if p.stem!=doc.get('statementId'):fail('STATEMENT_INVALID','对账快照编号不一致。')
        result.append(doc)
    return result

def confirm(doc,root,confirmed=False):
    if confirmed is not True:fail('STATEMENT_CONFIRM','须先核对对账快照。')
    validate(doc)
    if not 0<=(_now()-__import__('datetime').datetime.fromisoformat(doc['generatedAt'])).total_seconds()<=3600:fail('STATEMENT_EXPIRED','对账预览已过期。')
    with store_lock(private(root)):
        if any(x['broker']==doc['broker'] and x['sourceSha256']==doc['sourceSha256'] and x['asOf']==doc['asOf'] for x in load(root)):fail('STATEMENT_DUPLICATE','同一对账快照已经保存。')
        value={**doc,'statementId':uuid.uuid4().hex,'confirmedAt':_now().isoformat()}
        folder=_safe_path(private(root)/'ledger-statements',missing=True);folder.mkdir(parents=True,exist_ok=True)
        with (folder/(value['statementId']+'.json')).open('xb') as f:f.write(_canonical(value)+b'\n');f.flush();__import__('os').fsync(f.fileno())
        return {'status':'saved','statementId':value['statementId']}

"""Confirmed broker events; append-only imports and explicitly provisional reconciliation.

No broker login or order API. Original position snapshots are never rewritten.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D,ROUND_HALF_UP
from pathlib import Path
import hashlib, json, os, re, uuid
from .holdings import (_csv_rows, _xlsx_rows, _decimal, _quantity, _symbol,
                       _header, _safe_path, _canonical, _now, HoldingsError)

BROKERS={'华宝证券','银河证券'}
KINDS={'买入':'BUY','证券买入':'BUY','BUY':'BUY','卖出':'SELL','证券卖出':'SELL','SELL':'SELL',
       '红利入账':'DIVIDEND','股息入账':'DIVIDEND','分红':'DIVIDEND','DIVIDEND':'DIVIDEND',
       '银证转入':'CASH_IN','资金转入':'CASH_IN','CASH_IN':'CASH_IN',
       '银证转出':'CASH_OUT','资金转出':'CASH_OUT','CASH_OUT':'CASH_OUT',
       '红利税':'TAX','税费':'TAX','TAX':'TAX','手续费':'FEE','FEE':'FEE'}
KINDS.update({'红利应收':'DIVIDEND_RECEIVABLE','DIVIDEND_RECEIVABLE':'DIVIDEND_RECEIVABLE',
 '确认股份变动':'POSITION_CHANGE','POSITION_CHANGE':'POSITION_CHANGE','交收资金':'SETTLEMENT','SETTLEMENT':'SETTLEMENT'})
ALIASES={'id':{'成交编号','交割编号','流水号','事件编号','executionid'},
 'date':{'成交日期','发生日期','交易日期','日期','date'},
 'kind':{'操作','业务名称','买卖方向','类型','kind'},
 'symbol':{'证券代码','股票代码','代码','symbol'},'quantity':{'成交数量','数量','quantity'},
 'price':{'成交价格','成交均价','价格','price'},'gross':{'成交金额','金额','gross'},
 'fees':{'总费用','费用合计','fees'},'netCash':{'资金发生额','净发生金额','netcash'},
 'reportedRealizedPnl':{'已实现盈亏','实现盈亏','reportedrealizedpnl'},'commission':{'佣金','手续费','commission'},'stampTax':{'印花税','stamptax'},'transferFee':{'过户费','transferfee'},'otherFees':{'其他费用','杂费','otherfees'}}
ALIASES.update({'tradeDate':{'交易日','交易日期','tradedate'},'executionDate':{'成交日','成交日期','executiondate'},
 'executionTime':{'成交时间','成交时刻','executiontime'},
 'settlementDate':{'结算日','结算日期','交收日期','settlementdate'},'cashDate':{'资金到账日','到账日期','资金发生日期','cashdate'},
 'linkId':{'关联成交编号','关联流水号','分红事件编号','linkid'},'includedInEventId':{'费用已含流水号','includedineventid'},
 'positionDelta':{'股份变动数量','positiondelta'},'costDelta':{'成本变动金额','costdelta'}})
# Date columns retain their business meanings. A generic date is not promoted to
# an actual settlement or cash-credit date.
ALIASES['date']={'发生日期','日期','date'}
ADAPTERS={'华宝证券':{'id':'huabao-explicit-fields-v2','verifiedAgainstRealExport':False},
          '银河证券':{'id':'galaxy-explicit-fields-v2','verifiedAgainstRealExport':False}}

def fail(code,message):raise HoldingsError(code,message)
def digest(x):return hashlib.sha256(_canonical(x)).hexdigest()
def money(x):return format(x.quantize(D('.01'),rounding=ROUND_HALF_UP),'f') if x is not None else None
def day(value,*,allow_future=False):
 value=str(value).strip().replace('/','-')
 if re.fullmatch(r'\d{8}',value):value=f'{value[:4]}-{value[4:6]}-{value[6:]}'
 try:parsed=date.fromisoformat(value)
 except ValueError:fail('EVENT_DATE_INVALID','成交日期应为YYYY-MM-DD或YYYYMMDD。')
 if parsed>_now().date() and not allow_future:fail('EVENT_DATE_FUTURE','不能把未来日期导入为已发生成交或已到账资金。')
 return parsed.isoformat()

def execution_time(value):
 if not value:return None
 value=str(value).strip()
 if re.fullmatch(r'\d{6}',value):value=value[:2]+':'+value[2:4]+':'+value[4:]
 if not re.fullmatch(r'\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?',value):fail('EVENT_TIME_INVALID','成交时间应为HH:MM:SS或HHMMSS。')
 try:__import__('datetime').time.fromisoformat(value)
 except ValueError:fail('EVENT_TIME_INVALID','成交时间不是有效北京时间。')
 return value.ljust(15,'0') if '.' in value else value+'.000000'

def parse_events(raw,suffix,*,broker,coverage_start,coverage_end,complete=False,source_role='combined',cash_sign='signed'):
 if broker not in BROKERS:fail('BROKER_INVALID','请明确选择华宝证券或银河证券。')
 if source_role not in {'combined','executions','cash'} or cash_sign not in {'signed','outflow-positive'}:fail('EVENT_ADAPTER_INVALID','请明确文件范围与资金正负号口径。')
 if suffix not in {'.csv','.xlsx'} or not isinstance(raw,bytes) or not 0<len(raw)<=5*1024*1024:fail('EVENT_FILE_INVALID','请选择不超过5MiB的CSV或XLSX纯数据导出。')
 start,end=day(coverage_start),day(coverage_end)
 if start>end or type(complete) is not bool:fail('EVENT_COVERAGE_INVALID','请核对导出起止日期及完整性声明。')
 rows=_csv_rows(raw) if suffix=='.csv' else _xlsx_rows(raw)
 mapping=None
 for n,row in enumerate(rows[:20]):
  m={}
  for col,value in enumerate(row):
   for key,aliases in ALIASES.items():
    if _header(value) in aliases:
     if key in m:fail('EVENT_DUPLICATE_COLUMN','存在重复同义列，请保留一列。')
     m[key]=col
  if {'id','kind'}<=m.keys() and {'date','tradeDate','executionDate','cashDate'}&m.keys():mapping=m;offset=n+1;break
 if mapping is None:fail('EVENT_HEADERS_MISSING','需要成交/流水编号、日期、业务类型；不以委托号代替逐笔成交号。')
 events=[];seen={}
 for number,row in enumerate(rows[offset:],offset+1):
  if not any(str(x).strip() for x in row):continue
  get=lambda k:str(row[mapping[k]]).strip() if k in mapping and mapping[k]<len(row) else ''
  event_id=get('id');kind=KINDS.get(get('kind').upper()) or KINDS.get(get('kind'))
  if not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',event_id) or kind is None:fail('EVENT_ID_OR_KIND_INVALID',f'第{number}行需可核对的唯一成交/流水号及受支持业务类型；不猜测撤单或未知业务。')
  original_kind=kind
  if source_role=='cash' and kind in {'BUY','SELL'}:kind='SETTLEMENT'
  dates={k:day(get(k),allow_future=k=='settlementDate') if get(k) else None for k in ('tradeDate','executionDate','settlementDate','cashDate')}
  stamp=dates['executionDate'] or dates['tradeDate'] or (day(get('date')) if get('date') else dates['cashDate'])
  if dates['tradeDate'] and dates['executionDate'] and dates['executionDate']<dates['tradeDate']:fail('EVENT_DATE_ORDER','成交日早于交易日，请核对字段。')
  if dates['settlementDate'] and dates['settlementDate']<(dates['executionDate'] or stamp):fail('EVENT_DATE_ORDER','结算日早于成交日，请核对字段。')
  if not start<=stamp<=end:fail('EVENT_OUTSIDE_COVERAGE',f'第{number}行不在声明的导出日期内。')
  symbol=_symbol(get('symbol'),number) if get('symbol') else None
  quantity=_quantity(get('quantity'),number) if kind in {'BUY','SELL'} else None
  price=_decimal(get('price'),'成交价格',number) if kind in {'BUY','SELL'} else None
  gross=_decimal(get('gross'),'成交金额',number)
  if kind=='SETTLEMENT' and gross is None and get('netCash'):gross=money(abs(D(_decimal(get('netCash'),'资金发生额',number,nonnegative=False))))
  if kind in {'BUY','SELL'} and (symbol is None or not quantity or price is None or D(price)<=0):fail('EVENT_TRADE_INVALID',f'第{number}行买卖需明确证券、正整数数量和成交价。')
  if kind in {'BUY','SELL'} and D(price)*quantity>D('1000000000000000'):fail('EVENT_AMOUNT_RANGE','成交名义金额超出允许范围，请核对价格单位和数量。')
  if kind in {'BUY','SELL'} and gross is None:gross=money(D(price)*quantity)
  if gross is None:fail('EVENT_AMOUNT_MISSING',f'第{number}行金额未知，不能保存为已确认资金事件。')
  if kind in {'BUY','SELL'} and abs(D(gross)-D(price)*quantity)>max(D('.02'),D(quantity)*D('.00005')):fail('EVENT_GROSS_CONFLICT',f'第{number}行成交数量、价格和金额不符，请核对精度和单位。')
  event={'id':event_id,'date':stamp,'kind':kind,'symbol':symbol,'quantity':quantity,'price':price,'gross':gross,
   'fees':_decimal(get('fees'),'总费用',number),'netCash':_decimal(get('netCash'),'资金发生额',number,nonnegative=False),
   'reportedRealizedPnl':_decimal(get('reportedRealizedPnl'),'已实现盈亏',number,nonnegative=False)}
  event.update(dates)
  event['executionTime']=execution_time(get('executionTime'))
  event.update(sourceRole=source_role,linkId=get('linkId') or None,includedInEventId=get('includedInEventId') or None,
   positionDelta=int(_decimal(get('positionDelta'),'股份变动数量',number,nonnegative=False)) if get('positionDelta') and D(_decimal(get('positionDelta'),'股份变动数量',number,nonnegative=False))==D(_decimal(get('positionDelta'),'股份变动数量',number,nonnegative=False)).to_integral_value() else None,
   costDelta=_decimal(get('costDelta'),'成本变动金额',number,nonnegative=False))
  for ref in (event['linkId'],event['includedInEventId']):
   if ref and not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',ref):fail('EVENT_REFERENCE_INVALID','关联编号格式无效。')
  if kind=='POSITION_CHANGE' and (not symbol or event['positionDelta'] is None):fail('EVENT_POSITION_CHANGE','股份变动须明确证券、整数变动数量及成本变动口径。')
  for k in ('commission','stampTax','transferFee','otherFees'):event[k]=_decimal(get(k),k,number)
  components=[event[k] for k in ('commission','stampTax','transferFee','otherFees')]
  if all(v is not None for v in components):
   total=sum(D(v) for v in components)
   if event['fees'] is not None and abs(total-D(event['fees']))>D('.01'):fail('EVENT_FEE_CONFLICT','费用总额与分项合计不一致，停止导入。')
   if event['fees'] is None:event['fees']=money(total)
  if cash_sign=='outflow-positive' and event['netCash'] is not None:event['netCash']=str(-D(event['netCash']))
  if event['netCash'] is not None and ((kind in {'BUY','CASH_OUT','TAX','FEE'} and D(event['netCash'])>0) or (kind in {'CASH_IN','DIVIDEND'} and D(event['netCash'])<0)):fail('EVENT_CASH_SIGN_INVALID','资金发生额方向与业务类型冲突，请按收入正、支出负核对。')
  if event['fees'] is not None and event['netCash'] is not None and kind in {'BUY','SELL','DIVIDEND'}:
   expected=-D(gross)-D(event['fees']) if kind=='BUY' else D(gross)-D(event['fees'])
   if abs(D(event['netCash'])-expected)>D('.01'):fail('EVENT_NET_CONFLICT','净发生额与金额、费用不符，不能自动修平。')
  if source_role=='cash' and original_kind in {'BUY','SELL'}:
   if not event['linkId']:fail('EVENT_SETTLEMENT_LINK','资金流水中的买卖交收须有明确关联成交编号，避免与成交文件重复计算。')
   event.update(originalKind=original_kind)
  key=stamp+'|'+event_id
  if key in seen:fail('EVENT_DUPLICATE_IN_FILE','同日成交/流水号重复；请先核对文件，不默默合并。')
  seen[key]=event;events.append(event)
 if not events or len(events)>5000:fail('EVENT_ROWS_INVALID','需要1至5000条事件；空导出不改变现有账户。')
 preview={'format':'goose-broker-event-preview','version':2,'broker':broker,'generatedAt':_now().isoformat(),
  'sourceSha256':hashlib.sha256(raw).hexdigest(),'sourceKind':suffix[1:],'coverageStart':start,'coverageEnd':end,
  'completeCoverageDeclared':complete,'events':events,'adapter':ADAPTERS[broker],
  'sourceRole':source_role,'cashSignConvention':cash_sign,'fieldMapping':{k:str(rows[offset-1][i]) for k,i in mapping.items()},
  'warnings':['编号、日期、数量、方向与费用须对照券商确认；不保存姓名、账号或原始文件。','费用未知不会按零；日期级基准无法判断同日成交是否已包含。','成交日、结算日和到账日分别保留，缺少到账日不宣称现金已到账。','字段映射尚待真实券商导出文件验收；未知业务停止导入。','成交推演不是券商实时核验，不据此推断可卖数量或可用资金。']}
 preview['sha256']=digest(preview);return preview

def store(root):return _safe_path(Path(root)/'investment/work/private/broker-events',missing=True)
def load_batches(root,include_void=False):
 from .private_store import record_paths
 directory=store(root)
 batches=[]
 for p in record_paths(directory):
  if p.name=='.import.lock':continue
  _safe_path(p,file=True)
  if p.suffix!='.json' or p.stat().st_size>5*1024*1024:fail('EVENT_STORE_INVALID','成交存储含未知文件，已停止读取。')
  try:b=json.loads(p.read_text(encoding='utf8'))
  except (ValueError,OSError):fail('EVENT_STORE_INVALID','成交文件无法验证，请从备份恢复；未跳过损坏记录。')
  if type(b) is not dict:fail('EVENT_STORE_INVALID','成交记录结构无效。')
  clean={k:v for k,v in b.items() if k not in {'batchId','confirmedAt','sha256'}}
  if p.stem!=b.get('batchId') or b.get('format')!='goose-broker-event-preview' or type(b.get('version')) is not int or b['version'] not in {1,2} or b.get('broker') not in BROKERS or b.get('sha256')!=digest(clean):fail('EVENT_STORE_INVALID','成交记录校验失败。')
  validate_events(b)
  batches.append(b)
 voiddir=_safe_path(Path(root)/'investment/work/private/broker-event-voids',missing=True);voided=set()
 if record_paths(voiddir):
  for p in record_paths(voiddir):
   _safe_path(p,file=True)
   try:v=json.loads(p.read_text(encoding='utf8'))
   except (ValueError,OSError):fail('EVENT_STORE_INVALID','撤销记录无法验证。')
   if type(v) is not dict or p.stem!=v.get('batchId') or v.get('format')!='goose-broker-event-void' or v.get('sha256')!=digest({k:x for k,x in v.items() if k!='sha256'}):fail('EVENT_STORE_INVALID','撤销记录校验失败。')
   voided.add(v['batchId'])
 if not voided<={b['batchId'] for b in batches}:fail('EVENT_STORE_INVALID','撤销记录缺少原批次，恢复资料不完整。')
 active=[b for b in batches if b['batchId'] not in voided];seen=set()
 for b in active:
  for e in b['events']:
   key=(b['broker'],e['date'],e['id'])
   if key in seen:fail('EVENT_STORE_INVALID','当前账本出现重复成交编号，停止推演，不重复累加。')
   seen.add(key)
 return batches if include_void else active

def undo(root,batch_id,confirmed=False):
 from .private_store import private,store_lock
 with store_lock(private(root)):return _undo(root,batch_id,confirmed)

def _undo(root,batch_id,confirmed=False):
 if confirmed is not True:fail('EVENT_CONFIRM_REQUIRED','撤销需明确确认；原记录不会删除。')
 if not re.fullmatch(r'[0-9a-f]{32}',str(batch_id)):fail('EVENT_BATCH_INVALID','批次编号无效。')
 directory=store(root);lock=directory/'.import.lock'
 try:fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except FileExistsError:fail('EVENT_STORE_BUSY','另一笔导入正在保存。')
 try:
  os.close(fd)
  if not any(b['batchId']==batch_id for b in load_batches(root)):fail('EVENT_BATCH_INVALID','批次不存在或已撤销。')
  out=_safe_path(Path(root)/'investment/work/private/broker-event-voids',missing=True);out.mkdir(parents=True,exist_ok=True)
  record={'format':'goose-broker-event-void','version':1,'batchId':batch_id,'confirmedAt':_now().isoformat(),'reason':'用户确认撤销误导入；原始记录保留'};record['sha256']=digest(record)
  with (out/(batch_id+'.json')).open('xb') as f:f.write(_canonical(record)+b'\n')
  return {'status':'voided-original-retained','batchId':batch_id}
 finally:lock.unlink()

def classified(preview,root):
 known={}
 for b in load_batches(root):
  for e in b['events']:known[(b['broker'],e['date'],e['id'])]=e
 fresh=[];duplicates=[];conflicts=[]
 for e in preview['events']:
  prior=known.get((preview['broker'],e['date'],e['id']))
  if prior is None:fresh.append(e)
  elif prior==e:duplicates.append(e['id'])
  else:conflicts.append(e['id'])
 return {'newEvents':fresh,'duplicateIds':duplicates,'conflictIds':conflicts}

def confirm(preview,root,confirmed=False):
 from .private_store import private,store_lock
 with store_lock(private(root)):return _confirm(preview,root,confirmed)

def _confirm(preview,root,confirmed=False):
 if confirmed is not True:fail('EVENT_CONFIRM_REQUIRED','请先核对预览并明确确认。')
 if not isinstance(preview,dict) or preview.get('sha256')!=digest({k:v for k,v in preview.items() if k!='sha256'}):fail('EVENT_PREVIEW_CHANGED','预览已改变，请重新导入。')
 validate_events(preview)
 try:age=(_now()-datetime.fromisoformat(preview['generatedAt'])).total_seconds()
 except (ValueError,KeyError,TypeError):fail('EVENT_PREVIEW_INVALID','预览时间无效。')
 if not 0<=age<=3600:fail('EVENT_PREVIEW_EXPIRED','预览超过一小时，请重新核对。')
 directory=store(root);directory.mkdir(parents=True,exist_ok=True);_safe_path(directory)
 lock=directory/'.import.lock'
 try:fd=os.open(lock,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
 except FileExistsError:fail('EVENT_STORE_BUSY','另一笔导入正在保存；中断锁不得自动清除。')
 try:
  os.close(fd);checked=classified(preview,root)
  if checked['conflictIds']:fail('EVENT_ID_CONFLICT','同日同一编号的内容冲突，未写入；请核对券商原件。')
  if not checked['newEvents']:fail('EVENT_ALREADY_IMPORTED','全部事件已存在，没有重复导入。')
  batch={**preview,'events':checked['newEvents']};batch.pop('sha256');batch['sha256']=digest(batch)
  batch['batchId']=uuid.uuid4().hex;batch['confirmedAt']=_now().isoformat()
  with (directory/(batch['batchId']+'.json')).open('xb') as f:f.write(_canonical(batch)+b'\n');f.flush();os.fsync(f.fileno())
  return {'status':'saved','batchId':batch['batchId'],'newCount':len(checked['newEvents']),'duplicateCount':len(checked['duplicateIds'])}
 finally:lock.unlink()

def validate_events(batch):
 if type(batch.get('events')) is not list or not 1<=len(batch['events'])<=5000:fail('EVENT_STORE_INVALID','成交事件数量异常。')
 start,end=day(batch['coverageStart']),day(batch['coverageEnd'])
 if start>end or type(batch.get('completeCoverageDeclared')) is not bool:fail('EVENT_STORE_INVALID','事件覆盖区间异常。')
 seen=set()
 for e in batch['events']:
  if type(e) is not dict or e.get('kind') not in set(KINDS.values()) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',str(e.get('id',''))):fail('EVENT_STORE_INVALID','事件编号或业务无效。')
  stamp=day(e['date'])
  if not start<=stamp<=end or (stamp,e['id']) in seen:fail('EVENT_STORE_INVALID','事件日期或重复编号无效。')
  seen.add((stamp,e['id']))
  if e.get('symbol') is not None and _symbol(e['symbol'],1)!=e['symbol']:fail('EVENT_STORE_INVALID','证券代码无效。')
  for k in ('gross','fees','commission','stampTax','transferFee','otherFees','price','netCash','reportedRealizedPnl','costDelta'):
   if e.get(k) is not None:
    if not isinstance(e[k],str) or _decimal(e[k],k,1,nonnegative=k not in {'netCash','reportedRealizedPnl','costDelta'}) is None:fail('EVENT_STORE_INVALID','事件金额字段无效。')
  if e.get('gross') is None:fail('EVENT_STORE_INVALID','事件金额缺失。')
  if e['kind'] in {'BUY','SELL'} and (type(e.get('quantity')) is not int or not 0<e['quantity']<=10**12 or not e.get('symbol') or e.get('price') is None or D(e['price'])<=0):fail('EVENT_STORE_INVALID','成交数量或价格无效。')
  for k in ('tradeDate','executionDate','settlementDate','cashDate'):
   if e.get(k) is not None:day(e[k],allow_future=k=='settlementDate')
  if e.get('executionTime') is not None:execution_time(e['executionTime'])
  if e.get('positionDelta') is not None and (type(e['positionDelta']) is not int or abs(e['positionDelta'])>10**12):fail('EVENT_STORE_INVALID','股份变动数量无效。')
  if e['kind']=='POSITION_CHANGE' and (not e.get('symbol') or e.get('positionDelta') is None):fail('EVENT_STORE_INVALID','股份变动信息不完整。')
  if e.get('sourceRole','combined') not in {'combined','executions','cash'}:fail('EVENT_STORE_INVALID','事件范围无效。')

def project(account,batches,through):
 """Independent execution and explicitly dated cash evidence; never live cash."""
 from .ledger_accounting import coverage,cash_postings
 base=account['asOf'][:10];broker=account['source']['broker'];positions={p['symbol']:p['quantity'] for p in account['positions']}
 all_events=[{**e,'sourceRole':e.get('sourceRole',b.get('sourceRole','combined'))} for b in batches if b['broker']==broker for e in b['events']]
 def execution_day(e):return e.get('executionDate') or e['date']
 trades=[e for e in all_events if e['sourceRole'] in {'executions','combined'} and e['kind'] in {'BUY','SELL','POSITION_CHANGE'} and base<execution_day(e)<=through]
 same=[e for e in all_events if e['sourceRole'] in {'executions','combined'} and e['kind'] in {'BUY','SELL','POSITION_CHANGE'} and execution_day(e)==base]
 relevant=[e for e in all_events if base<e['date']<=through or e.get('cashDate') and base<e['cashDate']<=through or e in trades]
 refs={e.get('linkId') for e in relevant if e['kind']=='SETTLEMENT'}
 context=relevant+[e for e in all_events if e['id'] in refs and e not in relevant]
 quantity_changes={};issues=[];realized=[]
 for e in trades:
  delta=e['positionDelta'] if e['kind']=='POSITION_CHANGE' else e['quantity'] if e['kind']=='BUY' else -e['quantity']
  quantity_changes[e['symbol']]=quantity_changes.get(e['symbol'],0)+delta
  if e['kind']=='SELL':realized.append(e.get('reportedRealizedPnl'))
 for symbol,delta in quantity_changes.items():positions[symbol]=positions.get(symbol,0)+delta
 negative=any(q<0 for q in positions.values())
 if negative:issues.append('推演出现负持仓，可能缺成交、公司行为或基准不正确；停止使用推演数量。')
 if same:issues.append('存在与基准快照同日的成交事件，未自动计入，避免重复计算；请补充更明确的基准。')
 domains={role:coverage(batches,broker,base,through,role) for role in ('executions','cash')}
 complete=domains['executions']['completeDeclared'] and not same
 if not complete:issues.append('成交覆盖不完整，不能认定持仓数量已更新完整。')
 if not domains['cash']['completeDeclared']:issues.append('现金覆盖不完整，未生成完整现金桥接。')
 cash_issues=[];postings=cash_postings(context,cash_issues)
 for e in relevant:
  if e['sourceRole']=='cash' and e['kind'] in {'BUY','SELL','POSITION_CHANGE'}:cash_issues.append({'code':'CASH_SOURCE_EXECUTION_INVALID','message':'现金来源不得充当成交或股份变动证据。'})
 posted=[p for p in postings if base<p['date']<=through]
 cash_known=domains['cash']['completeDeclared'] and not cash_issues
 cash_change=sum((p['cash'] for p in posted),D(0));cash=account.get('availableCash')
 return {'broker':broker,'baselineAsOf':account['asOf'],'lastUserVerifiedAt':account.get('confirmedAt'),'lastBrokerVerifiedAt':None,
  'through':through,'projectionStatus':'conflict' if negative else 'complete-declared-not-broker-verified' if complete else 'partial-events',
  'source':'用户核对的成交/资金事件推演，不是券商API','eventCount':len(relevant),'sameDayUnapplied':len(same),
  'projectedQuantities':None if negative else positions,'coverage':domains,'cashIssues':cash_issues,
  'cashProjectionStatus':'complete-declared-not-broker-verified' if cash_known else 'blocked-incomplete-evidence',
  'cashFlowChange':money(cash_change) if cash_known else None,
  'cashReconciliationBridge':money(D(cash)+cash_change) if cash is not None and cash_known else None,
  'cashBridgeBasis':'导入可用资金＋明确cashDate净发生额的算术桥；成交本身不是到账证据，冻结/清算未知，不是账户总现金或当前可用资金。',
  'bookCashProjection':None,'availableCash':None,'frozenCash':None,'sellableQuantity':None,
  'brokerReportedRealizedPnl':money(sum((D(v) for v in realized),D(0))) if complete and realized and all(v is not None for v in realized) else None,
  'warnings':issues+[i['message'] for i in cash_issues]+['账面资金推演不是可用资金；冻结、清算、红利税与缺失费用仍需券商核对。','持仓成本、已实现盈亏和可卖数量未凭成交价格猜测。']}

def reconcile(projection,actual):
 expected=projection.get('projectedQuantities');actual_map={p['symbol']:p['quantity'] for p in actual['positions']}
 if expected is None:return {'status':'blocked','rows':[],'warning':'推演存在冲突，不能对账。'}
 rows=[{'symbol':s,'projected':expected.get(s,0),'brokerSnapshot':actual_map.get(s,0),'difference':actual_map.get(s,0)-expected.get(s,0)} for s in sorted(set(expected)|set(actual_map))]
 return {'status':'quantity-matches' if all(r['difference']==0 for r in rows) else 'quantity-difference','rows':rows,'warning':'数量相符不证明成本、费用、资金、分红税或账户整体已经对平。'}

def account_views(root,accounts,through=None):
 from .ledger_accounting import overview
 batches=load_batches(root);through=through or _now().date().isoformat()
 return {'version':2,'reconciliation':overview(root),'accounts':[project(a,batches,through) for a in accounts],'batchCount':len(batches),'storedEventCount':sum(len(b['events']) for b in batches),'automaticBrokerSync':False,'batches':[{'batchId':b['batchId'],'broker':b['broker'],'coverageStart':b['coverageStart'],'coverageEnd':b['coverageEnd'],'eventCount':len(b['events'])} for b in batches]}

def apply_events(root,accounts,through=None):
 """Derived report input only. Never writes/promotes a broker snapshot or cost."""
 from copy import deepcopy
 batches=load_batches(root);through=through or _now().date().isoformat();result=deepcopy(accounts)
 for account in result:
  p=project(account,batches,through)
  if not p['eventCount']:continue
  broker=account['source']['broker'];base=account['asOf'][:10]
  relevant=[e for b in batches if b['broker']==broker for e in b['events'] if base<e['date']<=through]
  changed={e['symbol'] for e in relevant if e.get('symbol')}
  by_symbol={r['symbol']:r for r in account['positions']}
  complete=p['projectionStatus']=='complete-declared-not-broker-verified'
  for symbol in changed:
   row=by_symbol.get(symbol,{'symbol':symbol,'name':symbol,'quantity':None,'costPrice':None,'reportedMarketValue':None,'reportedPnl':None})
   baseline=deepcopy(row)
   row.update(quantity=p['projectedQuantities'].get(symbol,0) if complete else None,
              costPrice=None,reportedMarketValue=None,reportedPnl=None,availableQuantity=None,
              baselineSnapshotPosition=baseline,quantitySource='confirmed-events-derived-unreconciled' if complete else 'partial-events-quantity-unknown',quantityEvidenceThrough=through)
   by_symbol[symbol]=row
  account['positions']=list(by_symbol.values());account['baselineAvailableCash']=account.get('availableCash');account['availableCash']=None
  account['eventProjection']=p
 return result

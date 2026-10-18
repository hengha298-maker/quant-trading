import csv
from datetime import date, datetime, timezone, timedelta
import re
from .storage import local_path, root, save
from .validation import number, validate_snapshot

FIELDS = ['account', 'broker', 'as_of', 'coverage', 'cash', 'symbol', 'kind', 'quantity', 'available', 'broker_cost']


def parse(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != FIELDS:
            raise ValueError('CSV 表头须与模板完全一致')
        rows = list(reader)
    if not rows:
        raise ValueError('CSV 为空')
    accounts = {}
    seen = set()
    snapshot_date = None
    for r in rows:
        if None in r or any(v is None for v in r.values()):
            raise ValueError('列数不正确')
        if any(len(v) > 128 or any(ord(c) < 32 for c in v) for v in r.values()):
            raise ValueError('字段过长或含控制字符')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,32}', r['account']):
            raise ValueError('账户使用匿名代号，勿填真实账号')
        d = date.fromisoformat(r['as_of'])
        if d > datetime.now(timezone(timedelta(hours=8))).date() or (snapshot_date and d != snapshot_date):
            raise ValueError('快照日期必须一致且不能在未来')
        snapshot_date = d
        if r['coverage'] not in ('partial', 'complete') or not r['broker']:
            raise ValueError('账户完整性或券商字段无效')
        cash = None if r['cash'] == '' else str(number(r['cash']))
        meta = {k: r[k] for k in ('account', 'broker', 'coverage')}
        meta['cash'] = cash
        a = accounts.setdefault(r['account'], {**meta, 'positions': []})
        if any(a[k] != v for k, v in meta.items()):
            raise ValueError('同一账户元数据不一致')
        if r['coverage'] == 'complete' and cash is None:
            raise ValueError('完整账户必须填写现金')
        if not r['symbol']:
            if any(r[k] for k in ('kind', 'quantity', 'available', 'broker_cost')):
                raise ValueError('现金账户行不得填写持仓字段')
            continue
        if not re.fullmatch(r'\d{6}\.(SH|SZ)', r['symbol']) or r['kind'] not in ('stock', 'etf'):
            raise ValueError('证券代码或类别无效')
        key = (r['account'], r['symbol'])
        if key in seen:
            raise ValueError('同一账户证券重复')
        seen.add(key)
        qty, available = number(r['quantity'], True), number(r['available'], True)
        if qty <= 0 or available > qty:
            raise ValueError('持仓须大于零，可卖数量不能超过持仓')
        cost = None if r['broker_cost'] == '' else str(number(r['broker_cost'], signed=True))
        a['positions'].append(dict(symbol=r['symbol'], kind=r['kind'], quantity=int(qty), available=int(available), broker_cost=cost, cost_basis='broker_display'))
    return validate_snapshot(dict(schema_version=1, as_of=str(snapshot_date), accounts=list(accounts.values()), transaction_history_complete=False))


def import_csv(path, base):
    p = local_path(path)
    if root(base) not in p.parents:
        raise ValueError('CSV 必须保存在已确认的本地数据目录')
    document = parse(p)  # validate everything before any snapshot is created
    return save(base, 'snapshots', document)

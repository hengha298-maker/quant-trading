"""Validated, read-only projection for HTML and a future V2 local adapter.

No persistence, networking, estimated positions, or new market permissions.
Decimal values remain strings; unknown is None at this boundary, never zero.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from .usage import market_usage
from .validation import validate_snapshot, validate_market, require

BJ = timezone(timedelta(hours=8))
FOCUS = ('600900.SH', '515450.SH')
NAMES = {'600900.SH': '长江电力', '515450.SH': '515450 ETF'}


def build_view_model(snapshot, markets, *, generated_at=None, demo=False):
    validate_snapshot(snapshot)
    require(isinstance(markets, list), '行情输入必须为列表')
    require(type(demo) is bool)
    stamp = generated_at or datetime.now(timezone.utc)
    require(isinstance(stamp, datetime) and stamp.tzinfo is not None)
    prices, daily, audit = {}, {}, []
    for m in markets:
        validate_market(m)
        usage = market_usage(m)
        if usage['historical_cost_reference']:
            require(m['symbol'] not in prices, '同一证券不可提供多份日线；请显式选择采集批次')
            prices[m['symbol']] = m
        if m['interval'] == 'daily':
            daily.setdefault(m['symbol'], []).append(m)
        audit.append(dict(**{k: deepcopy(v) for k, v in m.items() if k != 'rows'}, allowed_usage=usage))
    accounts = deepcopy(snapshot['accounts'])
    positions = {}
    for a in accounts:
        for p in a['positions']:
            positions.setdefault(p['symbol'], []).append(dict(account=a['account'], broker=a['broker'],
                coverage=a['coverage'], **p))
    symbols = list(FOCUS) + sorted(set(positions) - set(FOCUS))
    cards = []
    for symbol in symbols:
        entries = positions.get(symbol, [])
        quotes = daily.get(symbol, [])
        quote = dict(state='missing', label='尚未提供日线', latest_date=None, fetched_at=None,
                     source=None, close=None, volume=None, historical_cost_reference=False,
                     reason='未选择该标的的日线文件；无法形成可靠建议。')
        if len(quotes) > 1:
            quote.update(state='ambiguous', label='行情批次待确认',
                         reason='选择了多份日线记录，请只保留一份明确的批次；不自动拼接或挑选。')
        elif quotes:
            m = quotes[0]
            if m['status'] == 'unavailable':
                quote.update(state='unavailable', label='行情不可用', source=m['source'], fetched_at=m['fetched_at'],
                             reason='本次采集未取得通过校验的日线；未沿用旧行情，无法形成可靠建议。')
            else:
                latest = m['rows'][-1]
                permitted = market_usage(m)['historical_cost_reference']
                quote.update(state='reference' if permitted else 'observation',
                    label='历史收盘参考' if permitted else '仅原始日线观察',
                    latest_date=latest['time'], fetched_at=m['fetched_at'], source=m['source'],
                    close=latest['close'], volume=latest['volume'], historical_cost_reference=permitted,
                    lag_days=(stamp.astimezone(BJ).date() - datetime.fromisoformat(latest['time']).date()).days,
                    same_snapshot_date=latest['time'] == snapshot['as_of'],
                    reason='仅可作为历史费用情景参考；估值和投资建议尚未开放。' if permitted else
                    '价格单位、成交量单位及服务端复权口径未独立核验；禁止用于持仓估值、费用计算、量价信号或交易建议。')
        cards.append(dict(symbol=symbol, name=NAMES.get(symbol, symbol), positions=entries,
            quantity=sum(p['quantity'] for p in entries) if entries else None,
            available=sum(p['available'] for p in entries) if entries else None,
            kind=entries[0]['kind'] if entries else ('stock' if symbol == '600900.SH' else 'etf'),
            quote=quote, market_value=None, profit_loss=None, advice=None))
    known_cash = [Decimal(a['cash']) for a in accounts if a['cash'] is not None]
    return dict(view_version=1, is_demo=demo, snapshot_as_of=snapshot['as_of'], snapshot_at=None,
        generated_at=stamp.isoformat(), accounts=accounts, holdings=cards,
        recorded_cash=dict(amount=str(sum(known_cash)) if known_cash else None,
            known_accounts=len(known_cash), unknown_accounts=len(accounts) - len(known_cash)),
        total_assets=dict(state='unavailable', amount=None,
            reason='存在未完整录入的账户，且当前未开放持仓估值，不能汇总整个真实账户资产。' if
            any(a['coverage'] != 'complete' for a in accounts) else
            '账户按快照展示；当前未开放持仓估值，不能计算完整总资产。'),
        position_count=sum(len(a['positions']) for a in accounts),
        security_count=len(positions), audit=audit,
        extensions=dict(asset_distribution=None, history=None, dividends=None))

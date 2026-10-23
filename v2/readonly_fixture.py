"""Only this in-code fictional fixture provider is connected to the HTTP server.

No file import, holdings path, network source, or user data entry point exists.
Numbers are deliberately different from any user-provided acceptance examples.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import secrets

SCENARIOS = ('normal', 'missing', 'stale')


def fictional_inputs(scenario, now):
    if scenario not in SCENARIOS:
        raise ValueError('unsupported fictional scenario')
    day = str((now.astimezone(timezone(timedelta(hours=8))) - timedelta(days=31 if scenario == 'stale' else 1)).date())
    snapshot = dict(schema_version=1, as_of=day, transaction_history_complete=False, accounts=[
        dict(account='FICTION_HB', broker='华宝 · 虚构测试', coverage='partial', cash='2500.00', positions=[
            dict(symbol='600900.SH', kind='stock', quantity=200, available=100, broker_cost='25.10', cost_basis='broker_display')]),
        dict(account='FICTION_YH', broker='银河 · 虚构测试', coverage='partial', cash=None, positions=[
            dict(symbol='515450.SH', kind='etf', quantity=6000, available=0, broker_cost=None, cost_basis='broker_display')])])
    def quote(symbol, source, price, adjustment):
        return dict(schema_version=1, symbol=symbol, interval='daily', source=source, adjustment=adjustment,
            fetched_at=day+'T09:00:00+00:00', requested_start=day, requested_end=day, status='observed',
            rows=[dict(time=day, open=price, close=price, high=price, low=price, volume='123000')], count=1,
            observed_start=day, observed_end=day, stale=False)
    stock = quote('600900.SH', '虚构历史参考来源', '25.50', '不复权')
    etf = quote('515450.SH', '新浪公开日线（AKShare 固定解码器）', '1.234', '源站原始价格（复权口径未核验）')
    etf.update(url='https://finance.sina.com.cn/realstock/company/sh515450/hisdata_klc2/klc_kl.js', provenance=dict(
        provider='sina', symbol='sh515450', response_sha256=hashlib.sha256(b'V2-FICTIONAL-ONLY').hexdigest(),
        decoder='akshare-1.19.1', raw_start=day, raw_end=day, raw_count=1,
        volume_unit='源站原始值（AKShare文档称手，未核验）', adjustment_verified=False, raw_order_verified=True,
        usage='raw_observation_only', api_rows_match=True, price_unit_verified=False, volume_unit_verified=False))
    return snapshot, [] if scenario == 'missing' else [stock, etf]


def build_fixture_view(p1, scenario='normal', *, now=None):
    now = now or datetime.now(timezone.utc)
    snapshot, markets = fictional_inputs(scenario, now)
    # These are the ORIGINAL pinned PR #3 functions, not browser replacements.
    p1.validation.validate_snapshot(snapshot)
    for m in markets:
        p1.validation.validate_market(m)
        policy = p1.usage.market_usage(m)
        if policy['position_valuation'] or policy['investment_advice']:
            raise ValueError('Unexpected P1 permission expansion')
    view = p1.model.build_view_model(snapshot, markets, generated_at=now, demo=True)
    plain = lambda value: None if value is None else format(Decimal(value), 'f')
    accounts = []
    for i, a in enumerate(view['accounts']):
        accounts.append(dict(id=f'account-{i+1}', broker=a['broker'], coverage=a['coverage'], cash=plain(a['cash']),
            positions=[dict(symbol=p['symbol'], kind=p['kind'], quantity=p['quantity'], available=p['available'],
                            broker_cost=plain(p['broker_cost'])) for p in a['positions']]))
    quotes = []
    for card in view['holdings']:
        q = card['quote']
        quotes.append(dict(symbol=card['symbol'], name=card['name'], state=q['state'], label=q['label'],
            close=plain(q['close']), close_unit='CNY' if q['state']=='reference' else None,
            source=q['source'], as_of=q['latest_date'], fetched_at=q['fetched_at'],
            lag_days=q.get('lag_days'), reason=q['reason'], historical_cost_reference=q['historical_cost_reference']))
    return dict(api_version=1, mode='real_readonly', test_data=True, p1_revision=p1.revision,
        bundle_id='fixture-'+secrets.token_hex(8), snapshot_as_of=view['snapshot_as_of'], snapshot_at=None,
        generated_at=view['generated_at'], state='stale' if scenario=='stale' else 'historical',
        accounts=accounts, quotes=quotes, known_cash=plain(view['recorded_cash']['amount']),
        unknown_cash_accounts=view['recorded_cash']['unknown_accounts'],
        permissions=dict(valuation=False, profit_loss=False, advice=False), total_assets=None,
        strategies=[dict(id='long-term', title='长期加仓', status='unavailable'),
                    dict(id='intraday', title='日内做T', status='unavailable')],
        reports=[dict(id='current', title='当前持仓与行情状态', status='snapshot_only')])

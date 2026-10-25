"""Allowlisted transport projection of the original P1 model; no calculations."""
from datetime import datetime, timezone
from decimal import Decimal
import secrets


def project_view(p1, snapshot, markets, *, test_data, now=None, state='historical'):
    now = now or datetime.now(timezone.utc)
    p1.validation.validate_snapshot(snapshot)
    for market in markets:
        p1.validation.validate_market(market)
        policy = p1.usage.market_usage(market)
        if policy['position_valuation'] or policy['investment_advice']:
            raise ValueError('Unexpected P1 permission expansion')
    view = p1.model.build_view_model(snapshot, markets, generated_at=now, demo=test_data)
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
    return dict(api_version=1, mode='real_readonly', test_data=test_data, p1_revision=p1.revision,
        bundle_id=('fixture-' if test_data else 'private-')+secrets.token_hex(8), snapshot_as_of=view['snapshot_as_of'], snapshot_at=None,
        generated_at=view['generated_at'], state=state,
        accounts=accounts, quotes=quotes, known_cash=plain(view['recorded_cash']['amount']),
        unknown_cash_accounts=view['recorded_cash']['unknown_accounts'],
        permissions=dict(valuation=False, profit_loss=False, advice=False), total_assets=None,
        strategies=[dict(id='long-term', title='长期加仓', status='unavailable'),
                    dict(id='intraday', title='日内做T', status='unavailable')],
        reports=[dict(id='current', title='当前持仓与行情状态', status='snapshot_only')])

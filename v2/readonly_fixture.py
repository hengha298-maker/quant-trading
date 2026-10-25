"""Only this in-code fictional fixture provider is connected to the HTTP server.

No file import, holdings path, network source, or user data entry point exists.
Numbers are deliberately different from any user-provided acceptance examples.
"""
from datetime import datetime, timedelta, timezone
import hashlib

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
    from readonly_projection import project_view
    return project_view(p1, snapshot, markets, test_data=True, now=now,
                        state='stale' if scenario == 'stale' else 'historical')

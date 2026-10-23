"""Explicitly fictional offline UI preview. Never reads holdings or network."""
import argparse
from hashlib import sha256
from pathlib import Path
import tempfile
import sys
import webbrowser
from .report import render


def fixtures():
    """All values below are invented for UI testing, not Sina observations."""
    def position(symbol, kind, quantity, available, cost):
        return dict(symbol=symbol, kind=kind, quantity=quantity, available=available,
                    broker_cost=cost, cost_basis='broker_display')
    snapshot = dict(schema_version=1, as_of='2026-10-20', transaction_history_complete=False,
        accounts=[dict(account='HB_DEMO', broker='华宝 · 虚构演示', coverage='partial', cash='12980.50', positions=[
            position('600900.SH', 'stock', 400, 300, '27.84'),
            position('515450.SH', 'etf', 900, 800, '1.28')]),
        dict(account='YH_DEMO', broker='银河 · 虚构演示', coverage='partial', cash=None, positions=[
            position('600900.SH', 'stock', 100, 100, '28.25'),
            position('515450.SH', 'etf', 600, 400, None)])])
    def quote(symbol, source, adjustment, price):
        return dict(schema_version=1, symbol=symbol, interval='daily', source=source, adjustment=adjustment,
            fetched_at='2026-10-20T09:05:00+00:00', requested_start='2026-10-19', requested_end='2026-10-20',
            status='observed', count=1, observed_start='2026-10-20', observed_end='2026-10-20', stale=False,
            rows=[dict(time='2026-10-20', open=price, close=price, high=price, low=price, volume='4000000')])
    stock = quote('600900.SH', '虚构演示 · 历史费用参考情景', '不复权', '28.36')
    etf = quote('515450.SH', '新浪公开日线（AKShare 固定解码器）', '源站原始价格（复权口径未核验）', '1.333')
    etf.update(url='https://finance.sina.com.cn/realstock/company/sh515450/hisdata_klc2/klc_kl.js',
        provenance=dict(provider='sina', symbol='sh515450', response_sha256=sha256(b'FICTIONAL_UI_FIXTURE').hexdigest(),
            decoder='akshare-1.19.1', raw_start='2026-10-20', raw_end='2026-10-20', raw_count=1,
            volume_unit='源站原始值（AKShare文档称手，未核验）', adjustment_verified=False, raw_order_verified=True,
            usage='raw_observation_only', api_rows_match=True, price_unit_verified=False, volume_unit_verified=False))
    return snapshot, [stock, etf]


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='只生成虚构演示；不读真实数据，不联网')
    parser.add_argument('--no-open', action='store_true', help='生成后不打开浏览器')
    args = parser.parse_args(argv)
    snapshot, markets = fixtures()
    folder = Path(tempfile.mkdtemp(prefix='GooseP1-Fictional-Demo-'))
    path = folder / 'fictional-demo.html'
    path.write_text(render(snapshot, markets, demo=True), encoding='utf-8')
    print('虚构演示报告已生成，不包含真实持仓。')
    print(path)
    if not args.no_open:
        webbrowser.open(path.as_uri())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

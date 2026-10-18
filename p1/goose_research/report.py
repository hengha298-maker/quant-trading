from datetime import datetime, timezone
from decimal import Decimal
from html import escape
import json
from uuid import uuid4
from .storage import root, read_private, local_path
from .fees import scenario, PROFILES
from .validation import validate_snapshot, validate_market, require


def render(snapshot, markets):
    validate_snapshot(snapshot)
    require(isinstance(markets, list), '行情输入必须为列表')
    for market in markets:
        validate_market(market)
    # Only explicit supplied daily observations can serve as historical reference prices.
    prices = {}
    for m in markets:
        if m.get('status') == 'observed' and m.get('interval') == 'daily' and m.get('rows'):
            if m['symbol'] in prices:
                raise ValueError('同一证券不可提供多份日线；请显式选择采集批次')
            prices[m['symbol']] = m
    sections = []
    for a in snapshot['accounts']:
        lines = [f"账户 {a['account']} / {a['broker']} / {a['coverage']}"]
        cash = '未知' if a['cash'] is None else str(a['cash']) + ' 元'
        lines.append('可用现金：' + cash + '（按快照录入，未知不估算）')
        if not a['positions']:
            lines.append('无持仓（仅现金账户）' if a['coverage'] == 'complete' else '尚无持仓录入，不代表账户实际空仓')
        lines.append('持仓未完整录入；以下不是整个真实账户的资产配置比例。' if a['coverage'] != 'complete' else '用户声明完整账户；P1 仍不输出全账户配置比例。')
        for p in a['positions']:
            kind = '股票' if p['kind'] == 'stock' else 'ETF'
            lines.append(f"{p['symbol']} / 资产类型：{kind} / 总数量：{p['quantity']} / 可卖数量（真实录入）：{p['available']}")
            lines.append(f"券商显示成本：{p['broker_cost'] if p['broker_cost'] is not None else '未知'} 元/股或份；快照日期：{snapshot['as_of']}（未记录日内时刻）")
            m = prices.get(p['symbol'])
            if not m:
                lines.append('不可用：缺少有效日线，不能计算交易成本参考情景。')
                continue
            lines.append(f"参考收盘 {m['rows'][-1]['close']} / {m['rows'][-1]['time']}；非实时行情；是否与快照同日：{m['rows'][-1]['time'][:10] == snapshot['as_of']}")
            lines.append('软件估算情景：100股/份；可卖数量沿用真实录入值，不估算为当前可卖量')
            for profile in PROFILES:
                if profile == 'yinhe_etf_example' and p['kind'] != 'etf':
                    continue
                for direction in ('buy_then_sell', 'sell_then_buy'):
                    result = scenario(p['kind'], m['rows'][-1]['close'], 100, p['available'], a['cash'], direction, profile)
                    order = '先买后卖' if direction == 'buy_then_sell' else '先卖后买'
                    lines.append(f"{profile}（示例非实际）/ {order}：往返费用 {result['round_trip_cost']} 元；盈亏平衡价差 {Decimal(result['break_even_spread']):.4f} 元/股份；" + ('不可用：' + '；'.join(result['constraints']) if result['constraints'] else '仅费用情景，实际交易可行性未知'))
        sections.append('<pre>' + escape('\n'.join(lines)) + '</pre>')
    facts = json.dumps([{k: v for k, v in m.items() if k != 'rows'} for m in markets], ensure_ascii=False, indent=2)
    return '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>养鹅计划 P1 只读研究</title><style>body{font:16px system-ui;max-width:1000px;margin:2em auto;padding:1em}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f7;padding:1em}h2{color:#234}</style>
<h1>养鹅计划 V2.0-P1 只读研究</h1>''' + '<p>快照 ' + escape(snapshot['as_of']) + '；报告生成 UTC ' + escape(datetime.now(timezone.utc).isoformat()) + '''</p>
<h2>行情事实</h2><p>历史数据，不是实时可成交价格。日线默认请求三年；范围、条数和失败状态如下，未证实完整性。无分钟数据时分钟研究不可用。</p><pre>''' + escape(facts) + '''</pre>
<h2>费用假设</h2><p>全部为示例配置，实际费率未知。华宝股票/ETF免五、银河ETF免五按用户描述建模；银河ETF示例佣金较低不代表实际报价。保守示例故意保留最低5元压力项，非券商实际规则。佣金、税费、滑点均须核实；滑点为每腿名义金额比例。</p><pre>''' + escape(json.dumps(PROFILES, ensure_ascii=False, indent=2)) + '''</pre>
<h2>研究结论：仅交易成本与约束情景</h2><p>软件估算情景：100股/份同价往返研究，与真实录入的总数量和可卖数量分开展示，盈亏平衡价差仅为费用估计。两种底仓T顺序均保留，未生成交易信号。</p>''' + ''.join(sections) + '''
<h2>不可用状态与口径限制</h2><p>无完整流水，不计算真实累计收益率。成本采用券商显示口径，历史分红和做T会影响口径，未知成本保留未知。未计算历史累计收益或投资建议。快照可卖数量仅对应快照时点，不代表今天可卖。分钟可用范围以实际采集为准；缺数据不补造。完整交易日审计、实际费率、停牌涨跌停及撮合验证尚未完成。</p></html>'''


def generate(base, snapshot_path, market_paths):
    snapshot = read_private(snapshot_path, base, 'snapshot')
    markets = [read_private(p, base, 'market') for p in market_paths]
    document = render(snapshot, markets)
    directory = local_path(root(base) / 'reports')
    if root(base) not in directory.parents:
        raise ValueError('报告目录不得链接至数据目录之外')
    directory.mkdir(exist_ok=True)
    dest = directory / (uuid4().hex + '.html')
    with dest.open('x', encoding='utf-8') as f:
        f.write(document)
    return dest

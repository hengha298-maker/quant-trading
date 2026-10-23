"""Self-contained Chinese dashboard; no scripts, assets, server or network."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from html import escape
import json
from pathlib import Path
from uuid import uuid4
from .storage import root, read_private, local_path
from .fees import scenario, PROFILES
from .usage import market_usage
from .report_model import build_view_model


def _e(value):
    return escape(str(value), quote=True)


def _amount(value):
    return '未知' if value is None else format(Decimal(value), ',.2f')


def _time(value):
    if value is None:
        return '未记录'
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')


def _account(a):
    complete = a['coverage'] == 'complete'
    state = '已声明完整' if complete else '部分录入'
    note = f"已录入 {len(a['positions'])} 项持仓 · 现金已录入" if a['cash'] is not None else f"已录入 {len(a['positions'])} 项持仓 · 现金未知"
    if not a['positions']:
        note = '无持仓（仅现金账户）' if complete else '尚无持仓录入，不代表账户实际空仓'
    return f'''<article class="account-card"><div class="card-heading"><h3>{_e(a['broker'])}<span class="account-id">{_e(a['account'])}</span></h3><span class="pill{'' if complete else ' warn'}">{state}</span></div>
<p class="cash">可用现金：<strong>{_amount(a['cash'])}{' 元' if a['cash'] is not None else ''}</strong></p><p>{note}</p></article>'''


def _holding(card, model):
    unit = '股' if card['kind'] == 'stock' else '份'
    q = card['quote']
    parts = []
    for p in card['positions']:
        parts.append(f'''<div class="position"><p class="position-title"><strong>{_e(p['broker'])}</strong> · {_e(p['account'])}</p><dl class="facts">
<div><dt>总数量</dt><dd>{p['quantity']:,}<small>{unit}</small></dd></div>
<div><dt>可卖数量 · 快照</dt><dd>{p['available']:,}<small>{unit}</small></dd></div>
<div><dt>券商显示成本 · 元/{unit}</dt><dd class="{'unknown' if p['broker_cost'] is None else ''}">{_amount(p['broker_cost'])}</dd></div></dl></div>''')
    if not parts:
        parts.append('<p class="empty">未录入该标的持仓，不代表实际未持有。</p>')
    if q['close'] is not None:
        raw = q['state'] == 'observation'
        label = '源站原始收盘数值 · 单位未核验' if raw else f'历史参考收盘 · 元/{unit}'
        parts.append(f'''<div class="quote{' warn' if raw else ''}"><div class="meta-row"><span>{label}</span><strong class="{'raw' if raw else ''}">{_e(q['close'])}</strong></div>
<p>行情日期 {_e(q['latest_date'])} · 非实时</p>{'<p class="explain">仅观察，不用于估值、盈亏或投资建议。</p>' if raw else '<p>通过历史费用参考资格，不代表可用于持仓估值或交易。</p>'}</div>''')
    else:
        parts.append(f'<div class="quote warn"><span>{_e(q["label"])}</span><p>{_e(q["reason"])}</p></div>')
    if q['latest_date']:
        freshness = f"截至 {q['latest_date']}；距本次报告日期 {q['lag_days']} 自然日；未核验交易日历，非实时。"
        freshness += ' 行情与快照同日。' if q['same_snapshot_date'] else ' 行情与持仓快照日期不同。'
    else:
        freshness = '没有通过校验且可明确选择的日线，无法形成可靠建议。'
    source = ('虚构演示 · ' if model['is_demo'] else '') + (q['source'] or '尚未提供')
    parts.append(f'''<details><summary>数据来源与使用限制</summary><p>资产类型：{'股票' if card['kind'] == 'stock' else 'ETF'}；快照日期：{model['snapshot_as_of']}（未记录日内时刻）。可卖数量仅对应该快照，不代表今天可卖。</p>
<p>{_e(freshness)}</p><p>来源：{_e(source)}；采集时间：{_time(q['fetched_at'])}（北京时间）。</p><p>{_e(q['reason'])}</p>
{'<p>原始成交量数值：' + _e(q['volume']) + '；单位未核验，不换算为手或份。</p>' if q['state'] == 'observation' else ''}
<p>市值、盈亏和投资建议：尚不可用，未计算。</p></details>''')
    return f'''<article class="holding-card"><div class="card-heading"><div><h3>{_e(card['name'])}</h3><p class="stock-code">{_e(card['symbol'])}</p></div><span class="pill{' warn' if q['state'] != 'reference' else ''}">{_e(q['label'])}</span></div>{''.join(parts)}</article>'''


def render(snapshot, markets, *, demo=False):
    model = build_view_model(snapshot, markets, generated_at=datetime.now(timezone.utc), demo=demo)
    accounts = ''.join(_account(a) for a in model['accounts'])
    holdings = ''.join(_holding(c, model) for c in model['holdings'])
    cash = model['recorded_cash']
    cash_note = f"{cash['known_accounts']} 个账户现金已知"
    if cash['unknown_accounts']:
        cash_note += f" · {cash['unknown_accounts']} 个未知，未按零计算"
    else:
        cash_note += ' · 仅已录入现金合计'
    quote_dates = sorted({c['quote']['latest_date'] for c in model['holdings'] if c['quote']['latest_date']})
    date_note = ' / '.join(quote_dates) if quote_dates else '尚无可用日线'
    audit = ('<pre>' + _e(json.dumps(model['audit'], ensure_ascii=False, indent=2)) + '</pre>') if model['audit'] else '<p>尚未附加行情文件，暂无审计数据。</p>'
    css = Path(__file__).with_name('report.css').read_text(encoding='utf-8')
    demo_banner = '<div class="demo" role="note"><strong>虚构演示 · 非真实账户</strong>　所有持仓、价格、费用情景和来源审计均为演示；不代表实时行情或投资建议。</div>' if demo else ''
    completeness = '存在部分录入账户，以下不是整个真实账户的资产配置比例。' if any(a['coverage'] != 'complete' for a in model['accounts']) else '账户完整性来自用户声明，尚未进行全资产核对。'
    etf_quote = next(c['quote'] for c in model['holdings'] if c['symbol'] == '515450.SH')
    etf_title = '515450 · ' + etf_quote['label']
    etf_note = ('价格单位、成交量单位及服务端复权口径未独立核验。备用源复权口径尚未核验，仅展示行情事实，不计算费用参考；不换算成交量，不用于估值、量价信号或交易建议。'
                if etf_quote['state'] == 'observation' else etf_quote['reason'])
    cost_symbols = {c['symbol'] for c in model['holdings'] if c['quote']['historical_cost_reference']}
    record_note = '按虚构演示快照展示，缺失数据保留未知。' if demo else '按真实录入的快照展示，缺失数据保留未知。'
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>{'虚构演示 · ' if demo else ''}养鹅计划｜本地资产报告</title><style>{css}</style></head><body><div class="shell">
<header class="topbar"><div class="brand"><span class="mark" aria-hidden="true">鹅</span><div><strong>养鹅计划</strong><small>个人投资 · 本地只读</small></div></div><nav class="nav" aria-label="报告导航"><a href="#accounts">账户</a><a href="#holdings">持仓</a><a href="#data">数据</a></nav><span class="local">离线可读 · 无交易连接</span></header>
{demo_banner}<main><div class="intro"><div><p class="eyebrow">我的投资记录 / P1</p><h1>资产与持仓，一眼看清</h1><p>{record_note}</p></div><div class="date"><span>持仓快照 <strong>{model['snapshot_as_of']}</strong> · 未记录日内时刻</span><br><span>报告生成 {_time(model['generated_at'])} · 北京时间</span></div></div>
<section class="summary-grid" aria-label="资产状态"><div class="hero"><div class="label">总资产状态</div><h2>暂无法完整估值</h2><p>{_e(model['total_assets']['reason'])}</p><span class="tag">未计算市值与盈亏 · 未生成交易建议</span></div>
<div class="metric"><div class="label">已知现金 · 已录入账户</div><div class="amount">{_amount(cash['amount'])}<span>{' 元' if cash['amount'] is not None else ''}</span></div><p>{cash_note}</p></div>
<div class="metric"><div class="label">持仓记录概览</div><div class="inline-stats"><div><strong>{len(model['accounts'])}</strong><span>已录入账户</span></div><div><strong>{model['security_count']}</strong><span>已录入标的</span></div></div><p>{model['position_count']} 项分账户持仓 · 不推算未录入资产</p></div></section>
<section class="section" id="accounts"><div class="section-heading"><div><h2>账户概览</h2><p>分账户记录，现金与可卖数量以快照为准</p></div><a href="#data">查看数据口径 ↓</a></div><div class="account-grid">{accounts}</div><div class="notice">{completeness} 券商显示成本不等于市值；不以未知成本或现金补零。</div></section>
<section class="section" id="holdings"><div class="section-heading"><div><h2>我的持仓</h2><p>分账户保留数量、可卖数量和成本</p></div><span class="muted">只读快照</span></div><div class="holding-grid">{holdings}</div></section>
<section class="section data-panel" id="data"><div class="section-heading"><div><h2>数据状态与提醒</h2><p>持仓、行情和报告生成时间分别记录</p></div></div><div class="data-grid"><div><div class="data-item"><strong>持仓快照 · {model['snapshot_as_of']}</strong><p>未记录日内时刻；快照可卖数量不代表当前可卖量。无完整流水，不计算真实累计收益。</p></div><div class="data-item"><strong>行情事实 · {_e(date_note)}</strong><p>全部为历史数据，不是实时可成交价格。只使用显式选择的批次，缺失时不沿用旧行情。</p></div></div><div><div class="data-item"><strong>{_e(etf_title)}</strong><p>{_e(etf_note)}</p></div><div class="data-item"><strong>研究状态 · 无法形成可靠建议</strong><p>长期加仓与日内做T均未形成经验证的操作建议，任何真实交易由用户自行确认。</p></div></div></div><div class="future"><span>资产分布 · 尚未接入</span><span>历史变化 · 尚未接入</span><span>分红记录 · 尚未接入</span></div></section>
<details class="advanced" id="advanced"><summary>高级信息<small>费用假设、研究情景与 JSON 审计 · 默认隐藏</small></summary>
<h3>费用假设（全部为示例，非实际费率）</h3><p>佣金、最低佣金、印花税、过户费和滑点均为示例配置，实际费率未知。不得用作实际收费承诺。只有通过历史费用参考资格的数据才进入下列情景。</p><pre>{_e(json.dumps(PROFILES, ensure_ascii=False, indent=2))}</pre>
<h3>费用与底仓约束情景</h3><p>软件估算情景：100股/份；同价两腿费用参考，保留先买后卖和先卖后买，不生成交易信号。</p>{_cost_details(snapshot, markets, cost_symbols)}
<h3>JSON 来源审计</h3><p>{'以下审计字段全部虚构，仅用于UI和权限验证。' if demo else '只展示选定行情的审计信息；无行情时不输出空数组。'} observed仅表示当批采集校验通过，不代表所有用途获准、三年完整或策略有效。</p>{audit}</details>
</main><footer class="footer"><span>养鹅计划 · P1本地资产报告</span><span>仅本地查看 · 无外部资源 · 不接入券商交易</span></footer></div></body></html>'''


def _cost_details(snapshot, markets, eligible_symbols):
    # Only explicit supplied daily observations can serve as historical reference prices.
    prices = {}
    for m in markets:
        if m['symbol'] in eligible_symbols and market_usage(m)['historical_cost_reference']:
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
                unknown_adjustment = any(m['symbol'] == p['symbol'] and m['status'] == 'observed' and
                                         m['adjustment'] != '不复权' for m in markets)
                lines.append('不可用：备用源复权口径尚未核验，仅展示行情事实，不计算费用参考。' if unknown_adjustment
                             else '不可用：缺少有效日线，不能计算交易成本参考情景。')
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
    return ''.join(sections)


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

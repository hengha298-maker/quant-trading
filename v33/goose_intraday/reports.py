"""Compatible observation-only phone report; no new phone database schema."""
from datetime import datetime, timezone
from hashlib import sha256
import json
from uuid import uuid4
from goose_research.market import BJ


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def phone_report(health):
    clock = datetime.fromisoformat(health['generatedAt'])
    historical = [datetime.fromisoformat(x['sourceTimestamp']).astimezone(BJ).date().isoformat()
        for x in health['observations'] if x['sourceTimestamp'] and datetime.fromisoformat(x['sourceTimestamp']) <= clock]
    # Never re-date an old quote as today's market data. If there is no quote,
    # asOf is explicitly a diagnostic date, with no price or market-time claim.
    day = min(historical) if historical else health['calendarContext']['date']
    basis = '行情健康观察；' + ('检查项已通过，仍需盘中人工核验。' if health['manualWindowChecksPassed'] else '盘中行情未验证。')
    for item in health['observations']:
        basis += f"{item['source']}：来源时间{item['sourceTimestamp'] or '未知'}，新鲜度秒{item['ageSeconds'] if item['ageSeconds'] is not None else '未知'}；"
    history = health['historyContext']
    if history['available']:
        basis += f"前一完整交易日{history['through']}收盘{history['previousClose']}元，对含该日20日MA{history['ma20']}元为{history['comparison']}；不是均线交叉，不把盘中报价代替前收盘。"
    else:
        basis += '缺少有效的上一交易日20日历史上下文；不产生策略买卖结论。'
    basis += '本条仅观察、不操作；没有已认证最后成交时间。'
    notes = ['报告来源时间是分钟标签或供应商更新时间，绝不是抓取时间冒充成交时间。',
        '本健康快照按generatedAt检查；最终文件完成是否仍在14:45前须核对run.json。',
        'asOf为来源日期；无来源时仅为诊断日期，不代表取得当日行情。',
        '没有模拟或真实成交、没有Bark发送、没有新增或修改任务。',
        '人工验收、行情持续可靠性和交易决策权限均未自动认证。',
        'health SHA256=' + sha256(canonical(health)).hexdigest(),
        '诊断码：' + ' / '.join(health['codes']), *health['limitations']]
    if health['mode'] != 'live':
        notes.insert(0, '离线重放或完全虚构工程测试，不能证明真实盘中行情。')
    return {'format': 'goose-investment-report', 'version': 1, 'id': str(uuid4()),
        'source': ('V3.3 行情健康诊断' if health['mode'] == 'live' else 'V3.3 离线/虚构测试') + ' / 非买卖信号',
        'asOf': day, 'generatedAt': clock.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z'),
        'strategy': 'V3.3 / 观察、不操作 / 仅数据健康研究',
        'symbols': ['600900.SH'], 'signals': [{'symbol': '600900.SH', 'stance': 'watch',
            'basis': basis[:2000], 'risk': '报价更新时间不等于最后成交时间；盘外/陈旧/失败数据不能生成今天的买卖信号；无自动交易权限。',
            'quality': 'low'}], 'backtest': None, 'paperTrades': [], 'notes': '；'.join(notes)[:10000]}


def markdown(health):
    lines = ['# V3.3 当日行情健康观察', '', f"生成时间：{health['generatedAt']}",
        f"状态：{health['status']}。研究结论：**观察、不操作**。", '',
        '**盘中可靠性、人工验收与真实交易权限均未认证。未发送Bark。**', '',
        '| 来源 | 来源时间 | 时间语义 | 抓取时刻 | 价格(元/股，仅来源观察) | 年龄秒 | 检查通过 |',
        '|---|---|---|---|---:|---:|---|']
    for item in health['observations']:
        lines.append('| ' + ' | '.join(str(x) if x is not None else '未知' for x in [item['source'],
            item['sourceTimestamp'], item['timestampKind'], item.get('receivedAt'), item['price'],
            item['ageSeconds'], item['checksPassed']]) + ' |')
    lines += ['', '最后成交时刻：未知。分钟标签与供应商报价更新时间不能冒充成交时刻。', '',
        '14:35—14:45本次检查候选：' + str(health['manualWindowChecksPassed']),
        '两源独立性：未证明。数值对照：' + json.dumps(health['comparison'], ensure_ascii=False), '',
        '诊断码：' + ' / '.join(health['codes']), '', '## 限制']
    lines += ['- ' + value for value in health['limitations']]
    return '\n'.join(lines) + '\n'

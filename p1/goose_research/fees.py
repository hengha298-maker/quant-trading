from decimal import Decimal, ROUND_HALF_UP
from .holdings import number

# Entirely illustrative, including tax/transfer/slippage. NOT actual broker rates.
PROFILES = {
    'huabao_example': dict(commission='0.0003', minimum='0', stock_tax='0.0005', transfer='0.00001', slippage='0.001'),
    'yinhe_etf_example': dict(commission='0.0002', minimum='0', stock_tax='0.0005', transfer='0.00001', slippage='0.001'),
    'conservative_example': dict(commission='0.001', minimum='5', stock_tax='0.001', transfer='0.00002', slippage='0.003'),
}


def money(n):
    return n.quantize(Decimal('.01'), rounding=ROUND_HALF_UP)


def scenario(kind, price, quantity, available, cash, direction, profile):
    if direction not in ('buy_then_sell', 'sell_then_buy') or kind not in ('stock', 'etf'):
        raise ValueError('情景类别无效')
    if profile == 'yinhe_etf_example' and kind != 'etf':
        raise ValueError('银河免五示例仅适用于 ETF')
    p = PROFILES[profile]
    price, quantity, available = number(price), number(quantity, True), number(available, True)
    if price <= 0 or quantity <= 0 or quantity % 100:
        raise ValueError('价格须正数、数量须为100的正整数倍')
    notional = money(price * quantity)
    commission = money(max(notional * Decimal(p['commission']), Decimal(p['minimum'])))
    transfer = money(notional * Decimal(p['transfer'])) if kind == 'stock' else Decimal(0)
    tax = money(notional * Decimal(p['stock_tax'])) if kind == 'stock' else Decimal(0)
    slip = money(notional * Decimal(p['slippage'])) * 2
    total = commission * 2 + transfer * 2 + tax + slip
    reasons = []
    if quantity > available:
        reasons.append('T+1：已有可卖底仓不足；当天新买不能用于当日卖出')
    needed = notional + commission + transfer + slip / 2
    if cash is None:
        reasons.append('现金未知，无法验证资金约束')
    elif direction == 'buy_then_sell' and number(cash) < needed:
        reasons.append('先买所需现金不足')
    elif direction == 'sell_then_buy' and number(cash) + notional - commission - transfer - tax - slip / 2 < needed:
        reasons.append('同价卖后回买资金不足')
    return dict(profile=profile, assumption='示例假设，非实际费率', direction=direction,
                notional=str(notional), round_trip_cost=str(total), break_even_spread=str(total / quantity),
                constraints=reasons, status='unavailable' if reasons else 'scenario_only',
                limitation='同价两腿成本估计；不验证涨跌停、停牌、盘口、成交、资金交收或未来价格；不是交易信号')

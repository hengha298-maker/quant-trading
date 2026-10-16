"""All money is integer fen. SQLite transaction covers account/order/fill changes."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import json
import math
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

BJ = ZoneInfo("Asia/Shanghai")


def fen(value):
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("价格/资金必须为有限正数")
    return int((number * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


@dataclass(frozen=True)
class Config:
    mode: str = "paper"
    initial_cash: float = 100000
    etf_symbol: str | None = None
    lookback: int = 20
    order_lots: int = 1
    max_position: float = .30
    commission_rate: float = .0003
    min_commission: float = 5
    stock_sell_tax: float = .0005
    stock_transfer_rate: float = .00001
    max_quote_age_seconds: int = 120

    def __post_init__(self):
        if self.mode != "paper":
            raise ValueError("仅支持 paper，禁止实盘")
        fen(self.initial_cash)
        for key in ("lookback", "order_lots", "max_quote_age_seconds"):
            if type(getattr(self, key)) is not int or getattr(self, key) <= 0:
                raise ValueError(key)
        if self.lookback < 2 or self.max_quote_age_seconds > 300:
            raise ValueError("均线至少2日，行情期限不得超过300秒")
        if not math.isfinite(self.max_position) or not 0 < self.max_position <= 1:
            raise ValueError("仓位上限必须在(0,1]")
        for key in ("commission_rate", "min_commission", "stock_sell_tax", "stock_transfer_rate"):
            v = getattr(self, key)
            if not math.isfinite(v) or v < 0:
                raise ValueError(key)
        if self.etf_symbol is not None and (not re.fullmatch(r"\d{6}\.(SH|SZ)", self.etf_symbol) or self.etf_symbol == "600900.SH"):
            raise ValueError("ETF代码须为六位代码.SH/SZ且不能与股票重复")

    @property
    def symbols(self):
        return ["600900.SH"] + ([self.etf_symbol] if self.etf_symbol else [])


@dataclass(frozen=True)
class Quote:
    symbol: str
    timestamp: datetime
    price: float
    volume: float


class Store:
    def __init__(self, path, cfg):
        self.db = sqlite3.connect(path, timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS account(id INTEGER PRIMARY KEY CHECK(id=1), cash INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS positions(symbol TEXT PRIMARY KEY, qty INTEGER NOT NULL, bought_day TEXT);
        CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY, day TEXT, symbol TEXT, side TEXT,
          qty INTEGER, price INTEGER, fee INTEGER, status TEXT, reason TEXT,
          UNIQUE(day,symbol));
        CREATE TABLE IF NOT EXISTS fills(order_id INTEGER PRIMARY KEY, timestamp TEXT, label TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, timestamp TEXT, level TEXT, message TEXT);
        ''')
        self.db.execute("INSERT OR IGNORE INTO account VALUES(1,?)", (fen(cfg.initial_cash),))

    def event(self, now, level, message):
        self.db.execute("INSERT INTO events(timestamp,level,message) VALUES(?,?,?)", (now.isoformat(), level, message))

    def snapshot(self):
        return {"cash_fen": self.db.execute("SELECT cash FROM account").fetchone()[0],
                "positions": [dict(r) for r in self.db.execute("SELECT * FROM positions")],
                "orders": [dict(r) for r in self.db.execute("SELECT * FROM orders")],
                "fills": [dict(r) for r in self.db.execute("SELECT * FROM fills")]}


def valid_quote(q, symbol, now, cfg):
    if q.symbol != symbol or q.timestamp.tzinfo is None:
        raise ValueError("行情代码/时区错误")
    stamp = q.timestamp.astimezone(BJ)
    age = (now - stamp).total_seconds()
    clock = stamp.strftime("%H:%M:%S")
    if stamp.date() != now.date() or not 0 <= age <= cfg.max_quote_age_seconds:
        raise ValueError("行情过期或来自未来")
    if not ("09:30:00" <= clock <= "11:30:00" or "13:00:00" <= clock <= "15:00:00"):
        raise ValueError("行情不在连续交易时段")
    if fen(q.price) <= 0 or not math.isfinite(q.volume) or q.volume <= 0:
        raise ValueError("行情价格/成交量无效")


def execute(store, cfg, now, symbol, side, quotes):
    """One decision per symbol/day, even rejected decisions; concurrent runs serialized."""
    db = store.db
    day = now.date().isoformat()
    q = quotes[symbol]
    price = fen(q.price)
    waiting_started = time.monotonic()
    db.execute("BEGIN IMMEDIATE")
    try:
        now = now + timedelta(seconds=time.monotonic() - waiting_started)
        clock = now.strftime("%H:%M:%S")
        if now.date().isoformat() != day or not ("09:32:00" <= clock <= "11:29:59" or "13:02:00" <= clock <= "14:59:59"):
            raise ValueError("下单时间已超出交易时段")
        for code, quote in quotes.items():
            valid_quote(quote, code, now, cfg)
        if db.execute("SELECT 1 FROM orders WHERE day=? AND symbol=?", (day, symbol)).fetchone():
            db.execute("COMMIT")
            return "DUPLICATE"
        cash = db.execute("SELECT cash FROM account").fetchone()[0]
        positions = {r["symbol"]: r for r in db.execute("SELECT * FROM positions")}
        held = positions.get(symbol)
        qty = cfg.order_lots * 100 if side == "BUY" else (held["qty"] if held else 0)
        notional = price * qty
        commission = max(Decimal(str(cfg.min_commission)) * 100, Decimal(notional) * Decimal(str(cfg.commission_rate)))
        extra = Decimal(0)
        if symbol == "600900.SH":
            extra = Decimal(notional) * (Decimal(str(cfg.stock_transfer_rate)) + (Decimal(str(cfg.stock_sell_tax)) if side == "SELL" else 0))
        fee = int((commission + extra).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        reason = ""
        if side not in ("BUY", "SELL"):
            raise ValueError("无效方向")
        if side == "BUY":
            equity = cash + sum(r["qty"] * fen(quotes[s].price) for s, r in positions.items())
            current = (held["qty"] if held else 0) * price
            if cash < notional + fee:
                reason = "INSUFFICIENT_CASH"
            elif current + notional > (equity - fee) * cfg.max_position:
                reason = "POSITION_LIMIT"
        elif qty == 0:
            reason = "NO_POSITION"
        elif held["bought_day"] == day:
            reason = "T_PLUS_ONE"
        elif notional <= fee:
            reason = "PROCEEDS_BELOW_FEE"
        status = "REJECTED" if reason else "FILLED"
        cur = db.execute("INSERT INTO orders(day,symbol,side,qty,price,fee,status,reason) VALUES(?,?,?,?,?,?,?,?)", (day, symbol, side, qty, price, fee, status, reason))
        if not reason:
            cash += -notional - fee if side == "BUY" else notional - fee
            db.execute("UPDATE account SET cash=? WHERE id=1", (cash,))
            if side == "BUY":
                db.execute("INSERT INTO positions VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET qty=qty+excluded.qty,bought_day=excluded.bought_day", (symbol, qty, day))
            else:
                db.execute("DELETE FROM positions WHERE symbol=?", (symbol,))
            db.execute("INSERT INTO fills VALUES(?,?,?)", (cur.lastrowid, now.isoformat(), "PAPER_SYNTHETIC_MINUTE_PRICE"))
        db.execute("COMMIT")
        return reason or status
    except BaseException:
        db.execute("ROLLBACK")
        raise


def run(store, cfg, provider, calendar, now, *, outcomes=None):
    if now.tzinfo is None:
        raise ValueError("运行时间必须含时区")
    now = now.astimezone(BJ)
    started = time.monotonic()
    try:
        if not calendar.is_session(now.date()):
            store.event(now, "INFO", "SKIP_NON_TRADING_DAY")
            return "SKIP_NON_TRADING_DAY"
        clock = now.strftime("%H:%M:%S")
        # Skip first minute: minute bars may be incomplete.
        if not ("09:32:00" <= clock <= "11:29:59" or "13:02:00" <= clock <= "14:59:59"):
            store.event(now, "INFO", "SKIP_OUTSIDE_SESSION")
            return "SKIP_OUTSIDE_SESSION"
        previous = calendar.previous(now.date(), cfg.lookback)
        if len(previous) != cfg.lookback or previous != sorted(set(previous)) or previous[-1] >= now.date():
            raise ValueError("交易日历窗口不足或包含当日/未来日期")
        quotes, signals = {}, {}
        # Fetch/validate entire portfolio before any order. No stale cache fallback.
        for symbol in cfg.symbols:
            bars = provider.history(symbol, previous[0], previous[-1])
            if set(bars) != set(previous):
                raise ValueError(f"{symbol}: 历史行情缺失/日期异常")
            closes = [fen(bars[d]) for d in previous]
            signals[symbol] = "BUY" if closes[-1] > sum(closes) / len(closes) else "SELL" if closes[-1] < sum(closes) / len(closes) else "HOLD"
            quotes[symbol] = provider.quote(symbol, now)
            valid_quote(quotes[symbol], symbol, now, cfg)
        unknown = {r[0] for r in store.db.execute("SELECT symbol FROM positions")} - set(cfg.symbols)
        if unknown:
            raise ValueError("账户中有配置外持仓，请恢复原配置后运行")
        store.event(now, "INFO", json.dumps({"config": cfg.__dict__, "history_days": [str(d) for d in previous],
            "quotes": {s: {"time": q.timestamp.isoformat(), "price": q.price, "volume": q.volume} for s, q in quotes.items()}}, ensure_ascii=False))
        # Optional caller-owned outcomes retain committed decisions if a later symbol fails.
        results = outcomes if outcomes is not None else {}
        for symbol, side in signals.items():
            if side != "HOLD":
                results[symbol] = execute(store, cfg, now + timedelta(seconds=time.monotonic() - started), symbol, side, quotes)
        store.event(now, "INFO", json.dumps({"signals": signals, "results": results}, ensure_ascii=False))
        return results
    except Exception as exc:
        store.event(now, "ERROR", f"禁止继续下单: {type(exc).__name__}: {exc}")
        raise

from datetime import datetime, date, timedelta
import sqlite3
import pytest
from quant_paper.core import BJ, Config, Store, Quote, run, execute

NOW = datetime(2026, 10, 15, 14, 50, tzinfo=BJ)
DAYS = [date(2026, 10, 13), date(2026, 10, 14)]


class Calendar:
    def is_session(self, day):
        return day.weekday() < 5

    def previous(self, day, count):
        return DAYS


class Provider:
    def history(self, symbol, start, end):
        return dict(zip(DAYS, [9, 10]))

    def quote(self, symbol, now):
        return Quote(symbol, now - timedelta(seconds=60), 10, 100)


@pytest.fixture
def setup(tmp_path):
    cfg = Config(lookback=2)
    store = Store(tmp_path / 'paper.db', cfg)
    yield cfg, store, tmp_path / 'paper.db'
    store.db.close()


def test_fill_and_restart_duplicate(setup):
    cfg, store, path = setup
    assert run(store, cfg, Provider(), Calendar(), NOW)['600900.SH'] == 'FILLED'
    snap = store.snapshot()
    assert snap['cash_fen'] == 9_899_499
    assert snap['positions'][0]['qty'] == 100
    assert len(snap['fills']) == 1
    other = Store(path, Config(initial_cash=1, lookback=2))
    assert other.snapshot() == snap
    assert run(other, cfg, Provider(), Calendar(), NOW)['600900.SH'] == 'DUPLICATE'
    assert other.snapshot() == snap
    other.db.close()


def test_insufficient_cash(tmp_path):
    cfg = Config(initial_cash=1000, lookback=2, max_position=1)
    store = Store(tmp_path / 'small.db', cfg)
    assert run(store, cfg, Provider(), Calendar(), NOW)['600900.SH'] == 'INSUFFICIENT_CASH'
    assert store.snapshot()['cash_fen'] == 100000
    assert store.snapshot()['fills'] == []
    assert run(store, cfg, Provider(), Calendar(), NOW)['600900.SH'] == 'DUPLICATE'
    store.db.close()


def test_position_limit(setup):
    cfg, store, _ = setup
    cfg = Config(lookback=2, max_position=.005)
    assert run(store, cfg, Provider(), Calendar(), NOW)['600900.SH'] == 'POSITION_LIMIT'
    assert not store.snapshot()['positions']


@pytest.mark.parametrize('kind', ['missing', 'stale', 'future', 'nan', 'zero_volume', 'wrong_symbol', 'failure', 'calendar'])
def test_bad_data_blocks_orders_and_logs(setup, kind):
    cfg, store, _ = setup
    class Bad(Provider):
        def history(self, *args):
            if kind == 'missing':
                return {DAYS[0]: 10}
            if kind == 'failure':
                raise OSError('source down')
            return super().history(*args)

        def quote(self, symbol, now):
            return Quote('000000.SZ' if kind == 'wrong_symbol' else symbol,
                         now + timedelta(seconds=60) if kind == 'future' else now - timedelta(days=1) if kind == 'stale' else now,
                         float('nan') if kind == 'nan' else 10,
                         0 if kind == 'zero_volume' else 100)
    class BadCalendar(Calendar):
        def is_session(self, day):
            if kind == 'calendar':
                raise ValueError('calendar out of bounds')
            return True
    with pytest.raises((ValueError, OSError)):
        run(store, cfg, Bad(), BadCalendar(), NOW)
    assert not store.snapshot()['orders']
    assert store.db.execute("SELECT level FROM events ORDER BY id DESC").fetchone()[0] == 'ERROR'


def test_non_trading_day_no_fetch(setup):
    cfg, store, _ = setup
    assert run(store, cfg, None, Calendar(), datetime(2026, 10, 18, 14, 50, tzinfo=BJ)) == 'SKIP_NON_TRADING_DAY'
    assert not store.snapshot()['orders']


@pytest.mark.parametrize('hour,minute', [(8,0),(12,0),(15,0),(9,30),(13,0)])
def test_outside_session(setup, hour, minute):
    cfg, store, _ = setup
    assert run(store, cfg, None, Calendar(), NOW.replace(hour=hour, minute=minute)) == 'SKIP_OUTSIDE_SESSION'


def test_beijing_timezone(setup):
    cfg, store, _ = setup
    from datetime import timezone
    assert run(store, cfg, Provider(), Calendar(), NOW.astimezone(timezone.utc))['600900.SH'] == 'FILLED'


def test_two_symbols_all_data_validated_before_order(setup):
    cfg, store, _ = setup
    cfg = Config(lookback=2, etf_symbol='999999.SH')
    class Bad(Provider):
        def history(self, symbol, *args):
            if symbol == cfg.etf_symbol:
                raise OSError('ETF down')
            return super().history(symbol, *args)
    with pytest.raises(OSError):
        run(store, cfg, Bad(), Calendar(), NOW)
    assert not store.snapshot()['orders']


def test_sell_and_t_plus_one(setup):
    cfg, store, _ = setup
    run(store, cfg, Provider(), Calendar(), NOW)
    # Direct broker checks to isolate settlement rule from daily deduplication.
    store.db.execute('DELETE FROM orders')
    q = {'600900.SH': Provider().quote('600900.SH', NOW)}
    assert execute(store, cfg, NOW, '600900.SH', 'SELL', q) == 'T_PLUS_ONE'
    tomorrow = NOW + timedelta(days=1)
    q = {'600900.SH': Provider().quote('600900.SH', tomorrow)}
    assert execute(store, cfg, tomorrow, '600900.SH', 'SELL', q) == 'FILLED'
    assert not store.snapshot()['positions']
    assert store.snapshot()['cash_fen'] == 9_998_948


def test_transaction_rollback(setup):
    cfg, store, _ = setup
    store.db.execute("CREATE TRIGGER fail_fill BEFORE INSERT ON fills BEGIN SELECT RAISE(ABORT, 'simulated crash'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run(store, cfg, Provider(), Calendar(), NOW)
    assert not store.snapshot()['orders']
    assert store.snapshot()['cash_fen'] == 10_000_000


@pytest.mark.parametrize('kwargs', [{'mode':'live'}, {'lookback':0}, {'order_lots':1.5}, {'commission_rate':-1}, {'max_position':float('nan')}, {'etf_symbol':'600900.SH'}, {'etf_symbol':'unknown'}, {'max_quote_age_seconds':999}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        Config(**kwargs)


def test_slow_data_rechecks_age(setup, monkeypatch):
    cfg, store, _ = setup
    ticks = iter([0, 200, 200, 200])
    monkeypatch.setattr('quant_paper.core.time.monotonic', lambda: next(ticks))
    with pytest.raises(ValueError, match='过期'):
        run(store, cfg, Provider(), Calendar(), NOW)
    assert not store.snapshot()['orders']


def test_parallel_runs_one_fill(setup):
    from concurrent.futures import ThreadPoolExecutor
    cfg, store, path = setup
    def worker():
        separate = Store(path, cfg)
        try:
            return run(separate, cfg, Provider(), Calendar(), NOW)['600900.SH']
        finally:
            separate.db.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))
    assert sorted(results) == ['DUPLICATE', 'FILLED']
    assert len(store.snapshot()['fills']) == 1


def test_short_calendar_window_blocks_orders(setup):
    cfg, store, _ = setup
    class Short(Calendar):
        def previous(self, day, count):
            return DAYS[:1]
    with pytest.raises(ValueError, match='窗口不足'):
        run(store, cfg, Provider(), Short(), NOW)
    assert not store.snapshot()['orders']

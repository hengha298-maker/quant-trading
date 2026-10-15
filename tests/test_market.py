from datetime import date, datetime
from types import SimpleNamespace
import pandas as pd
import pytest
from quant_paper.core import BJ
from quant_paper.market import Calendar, AkshareProvider


def test_actual_calendar_holiday_and_previous():
    cal = Calendar()
    assert not cal.is_session(date(2026, 10, 1))
    assert cal.is_session(date(2026, 10, 15))
    assert cal.previous(date(2026, 10, 15), 2) == [date(2026, 10, 13), date(2026, 10, 14)]
    with pytest.raises(Exception):
        cal.is_session(date(2100, 1, 1))


@pytest.mark.parametrize('etf', [None, '999999.SH'])
def test_adapter_contract(etf):
    provider = AkshareProvider.__new__(AkshareProvider)
    provider.etf_symbol = etf
    def history(**kwargs):
        assert kwargs['end_date'] == '20261014'
        assert kwargs['adjust'] == ''
        return pd.DataFrame({'日期':['2026-10-14'], '收盘':[10]})
    def minutes(**kwargs):
        assert kwargs['period'] == '1'
        return pd.DataFrame({'时间':['2026-10-15 14:49:00'], '收盘':[10], '成交量':[100]})
    provider.ak = SimpleNamespace(stock_zh_a_hist=history, fund_etf_hist_em=history,
                                  stock_zh_a_hist_min_em=minutes, fund_etf_hist_min_em=minutes)
    symbol = etf or '600900.SH'
    assert provider.history(symbol, date(2026,10,14), date(2026,10,14)) == {date(2026,10,14):10}
    quote = provider.quote(symbol, datetime(2026,10,15,14,50,tzinfo=BJ))
    assert quote.timestamp == datetime(2026,10,15,14,49,tzinfo=BJ)

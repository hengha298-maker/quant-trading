"""AKShare public data adapter. No broker dependencies, credentials or live orders."""
from datetime import datetime
import exchange_calendars as xcals
from .core import BJ, Quote


class Calendar:
    def __init__(self):
        self.cal = xcals.get_calendar("XSHG")

    def is_session(self, day):
        # Out of supported range raises; never substitute a weekday calendar.
        return self.cal.is_session(str(day))

    def previous(self, day, count):
        prev = self.cal.previous_session(str(day))
        return [x.date() for x in self.cal.sessions_window(prev, -count)]


class AkshareProvider:
    def __init__(self, etf_symbol=None):
        import akshare
        self.ak = akshare
        self.etf_symbol = etf_symbol

    def history(self, symbol, start, end):
        func = self.ak.fund_etf_hist_em if symbol == self.etf_symbol else self.ak.stock_zh_a_hist
        options = {} if symbol == self.etf_symbol else {"timeout": 15}
        frame = func(symbol=symbol[:6], period="daily", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), adjust="", **options)
        result = {}
        for _, row in frame.iterrows():
            day = datetime.fromisoformat(str(row["日期"])).date()
            if day in result:
                raise ValueError("重复日行情")
            result[day] = float(row["收盘"])
        return result

    def quote(self, symbol, now):
        func = self.ak.fund_etf_hist_min_em if symbol == self.etf_symbol else self.ak.stock_zh_a_hist_min_em
        frame = func(symbol=symbol[:6], period="1", start_date=now.strftime("%Y-%m-%d") + " 09:30:00", end_date=now.strftime("%Y-%m-%d %H:%M:%S"), adjust="")
        if frame.empty:
            raise ValueError("当日分钟行情为空")
        # Use source timestamp, never the request/receipt timestamp.
        row = frame.iloc[-1]
        timestamp = datetime.fromisoformat(str(row["时间"])).replace(tzinfo=BJ)
        return Quote(symbol, timestamp, float(row["收盘"]), float(row["成交量"]))

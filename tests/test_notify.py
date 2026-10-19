from datetime import timedelta
import json
from pathlib import Path
import sys

import pytest
import requests

from quant_paper import cli, notify
from quant_paper.core import Store
from test_core import NOW, Calendar, Provider


@pytest.fixture
def bark(tmp_path, monkeypatch):
    monkeypatch.delenv('BARK_IS_ARCHIVE', raising=False)
    monkeypatch.delenv('BARK_SERVER', raising=False)
    monkeypatch.delenv('BARK_DEVICE_KEY', raising=False)
    path = tmp_path / 'bark.local.json'
    path.write_text(json.dumps({'server': 'https://bark.example.test', 'device_key': 'test-key'}), encoding='utf-8-sig')
    return path


def test_config_precedence_and_secret_repr(bark, monkeypatch):
    cfg = notify.load_config(bark)
    assert cfg.server == 'https://bark.example.test'
    assert 'test-key' not in repr(cfg) and 'example.test' not in repr(cfg)
    monkeypatch.setenv('BARK_SERVER', 'https://other.example.test/')
    assert notify.load_config(bark).server == 'https://other.example.test'
    monkeypatch.setenv('BARK_DEVICE_KEY', 'environment-key')
    bark.write_text('invalid json', encoding='utf-8')
    assert notify.load_config(bark).device_key == 'environment-key'


@pytest.mark.parametrize('server,key', [
    ('http://bark.example.test', 'test-key'),
    ('https://bark.example.test/test-key', 'test-key'),
    ('https://user:password@bark.example.test', 'test-key'),
    ('https://bark.example.test?secret=value', 'test-key'),
    ('https://bark.example.test', ''), ('', 'test-key'),
    ('https://bark.example.test', 'key/invalid'),
])
def test_invalid_config_never_requests(bark, monkeypatch, server, key):
    bark.write_text(json.dumps({'server': server, 'device_key': key}), encoding='utf-8')
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: pytest.fail('must not send'))
    assert notify.deliver(bark, '标题', '正文') == '失败'


class Response:
    status_code = 200
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def json(self):
        return {'code': 200}


def test_post_payload_and_timeouts(bark, monkeypatch):
    def post(url, **kwargs):
        assert url == 'https://bark.example.test/push'
        assert 'test-key' not in url
        assert kwargs['json']['device_key'] == 'test-key'
        assert kwargs['json']['body'] == '中文正文'
        assert kwargs['json']['title'] == '中文标题'
        assert kwargs['json']['group'] == '模拟交易'
        assert kwargs['json']['isArchive'] == 1
        assert kwargs['allow_redirects'] is False
        assert kwargs['timeout'] == (3, 5)
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    assert notify.deliver(bark, '中文标题', '中文正文') == '成功'


@pytest.mark.parametrize('kind', ['timeout', 'connection', 'http', 'api', 'json', 'redirect'])
def test_delivery_failures_redacted(bark, monkeypatch, capsys, caplog, kind):
    def post(*args, **kwargs):
        if kind == 'timeout':
            raise requests.Timeout('test-key https://bark.example.test')
        if kind == 'connection':
            raise requests.ConnectionError('test-key')
        response = Response()
        response.status_code = {'http': 503, 'redirect': 302}.get(kind, 200)
        if kind == 'api':
            response.json = lambda: {'code': 400, 'message': 'test-key'}
        if kind == 'json':
            response.json = lambda: json.loads('test-key')
        return response
    monkeypatch.setattr(requests, 'post', post)
    assert notify.deliver(bark, '标题', '正文') == '失败'
    assert 'test-key' not in caplog.text + str(capsys.readouterr())


@pytest.fixture
def app(tmp_path, monkeypatch, bark):
    monkeypatch.chdir(tmp_path)
    Path('config.json').write_text(json.dumps({'lookback': 2}), encoding='utf-8')
    class Clock:
        @staticmethod
        def now(tz):
            return NOW
    monkeypatch.setattr(cli, 'datetime', Clock)
    monkeypatch.setattr(cli, 'Calendar', Calendar)
    monkeypatch.setattr(cli, 'AkshareProvider', lambda _: Provider())
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'run'])
    messages = []
    def post(url, **kwargs):
        messages.append(kwargs['json'])
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    return messages


def snapshot():
    from quant_paper.core import Config
    store = Store('data/paper.sqlite3', Config())
    try:
        return store.snapshot()
    finally:
        store.db.close()


def test_buy_report_and_duplicate(app):
    assert cli.main() == 0
    body = app[-1]['body']
    for value in ['2026-10-15', '正常', '模拟买入', '600900.SH', '100', '10.00元', '5.01元', '98994.99元']:
        assert value in body
    before = snapshot()
    assert cli.main() == 0
    assert snapshot() == before
    assert '今日已有委托' in app[-1]['body']
    assert '模拟买入' not in app[-1]['body']


def test_failed_push_preserves_fill_and_exit_code(app, monkeypatch, caplog):
    attempts = []
    def fail(*a, **kw):
        attempts.append(kw['json'])
        raise requests.Timeout('test-key https://bark.example.test')
    monkeypatch.setattr(requests, 'post', fail)
    assert cli.main() == 0
    before = snapshot()
    assert len(before['fills']) == 1
    assert cli.main() == 0
    assert snapshot() == before
    assert len(attempts) == 2  # One request per run; no notification/trading retries.
    assert attempts[0]['isArchive'] == 1
    assert '模拟买入' in attempts[0]['body']
    assert '今日已有委托' in attempts[1]['body']
    assert 'test-key' not in Path('logs/paper.log').read_text(encoding='utf-8') + caplog.text


def test_hold_and_risk_rejection_report(app, monkeypatch):
    class Hold(Provider):
        def history(self, *args):
            return {day: 10 for day in super().history(*args)}
    monkeypatch.setattr(cli, 'AkshareProvider', lambda _: Hold())
    assert cli.main() == 0
    assert '观望' in app[-1]['body']
    assert not snapshot()['orders']
    monkeypatch.setattr(cli, 'AkshareProvider', lambda _: Provider())
    Path('config.json').write_text(json.dumps({'lookback': 2, 'max_position': .001}), encoding='utf-8')
    assert cli.main() == 0
    assert '超过仓位限制' in app[-1]['body']
    assert '成交数量0' in app[-1]['body']
    assert not snapshot()['fills']


def test_sell_report(app, monkeypatch):
    assert cli.main() == 0
    class Clock:
        @staticmethod
        def now(tz):
            return NOW + timedelta(days=1)
    class Sell(Provider):
        def history(self, *args):
            return dict(zip(super().history(*args), [11, 10]))
    monkeypatch.setattr(cli, 'datetime', Clock)
    monkeypatch.setattr(cli, 'AkshareProvider', lambda _: Sell())
    assert cli.main() == 0
    assert '模拟卖出' in app[-1]['body']
    assert '5.51元' in app[-1]['body']
    assert '99989.48元' in app[-1]['body']


@pytest.mark.parametrize('day', [0, 3])
def test_skip_policy_and_status(app, monkeypatch, day):
    class Clock:
        @staticmethod
        def now(tz):
            return (NOW + timedelta(days=day)).replace(hour=20)
    monkeypatch.setattr(cli, 'datetime', Clock)
    assert cli.main() == 0
    assert app == []
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'run', '--notify', 'always'])
    assert cli.main() == 0
    assert '已跳过' in app[-1]['body']
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'status'])
    assert cli.main() == 0
    assert len(app) == 1


@pytest.mark.parametrize('kind', ['market', 'config', 'database', 'logger_setup'])
def test_errors_notify_without_orders(app, monkeypatch, kind):
    if kind == 'market':
        class Bad(Provider):
            def history(self, *args):
                raise OSError('行情不可用')
        monkeypatch.setattr(cli, 'AkshareProvider', lambda _: Bad())
    elif kind == 'config':
        Path('config.json').write_text('{bad', encoding='utf-8')
    elif kind == 'database':
        monkeypatch.setattr(cli, 'Store', lambda *a: (_ for _ in ()).throw(OSError('database unavailable')))
    else:
        # Fail logging directory creation before trading starts.
        Path('logs').write_text('blocked', encoding='utf-8')
    assert cli.main() == 1
    assert app[-1]['title'] == '模拟交易运行异常'
    assert '异常' in app[-1]['body']
    if Path('data/paper.sqlite3').exists():
        assert not snapshot()['orders']


def test_test_command_never_opens_account_or_market(app, monkeypatch, capsys):
    def forbidden(*a, **kw):
        pytest.fail('test command must not trade/read market')
    monkeypatch.setattr(cli, 'Store', forbidden)
    monkeypatch.setattr(cli, 'Calendar', forbidden)
    monkeypatch.setattr(cli, 'AkshareProvider', forbidden)
    Path('config.json').unlink()
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'notify-test'])
    assert cli.main() == 0
    assert len(app) == 1
    assert not Path('data').exists() and not Path('logs').exists()
    assert 'test-key' not in capsys.readouterr().out
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: (_ for _ in ()).throw(requests.Timeout('test-key')))
    assert cli.main() == 1


def test_missing_and_corrupt_config_do_not_block_trade(app, monkeypatch):
    Path('bark.local.json').write_text('{bad', encoding='utf-8')
    assert cli.main() == 0
    assert len(snapshot()['fills']) == 1
    Path('bark.local.json').unlink()
    assert cli.main() == 0
    assert app == []


def test_never_policy_suppresses_even_error(app, monkeypatch):
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'run', '--notify', 'never'])
    Path('config.json').write_text('{bad', encoding='utf-8')
    assert cli.main() == 1
    assert app == []


def test_partial_failure_reports_committed_fill(app, monkeypatch):
    from quant_paper import core
    Path('config.json').write_text(json.dumps({'lookback': 2, 'etf_symbol': '510880.SH'}), encoding='utf-8')
    original = core.execute
    def execute(store, cfg, now, symbol, side, quotes):
        if symbol == '510880.SH':
            raise OSError('second symbol failure')
        return original(store, cfg, now, symbol, side, quotes)
    monkeypatch.setattr(core, 'execute', execute)
    assert cli.main() == 1
    assert len(snapshot()['fills']) == 1
    assert '模拟买入 600900.SH' in app[-1]['body']
    assert '98994.99元' in app[-1]['body']
    assert cli.main() == 1
    assert len(snapshot()['fills']) == 1
    assert '模拟买入' not in app[-1]['body']


def test_report_format_failure_does_not_fail_trade(app, monkeypatch):
    def fail(*a, **kw):
        raise RuntimeError('notification formatting failure')
    monkeypatch.setattr(notify, 'report', fail)
    assert cli.main() == 0
    assert len(snapshot()['fills']) == 1
    assert cli.main() == 0
    assert len(snapshot()['fills']) == 1


def test_test_command_preserves_existing_account(app, monkeypatch):
    assert cli.main() == 0
    before = snapshot()
    monkeypatch.setattr(sys, 'argv', ['quant-paper', 'notify-test'])
    assert cli.main() == 0
    assert snapshot() == before


@pytest.mark.parametrize('enabled', [True, False])
def test_archive_file_switch(bark, monkeypatch, enabled):
    data = json.loads(bark.read_text(encoding='utf-8-sig'))
    data['is_archive'] = enabled
    bark.write_text(json.dumps(data), encoding='utf-8')
    def post(url, **kwargs):
        assert kwargs['json']['isArchive'] == int(enabled)
        return Response()
    monkeypatch.setattr(requests, 'post', post)
    assert notify.deliver(bark, '标题', '正文') == '成功'


@pytest.mark.parametrize('value', ['false', 0, 1, None, [], {}])
def test_invalid_archive_is_redacted(bark, monkeypatch, value):
    data = json.loads(bark.read_text(encoding='utf-8-sig'))
    data['is_archive'] = value
    bark.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: pytest.fail('must not send'))
    assert notify.deliver(bark, '标题', '正文') == '失败'


@pytest.mark.parametrize('value,enabled', [('0', False), ('1', True)])
def test_archive_environment_override(bark, monkeypatch, value, enabled):
    data = json.loads(bark.read_text(encoding='utf-8-sig'))
    data['is_archive'] = not enabled
    bark.write_text(json.dumps(data), encoding='utf-8')
    monkeypatch.setenv('BARK_IS_ARCHIVE', value)
    assert notify.load_config(bark).is_archive is enabled
    monkeypatch.setenv('BARK_SERVER', 'https://bark.example.test')
    monkeypatch.setenv('BARK_DEVICE_KEY', 'environment-key')
    bark.write_text('invalid json', encoding='utf-8')
    assert notify.load_config(bark).is_archive is enabled


def test_invalid_archive_environment(bark, monkeypatch):
    monkeypatch.setenv('BARK_IS_ARCHIVE', 'false')
    monkeypatch.setattr(requests, 'post', lambda *a, **kw: pytest.fail('must not send'))
    assert notify.deliver(bark, '标题', '正文') == '失败'

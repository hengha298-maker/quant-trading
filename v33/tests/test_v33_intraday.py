"""Entirely fictional observations; no network, user profile, V1 or Bark IO."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import http.client
import json
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, MagicMock, patch
from urllib.error import HTTPError, URLError

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'v32'))
sys.path.insert(0, str(REPO / 'v33'))
from goose_intraday import network, health, cli, reports
from goose_research.market import BJ

NOW = datetime(2026, 10, 27, 14, 40, 10, tzinfo=BJ)


def eastmoney(stamp='2026-10-27 14:40', price='20.00'):
    return json.dumps({'rc': 0, 'data': {'code': '600900', 'market': 1, 'name': '长江电力',
        'decimal': 2, 'prePrice': '20.00', 'trends': [f'{stamp},20.00,{price},20.10,19.90,10,20000,20.000']}}, ensure_ascii=False).encode()


def tencent(stamp='20261027144000', price='20.00'):
    fields = [''] * 88
    for i, value in {0:'1', 1:'长江电力', 2:'600900', 3:price, 4:'20.00', 5:'20.00', 6:'1000',
        30:stamp, 33:'20.10', 34:'19.90', 36:'1000'}.items(): fields[i] = value
    return ('v_sh600900="' + '~'.join(fields) + '";\n').encode('gbk')


def observation(source='eastmoney-minute', now=NOW):
    raw = eastmoney() if source == 'eastmoney-minute' else tencent()
    q = (network.parse_eastmoney if source == 'eastmoney-minute' else network.parse_tencent)(raw)
    return {'source': source, 'endpoint': network.SOURCES[source],
        'requestedAt': (now - timedelta(seconds=2)).isoformat(),
        'receivedAt': (now - timedelta(seconds=1)).isoformat(), 'elapsedMs': 20, 'httpStatus': 200,
        'headers': {}, 'quote': q, 'rawSha256': sha256(raw).hexdigest(), 'rawBytes': len(raw), 'errors': [],
        'transport': {'tlsVerification': True, 'proxyConfigurationModified': False, 'attempts': 1}}


def pair():
    return [observation(), observation('tencent-quote')]


class ParserTests(unittest.TestCase):
    def test_eastmoney_fields_prices_lots_and_minute_timestamp_remain_distinct(self):
        q = network.parse_eastmoney(eastmoney())
        self.assertEqual(q['price'], '20.00'); self.assertEqual(q['volume'], 1000)
        self.assertEqual(q['sourceTimestamp'], '2026-10-27T14:40:00+08:00')
        self.assertIsNone(q['confirmedLastTradeAt'])
        self.assertEqual(q['timestampKind'], 'minute-label-not-certified-last-trade')

    def test_tencent_gbk_identity_update_timestamp_and_cumulative_lots(self):
        q = network.parse_tencent(tencent('20261027161453'))
        self.assertEqual(q['name'], '长江电力'); self.assertEqual(q['volume'], 100000)
        self.assertEqual(q['sourceTimestamp'], '2026-10-27T16:14:53+08:00')
        self.assertIsNone(q['confirmedLastTradeAt'])
        self.assertFalse(q['independentFeedVerified'])

    def test_zero_first_minute_open_is_missing_not_a_trading_price(self):
        d = json.loads(eastmoney('2026-10-27 09:30'))
        d['data']['trends'][0] = d['data']['trends'][0].replace(',20.00,20.00,', ',0.00,20.00,')
        q = network.parse_eastmoney(json.dumps(d).encode())
        self.assertIn('ZERO_MINUTE_OPEN_IS_MISSING_NOT_ZERO_PRICE', q['warnings'])
        d['data']['trends'][0] = d['data']['trends'][0].replace('09:30', '14:40')
        with self.assertRaisesRegex(ValueError, 'LATEST_MINUTE_OPEN_MISSING'):network.parse_eastmoney(json.dumps(d).encode())

    def test_old_missing_open_is_flagged_and_never_used_for_current_quote(self):
        d=json.loads(eastmoney());old=json.loads(eastmoney('2026-10-26 14:40'))['data']['trends'][0]
        d['data']['trends'].insert(0,old.replace(',20.00,20.00,',',0.00,20.00,'))
        q=network.parse_eastmoney(json.dumps(d).encode())
        self.assertEqual(q['price'],'20.00');self.assertEqual(q['historicalMissingOpenRows'],1)
        self.assertIn('HISTORICAL_MINUTE_OPENS_MISSING_NOT_USED_IN_QUOTE_OR_BACKTEST',q['warnings'])

    def test_wrong_identity_decimal_code_and_return_status_fail_closed(self):
        for change in [lambda d:d.update(rc=1), lambda d:d.update(rc=False),
            lambda d:d['data'].update(code='600901'), lambda d:d['data'].update(market=0),
            lambda d:d['data'].update(decimal=3), lambda d:d['data'].update(name='FICTIONAL_OTHER')]:
            d = json.loads(eastmoney()); change(d)
            with self.assertRaises(ValueError):network.parse_eastmoney(json.dumps(d).encode())

    def test_empty_html_null_and_executable_javascript_are_never_evaluated(self):
        for raw in [b'', b'<html>login</html>', b'{"rc":0,"data":null}', b'alert("FICTIONAL")', b'{"rc":0,"data":{}}']:
            with self.assertRaises(ValueError):network.parse_eastmoney(raw)
        for raw in [b'alert("FICTIONAL")', b'v_sh600901="1~x";', b'v_sh600900="";']:
            with self.assertRaises(ValueError):network.parse_tencent(raw)

    def test_duplicate_json_keys_nan_unsafe_keys_and_nested_json_are_refused(self):
        for raw in [b'{"rc":0,"rc":1}', b'{"n":NaN}', b'{"__proto__":{}}', b'['*5000+b']'*5000]:
            with self.assertRaises(ValueError):network.strict_json(raw)

    def test_missing_duplicate_reversed_and_invalid_minute_rows_are_refused(self):
        for rows in [[], ['bad'], ['2026-02-30 14:40,20,20,20,20,1,20,20'],
            [json.loads(eastmoney())['data']['trends'][0]]*2,
            [json.loads(eastmoney())['data']['trends'][0], json.loads(eastmoney('2026-10-27 14:39'))['data']['trends'][0]]]:
            d = json.loads(eastmoney());d['data']['trends'] = rows
            with self.assertRaises(ValueError):network.parse_eastmoney(json.dumps(d).encode())

    def test_ohlc_nonpositive_price_wrong_tick_and_unit_scale_are_refused(self):
        for price in ['0.00', '-1', 'NaN', '20.001', '200.00']:
            with self.assertRaises(ValueError):network.parse_eastmoney(eastmoney(price=price))
            with self.assertRaises(ValueError):network.parse_tencent(tencent(price=price))

    def test_turnover_above_one_hundred_million_has_its_own_non_price_bound(self):
        d=json.loads(eastmoney());d['data']['trends'][0]=d['data']['trends'][0].replace(',20000,',',170915980.00,')
        self.assertEqual(network.parse_eastmoney(json.dumps(d).encode())['price'],'20.00')
        with self.assertRaises(ValueError):network.decimal_text('170915980.00')

    def test_tencent_field_count_timestamp_and_volume_disagreement_are_refused(self):
        for mutate in [lambda s:s.replace('20261027144000','20260230144000'),
            lambda s:s.replace('~600900~','~600901~'), lambda s:s.replace('~1000~','~1001~',1)]:
            with self.assertRaises(ValueError):network.parse_tencent(mutate(tencent().decode('gbk')).encode('gbk'))


class HealthTests(unittest.TestCase):
    def test_fresh_manual_window_is_only_a_candidate_never_trade_or_phone_acceptance(self):
        data = pair();before = deepcopy(data);r = health.build_health(data, now=NOW)
        self.assertEqual(r['status'], 'fresh-observation'); self.assertTrue(r['manualWindowChecksPassed'])
        self.assertFalse(r['manualAcceptanceVerified']); self.assertFalse(r['intradayReliabilityVerified'])
        self.assertFalse(r['decisionAllowed']); self.assertEqual(r['stance'], 'watch')
        self.assertFalse(r['notification']['sent']); self.assertFalse(r['comparison']['independentFeedVerified'])
        self.assertEqual(data, before)

    def test_after_close_never_passes_even_if_payload_timestamps_are_recent(self):
        now = NOW.replace(hour=20)
        data = [observation(s, now) for s in network.SOURCES]
        for item in data:item['quote']['sourceTimestamp'] = now.replace(second=0).isoformat()
        r = health.build_health(data, now=now)
        self.assertEqual(r['status'], 'intraday-not-verified'); self.assertFalse(r['manualWindowChecksPassed'])
        self.assertIn('SOURCE_TIME_OUTSIDE_CONTINUOUS_SESSION', r['codes'])

    def test_previous_day_and_stale_same_day_sources_cannot_become_today_signal(self):
        for stamp in ['2026-10-26T14:40:00+08:00', '2026-10-27T14:38:00+08:00']:
            data = pair()
            for item in data:item['quote']['sourceTimestamp'] = stamp
            r = health.build_health(data, now=NOW)
            self.assertEqual(r['status'], 'intraday-not-verified'); self.assertFalse(r['manualWindowChecksPassed'])
            self.assertIn('SOURCE_STALE_OVER_90_SECONDS', r['codes'])

    def test_future_source_receipt_and_reversed_client_clock_are_refused(self):
        for change in [lambda x:x['quote'].update(sourceTimestamp=(NOW+timedelta(seconds=1)).isoformat()),
            lambda x:x.update(receivedAt=(NOW+timedelta(seconds=1)).isoformat()),
            lambda x:x.update(requestedAt=NOW.isoformat())]:
            x = observation();change(x)
            r = health.build_health([x,observation('tencent-quote')], now=NOW)
            self.assertFalse(r['manualWindowChecksPassed']); self.assertIn('FUTURE_OR_REVERSED_CLOCK', r['codes'])

    def test_naive_times_and_clock_are_rejected(self):
        x = observation();x['quote']['sourceTimestamp'] = '2026-10-27T14:40:00'
        self.assertIn('AWARE_TIMESTAMP_REQUIRED', health.inspect_observation(x,NOW)['codes'])
        with self.assertRaisesRegex(ValueError,'AWARE_CLOCK'):health.build_health(pair(),now=NOW.replace(tzinfo=None))

    def test_weekend_lunch_auction_and_1445_boundary_do_not_pass_manual_window(self):
        clocks = [NOW.replace(day=31), NOW.replace(hour=12), NOW.replace(hour=14,minute=57),
            NOW.replace(minute=45,second=0), NOW.replace(hour=11)]
        for clock in clocks:
            r = health.build_health(pair(),now=clock)
            self.assertFalse(r['manualWindowChecksPassed']);self.assertFalse(r['decisionAllowed'])

    def test_2027_calendar_fails_closed_without_weekday_guess(self):
        r = health.build_health(pair(),now=NOW.replace(year=2027))
        self.assertEqual(r['calendarContext']['calendar'],'unverified-or-unavailable')
        self.assertIn('CALENDAR_UNVERIFIED_STOP',r['codes'])

    def test_missing_calendar_dependency_is_not_a_weekday_fallback(self):
        with patch('goose_intraday.health.xshg_sessions',side_effect=ValueError('MISSING')):
            r = health.build_health(pair(),now=NOW)
        self.assertIn('CALENDAR_UNVERIFIED_STOP',r['codes']);self.assertFalse(r['manualWindowChecksPassed'])

    def test_replay_and_synthetic_cannot_verify_live_even_with_fresh_fields(self):
        for mode in ['replay','synthetic']:
            r = health.build_health(pair(),now=NOW,mode=mode)
            self.assertFalse(r['manualWindowChecksPassed']);self.assertEqual(r['status'],'intraday-not-verified')
            self.assertIn('REPLAY_OR_SYNTHETIC_CANNOT_VERIFY_LIVE_MARKET',r['codes'])

    def test_duplicate_or_one_source_cannot_claim_independent_validation(self):
        with self.assertRaisesRegex(ValueError,'DUPLICATE_SOURCE'):health.build_health([observation(),observation()],now=NOW)
        r = health.build_health([observation()],now=NOW)
        self.assertFalse(r['manualWindowChecksPassed']);self.assertFalse(r['comparison']['evaluated'])

    def test_price_disagreement_and_time_skew_block_comparison(self):
        data = pair();data[1]['quote']['price'] = '20.10'
        r = health.build_health(data,now=NOW)
        self.assertFalse(r['manualWindowChecksPassed']);self.assertEqual(r['status'],'intraday-not-verified')
        self.assertIn('SOURCE_PRICE_DISAGREEMENT_REQUIRES_REVIEW',r['codes'])
        data=pair();data[0]['quote']['sourceTimestamp']='2026-10-27T14:39:00+08:00';data[1]['quote']['sourceTimestamp']='2026-10-27T14:40:08+08:00'
        r=health.build_health(data,now=NOW)
        self.assertIn('SOURCE_TIMESTAMP_SKEW_TOO_LARGE',r['codes'])

    def test_bad_units_provenance_zero_volume_and_unsupported_claims_are_blocked(self):
        changes=[lambda q:q.update(priceUnit='cents'),lambda q:q.update(currency='USD'),
            lambda q:q.update(adjustment='qfq'),lambda q:q.update(volume=0),
            lambda q:q.update(sourceUrl='https://secret.example.invalid/'),
            lambda q:q.update(independentFeedVerified=True),lambda q:q.update(confirmedLastTradeAt=NOW.isoformat())]
        for change in changes:
            data=pair();change(data[0]['quote']);r=health.build_health(data,now=NOW)
            self.assertFalse(r['manualWindowChecksPassed'])

    def test_cached_response_stale_receipt_clock_skew_and_slow_transport_are_blocked(self):
        for change in [lambda x:x['headers'].update(Age='91'),lambda x:x['headers'].update(Age='not-number'),
            lambda x:x['headers'].update(Date='bad'),lambda x:x['headers'].update(Date='Mon, 26 Oct 2026 00:00:00 GMT'),
            lambda x:x.update(receivedAt=(NOW-timedelta(seconds=31)).isoformat()),lambda x:x.update(elapsedMs=8001),
            lambda x:x['transport'].update(tlsVerification=False),lambda x:x['transport'].update(proxyConfigurationModified=True)]:
            data=pair();change(data[0]);r=health.build_health(data,now=NOW)
            self.assertFalse(r['manualWindowChecksPassed'])

    def test_raw_upstream_error_is_not_copied_into_health_report(self):
        x={'source':'eastmoney-minute','errors':['PRIVATE_TOKEN path=secret']}
        r=health.build_health([x],now=NOW)
        self.assertIn('UPSTREAM_ERROR_REDACTED',r['codes']);self.assertNotIn('PRIVATE_TOKEN',json.dumps(r))

    def test_malformed_unhashable_source_is_a_closed_failure_not_a_traceback(self):
        for source in [[],{},None,True]:
            r=health.build_health([{'source':source,'errors':[]}],now=NOW)
            self.assertIn('SOURCE_NOT_SUPPORTED',r['codes']);self.assertFalse(r['manualWindowChecksPassed'])

    def test_old_or_today_daily_context_is_not_substituted_for_previous_day(self):
        fixture=json.loads((REPO/'v32/fixtures/synthetic-600900-2025.json').read_text(encoding='utf-8'))
        self.assertFalse(health.history_context(fixture,'2026-10-27')['available'])
        fixture['quality']['status']='validated';fixture['bars']=fixture['bars'][:-1];fixture['calendar']['sessions']=fixture['calendar']['sessions'][:-1]
        r=health.history_context(fixture,'2025-12-31')
        self.assertTrue(r['available']);self.assertEqual(r['through'],'2025-12-30')
        self.assertEqual(r['rule'],'previous-complete-close-versus-inclusive-MA20-not-crossover')
        self.assertEqual(r['ma20'],str(sum(health.Decimal(b['close']) for b in fixture['bars'][-20:])/20))
        self.assertFalse(health.history_context(fixture,'2026-01-05')['available'])


class TransportTests(unittest.TestCase):
    def test_https_proxy_policy_and_no_redirects_are_kept(self):
        with patch.object(network,'build_opener') as builder:network.default_opener()
        handlers=builder.call_args.args
        ctx=next(x._context for x in handlers if isinstance(x,network.HTTPSHandler))
        self.assertTrue(ctx.check_hostname);self.assertEqual(ctx.verify_mode,ssl.CERT_REQUIRED)
        redirect=next(x for x in handlers if isinstance(x,network.NoRedirect))
        self.assertIsNone(redirect.redirect_request(None,None,302,'',{},'http://example.invalid'))

    def test_one_request_timeout_fingerprint_and_no_secret_headers(self):
        response=MagicMock();response.__enter__.return_value=response;response.status=200
        response.headers={};response.read.return_value=eastmoney()
        transport=Mock();transport.open.return_value=response
        response.geturl.side_effect=lambda:transport.open.call_args.args[0].full_url
        result,raw=network.fetch_observation('eastmoney-minute',opener=transport)
        transport.open.assert_called_once();self.assertEqual(transport.open.call_args.kwargs,{'timeout':8})
        self.assertEqual(result['rawSha256'],sha256(raw).hexdigest());self.assertFalse(result['errors'])
        self.assertIn('ndays=5',transport.open.call_args.args[0].full_url)

    def test_proxy_tls_dns_timeout_remote_close_errors_are_fixed_no_retry(self):
        failures=[(URLError(ssl.SSLCertVerificationError('SECRET')),'TLS_CERTIFICATE_VERIFICATION_FAILED'),
            (URLError(socket.gaierror('SECRET')),'DNS_FAILURE_PROXY_OR_ORIGIN'),
            (URLError(ConnectionRefusedError('SECRET')),'CONNECTION_REFUSED_PROXY_OR_ORIGIN'),
            (URLError(http.client.RemoteDisconnected('SECRET')),'REMOTE_DISCONNECTED_PROXY_OR_ORIGIN'),
            (URLError(socket.timeout('SECRET')),'NETWORK_TIMEOUT'),
            (HTTPError('https://SECRET_HOST',403,'SECRET',{},None),'HTTP_403')]
        for error,code in failures:
            transport=Mock();transport.open.side_effect=error
            r,raw=network.fetch_observation('tencent-quote',opener=transport)
            self.assertEqual(r['errors'],[code]);transport.open.assert_called_once();self.assertIsNone(raw)
            self.assertNotIn('SECRET',json.dumps(r))

    def test_oversized_body_and_changed_redirect_target_are_refused(self):
        for target,body,code in [('https://other.invalid/',b'','UPSTREAM_REDIRECT_OR_URL_CHANGED'),
            (network.SOURCES['tencent-quote'],b'x'*(network.MAX_BYTES+1),'UPSTREAM_BODY_TOO_LARGE')]:
            reply=MagicMock();reply.__enter__.return_value=reply;reply.status=200;reply.headers={};reply.geturl.return_value=target;reply.read.return_value=body
            transport=Mock();transport.open.return_value=reply
            r,_=network.fetch_observation('tencent-quote',opener=transport)
            self.assertIn(code,r['errors']);self.assertIsNone(r['quote'])


class IntegrationTests(unittest.TestCase):
    def test_live_failures_and_offline_replay_write_only_observation_reports(self):
        cli.WORK.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cli.WORK) as directory:
            root=Path(directory);fake={'format':'goose-intraday-replay','version':1,'fictional':True,'observations':pair()}
            input_path=root/'fictional.json';input_path.write_text(json.dumps(fake),encoding='utf-8')
            with patch.object(cli,'fetch_observation',side_effect=AssertionError('NO_NETWORK')):
                result=cli.run_check(replay=input_path,out=root/'run')
            self.assertEqual(result['status'],'intraday-not-verified');self.assertFalse(result['decisionAllowed'])
            folder=Path(result['output']);h=json.loads((folder/'health.json').read_text(encoding='utf-8'));r=json.loads((folder/'goose-report-v31.json').read_text(encoding='utf-8'))
            self.assertEqual(h['mode'],'synthetic');self.assertEqual(r['signals'][0]['stance'],'watch')
            self.assertIsNone(r['backtest']);self.assertEqual(r['paperTrades'],[])
            manifest=json.loads((folder/'run.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['notificationSent']);self.assertEqual(manifest['healthCanonicalSha256'],sha256(reports.canonical(h)).hexdigest())
            for name,digest in manifest['files'].items():self.assertEqual(sha256((folder/name).read_bytes()).hexdigest(),digest)

    def test_no_network_when_calendar_is_unverified(self):
        cli.WORK.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cli.WORK) as directory,patch('goose_intraday.health.context',return_value={'calendar':'unverified-or-unavailable','date':'2027-01-04','codes':['CALENDAR_UNVERIFIED_STOP'],'manualWindow':False,'continuousSession':False}),patch.object(cli,'fetch_observation',side_effect=AssertionError('NO_NETWORK')):
            result=cli.run_check(out=Path(directory)/'run')
            self.assertIn('CALENDAR_UNVERIFIED_NO_NETWORK',result['codes'])

    def test_output_guard_rejects_old_workspace_existing_folder_and_reparse(self):
        with self.assertRaisesRegex(ValueError,'OUTPUT_ONLY'):cli.output_directory(REPO.parent/'V3.2-dev/v33/work/DO_NOT_CREATE')
        cli.WORK.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cli.WORK) as directory:
            with self.assertRaises(FileExistsError):cli.output_directory(directory)
            original=Path.is_symlink
            def marked(path):return path==cli.WORK or original(path)
            with patch.object(Path,'is_symlink',marked):
                with self.assertRaisesRegex(ValueError,'REPARSE'):cli.output_directory(Path(directory)/'new')

    def test_old_quote_report_keeps_source_date_and_missing_quote_uses_diagnostic_date(self):
        items=pair()
        for item in items:item['quote']['sourceTimestamp']='2026-10-26T14:40:00+08:00'
        report=reports.phone_report(health.build_health(items,now=NOW))
        self.assertEqual(report['asOf'],'2026-10-26');self.assertEqual(report['signals'][0]['stance'],'watch')
        report=reports.phone_report(health.build_health([{'source':'eastmoney-minute','errors':['NETWORK_TIMEOUT']}],now=NOW))
        self.assertEqual(report['asOf'],'2026-10-27');self.assertIn('仅为诊断日期',report['notes'])
        self.assertRegex(report['generatedAt'],r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$')

    def test_run_crossing_1445_cannot_return_success_for_an_earlier_snapshot(self):
        snapshot=health.build_health(pair(),now=NOW)
        self.assertTrue(snapshot['manualWindowChecksPassed'])
        cli.WORK.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cli.WORK) as directory,patch.object(cli,'datetime') as clock,patch.object(cli,'build_health',return_value=snapshot),patch.object(cli,'fetch_observation') as fetch:
            clock.now.side_effect=[NOW,NOW.replace(minute=45,second=1)]
            fetch.side_effect=[(x,None) for x in pair()]
            result=cli.run_check(out=Path(directory)/'run')
            self.assertFalse(result['manualWindowChecksPassed'])
            manifest=json.loads((Path(result['output'])/'run.json').read_text(encoding='utf-8'))
            self.assertFalse(manifest['manualWindowCheckComplete'])

    def test_real_cli_replay_has_expected_exit_two_and_no_clock_override(self):
        cli.WORK.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cli.WORK) as directory:
            result=subprocess.run([sys.executable,'-I','-B',str(REPO/'v33/run.py'),'replay','--input',str(REPO/'v33/fixtures/fictional-observations.json'),'--out',str(Path(directory)/'run')],capture_output=True,text=True)
            self.assertEqual(result.returncode,2)
            self.assertFalse(json.loads(result.stdout)['notificationSent'])

    def test_cli_has_no_clock_override_notification_or_trading_switch(self):
        for argument in ['--now','--send-bark','--trade','--schedule']:
            with self.assertRaises(SystemExit),patch('sys.stderr'):
                cli.main(['check',argument])

    def test_json_parser_rejects_duplicate_unsafe_and_large_input_before_network(self):
        cli.WORK.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cli.WORK) as directory:
            p=Path(directory)/'bad.json'
            for raw in ['{"x":1,"x":2}','{"__proto__":{}}','{"n":NaN}','{"n":1e999}','['*5000+']'*5000]:
                p.write_text(raw,encoding='utf-8')
                with self.assertRaises(ValueError):cli.load_json(p)


if __name__ == '__main__':
    unittest.main()

"""Private report delivery and explicit import boundary tests; no live requests."""
from __future__ import annotations

import base64
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from investment.server import make_server, MAX_BODY


class PrivateServerTests(unittest.TestCase):
    def setUp(self):
        scratch = Path(__file__).resolve().parents[1] / 'work' / 'server-tests'
        scratch.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=scratch)
        self.root = Path(self.temp.name)
        self.web = self.root / 'v3' / 'web'
        self.web.mkdir(parents=True)
        (self.web / 'index.html').write_text('<h1>Existing PWA unchanged</h1>')
        self.release = threading.Event()
        self.started = threading.Event()

        def analyze(root):
            self.started.set()
            self.release.wait(3)
            return {'id': 'test-job'}

        self.server = make_server(self.root, port=0, analyze=analyze)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.origin = f'http://127.0.0.1:{self.port}'
        self.token = self.server.access_key
        self.csrf = self.request('GET', '/investment/api/session')[2]['csrf']

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None, authenticated=True):
        connection = HTTPConnection('127.0.0.1', self.port, timeout=3)
        merged = {'Host': f'127.0.0.1:{self.port}'}
        if authenticated:
            merged['Authorization'] = 'Bearer ' + self.token
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            merged.update({'Content-Type': 'application/json', 'Origin': self.origin, 'X-Goose-CSRF': self.csrf})
        merged.update(headers or {})
        connection.request(method, path, body, headers=merged)
        response = connection.getresponse()
        raw = response.read()
        status, result_headers = response.status, dict(response.getheaders())
        connection.close()
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        return status, result_headers, parsed

    def report(self, identifier='new', stamp='2026-10-28T13:00:00Z'):
        run = self.root / 'investment' / 'work' / 'runs' / identifier
        run.mkdir(parents=True)
        value = {'id': identifier, 'generatedAt': stamp, 'asOf': '2026-10-28', 'status': 'holdings-required', 'summary': '真实日期，持仓仍缺失', 'portfolio': {'positions': []}}
        (run / 'report.json').write_text(json.dumps(value), encoding='utf-8')
        (run / 'goose-report-v31.json').write_text(json.dumps({'format': 'goose-investment-report', 'id': identifier}), encoding='utf-8')
        return value

    def test_all_private_read_endpoints_require_bearer(self):
        self.report()
        for path in ['session', 'status', 'latest', 'holdings', 'history', 'report-json', 'report-v31','ledger','broker-access','automation']:
            with self.subTest(path=path):
                status, headers, value = self.request('GET', '/investment/api/' + path, authenticated=False)
                self.assertEqual(status, 401)
                self.assertEqual(value['error'], 'PRIVATE_ACCESS_REQUIRED')
                self.assertEqual(headers['Cache-Control'], 'no-store')
                self.assertNotIn('Access-Control-Allow-Origin', headers)

    def test_incremental_events_preview_confirm_duplicate_and_undo(self):
        data='成交编号,成交日期,操作,证券代码,成交数量,成交价格,成交金额,总费用,资金发生额\nFictional-T1,2026-10-27,买入,600900,50,10,500,,\n'
        payload={'fileName':'fictional.csv','content':base64.b64encode(data.encode()).decode(),'broker':'华宝证券','coverageStart':'2026-10-27','coverageEnd':'2026-10-28','completeCoverage':True}
        code,_,preview=self.request('POST','/investment/api/trade-preview',payload)
        self.assertEqual(200,code);self.assertEqual(1,len(preview['eventCheck']['newEvents']))
        code,_,saved=self.request('POST','/investment/api/confirm',{'previewId':preview['previewId'],'confirmed':True})
        self.assertEqual(200,code);self.assertEqual(1,saved['newCount'])
        code,_,again=self.request('POST','/investment/api/trade-preview',payload)
        self.assertEqual(0,len(again['eventCheck']['newEvents']));self.assertEqual(1,len(again['eventCheck']['duplicateIds']))
        code,_,voided=self.request('POST','/investment/api/trade-undo',{'batchId':saved['batchId'],'confirmed':True})
        self.assertEqual(200,code);self.assertEqual('voided-original-retained',voided['status'])
        self.assertEqual(0,self.request('GET','/investment/api/ledger')[2]['storedEventCount'])
        self.assertEqual(1,len(list((self.root/'investment/work/private/broker-events').glob('*.json'))))

    def test_pause_endpoint_requires_confirmation_and_preserves_task(self):
        self.assertEqual(400,self.request('POST','/investment/api/automation-control',{'paused':True})[0])
        self.assertEqual(200,self.request('POST','/investment/api/automation-control',{'paused':True,'confirmed':True})[0])
        from investment.operations import pause_state
        self.assertTrue(pause_state(self.root)['paused'])

    def test_dns_rebinding_and_cross_origin_requests_denied(self):
        for headers in [{'Host': 'malicious.example'}, {'Origin': 'https://malicious.example'}, {'Sec-Fetch-Site': 'cross-site'}]:
            self.assertEqual(self.request('GET', '/investment/api/latest', headers=headers)[0], 403)
        for headers in [{'Origin': ''}, {'X-Goose-CSRF': 'wrong'}, {'Origin': 'null'}]:
            self.assertEqual(self.request('POST', '/investment/api/analyze', {}, headers)[0], 403)
        self.assertFalse(self.started.is_set())

    def test_analyze_is_single_job_and_exposes_no_internal_exception(self):
        self.assertEqual(self.request('POST', '/investment/api/analyze', {})[0], 202)
        self.assertTrue(self.started.wait(1))
        self.assertEqual(self.request('POST', '/investment/api/analyze', {})[0], 409)
        self.assertTrue(self.request('GET', '/investment/api/status')[2]['running'])
        self.release.set()
        for _ in range(30):
            status = self.request('GET', '/investment/api/status')[2]
            if not status['running']:
                break
            time.sleep(.02)
        self.assertEqual(status['reportId'], 'test-job')

    def test_latest_history_and_compatibility_report_are_read_only(self):
        old = self.report('old', '2026-10-27T13:00:00Z')
        new = self.report()
        self.assertEqual(self.request('GET', '/investment/api/latest')[2]['report']['id'], new['id'])
        self.assertEqual([r['id'] for r in self.request('GET', '/investment/api/history')[2]['reports']], ['new', 'old'])
        past = self.request('GET', '/investment/api/history/old')[2]['report']
        self.assertEqual(past['id'], old['id'])
        self.assertEqual(past['status'], 'stale-report')
        self.assertEqual(json.loads((self.root / 'investment' / 'work' / 'runs' / 'old' / 'report.json').read_text()), old)
        self.assertEqual(self.request('GET', '/investment/api/report-v31')[2]['format'], 'goose-investment-report')
        self.assertEqual(self.request('GET', '/investment/api/history/missing')[0], 404)
        self.assertEqual(len(list((self.root / 'investment' / 'work' / 'runs').glob('*/report.json'))), 2)

    def test_no_report_is_explicit_null_never_demo_data(self):
        self.assertEqual(self.request('GET', '/investment/api/latest')[2], {'report': None})
        self.assertEqual(self.request('GET', '/investment/api/report-v31')[0], 404)

    def test_only_allowlisted_static_resources_no_private_paths(self):
        self.assertIn(b'Existing PWA unchanged', self.request('GET', '/')[2])
        self.assertIn('让决策有依据'.encode(), self.request('GET', '/investment/')[2])
        (self.web / 'private.json').write_text('{"secret":"not public"}')
        for path in ['/private.json', '/investment/work/runs/report.json', '/../server.py', '/investment/%2e%2e/server.py', '/investment/?file=server.py']:
            self.assertIn(self.request('GET', path)[0], {400, 404})

    def test_body_size_content_type_and_path_scope_limits(self):
        headers = {'Content-Type': 'application/json', 'Content-Length': str(MAX_BODY + 1), 'Origin': self.origin, 'X-Goose-CSRF': self.csrf}
        self.assertEqual(self.request('POST', '/investment/api/analyze', headers=headers)[0], 413)
        self.assertEqual(self.request('POST', '/investment/api/analyze', {}, {'Content-Type': 'text/plain'})[0], 415)
        self.assertEqual(self.request('POST', '/investment/api/analyze?execute=other', {})[0], 404)
        self.assertEqual(self.request('POST', '/investment/api/preview', {'fileName': 'image.png', 'asOf': '2026-10-28', 'content': 'AA=='})[2]['error'], 'STRUCTURED_CSV_OR_XLSX_REQUIRED')

    def test_upload_preview_then_explicit_confirm_and_duplicate_guard(self):
        raw = '证券代码,证券名称,持仓数量,成本价,市值,浮动盈亏,可用资金\n600900,虚构测试股票,100,10.00,1050,50,200\n'
        payload = {'fileName': 'private-account.csv', 'asOf': '2026-10-28', 'broker': '虚构测试券商', 'content': base64.b64encode(raw.encode()).decode()}
        status, _, preview = self.request('POST', '/investment/api/preview', payload)
        self.assertEqual(status, 200, preview)
        directory = self.root / 'investment' / 'work' / 'private' / 'holdings'
        self.assertFalse(directory.exists())
        self.assertEqual(self.request('POST', '/investment/api/confirm', {'previewId': preview['previewId']})[2]['error'], 'EXPLICIT_CONFIRMATION_REQUIRED')
        self.assertEqual(self.request('POST', '/investment/api/confirm', {'previewId': preview['previewId'], 'confirmed': True})[0], 200)
        saved = self.request('GET', '/investment/api/holdings')[2]['holdings']
        self.assertEqual(saved['positions'][0]['quantity'], 100)
        self.assertEqual(saved['availableCash'], '200')
        self.assertNotIn('private-account', json.dumps(saved))
        again = self.request('POST', '/investment/api/preview', payload)[2]
        result = self.request('POST', '/investment/api/confirm', {'previewId': again['previewId'], 'confirmed': True})
        self.assertEqual(result[0], 409)
        self.assertEqual(result[2]['error'], 'DUPLICATE_IMPORT')
        self.assertEqual(len(list(directory.glob('*.json'))), 1)
        self.assertEqual(list((directory.parent / 'uploads').iterdir()), [])

    def test_external_bind_and_weak_token_rejected(self):
        with self.assertRaisesRegex(ValueError, 'LOOPBACK_BIND_REQUIRED'):
            make_server(self.root, host='0.0.0.0')
        with self.assertRaisesRegex(ValueError, 'STRONG_ACCESS_KEY_REQUIRED'):
            make_server(self.root, access_key='weak')

    def test_manual_preview_explicit_confirmation_preserves_two_brokers(self):
        date = '2026-10-28'
        for broker, quantity in [('华宝证券', '100'), ('银河证券', '200')]:
            status, _, result = self.request('POST', '/investment/api/manual-preview', {
                'broker': broker, 'asOf': date,
                'rows': [{'symbol': '600900.SH', 'quantity': quantity, 'costPrice': ''}],
                'availableCash': '',
            })
            self.assertEqual(status, 200, result)
            self.assertIsNone(result['preview']['availableCash'])
            self.assertIsNone(result['preview']['positions'][0]['costPrice'])
            self.assertEqual(result['preview']['source']['kind'], 'manual')
            self.assertIn('手工', ' '.join(result['preview']['warnings']))
            self.assertEqual(self.request('POST', '/investment/api/confirm', {'previewId': result['previewId'], 'confirmed': True})[0], 200)
        accounts = self.request('GET', '/investment/api/holdings')[2]['accounts']
        self.assertEqual({a['source']['broker'] for a in accounts}, {'华宝证券', '银河证券'})
        self.assertEqual(sorted(a['positions'][0]['quantity'] for a in accounts), [100, 200])
        self.assertEqual(self.request('POST', '/investment/api/manual-preview', {'broker': '未知机构', 'asOf': date, 'rows': []})[0], 400)
        status, _, result = self.request('POST', '/investment/api/manual-preview', {'broker': '华宝证券', 'asOf': date, 'rows': [{'symbol': '600900.SH', 'quantity': ''}]})
        self.assertEqual(status, 400)
        self.assertIn('message', result)
        self.assertEqual(len(self.request('GET', '/investment/api/holdings')[2]['accounts']), 2)


    def test_new_presentation_asset_and_key_rotation_are_isolated(self):
        status, headers, body = self.request('GET', '/investment/presentation.js')
        self.assertEqual(status, 200)
        self.assertIn('javascript', headers['Content-Type'])
        self.server.access_key = 'test-rotated-private-key-' + 'r' * 48
        self.assertEqual(self.request('GET', '/investment/api/latest')[0], 401)
        self.token = self.server.access_key
        self.assertEqual(self.request('GET', '/investment/api/latest')[0], 200)


if __name__ == '__main__':
    unittest.main()

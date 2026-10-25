"""Fixture-only contract/security tests. Integration requires the pinned P1 CODE."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import p1_dependency as dependency
import readonly_fixture as fixture
import readonly_server as bridge

P1_CODE=os.environ.get('GOOSE_P1_CODE')


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.access=bridge.Access({'fictional':True},clock=lambda:self.now)
    def test_one_use_and_session_expiry_without_sliding(self):
        code=self.access.pair_code;token=self.access.pair(code)
        self.assertIsNotNone(token);self.assertIsNone(self.access.pair(code))
        self.assertTrue(self.access.authorized(token));self.now=900
        self.assertFalse(self.access.authorized(token))
    def test_code_expiry_and_five_wrong_attempts(self):
        code=self.access.pair_code;self.now=300;self.assertIsNone(self.access.pair(code))
        code=self.access.rotate()
        for _ in range(5):self.assertIsNone(self.access.pair('not-a-code'))
        self.assertIsNone(self.access.pair(code))
    def test_revoke_rotate_close_and_untrusted_strings(self):
        for value in (None,1,'\ud800','１２３４５６７８'):
            self.assertIsNone(self.access.pair(value))
        code=self.access.rotate();token=self.access.pair(code)
        self.access.revoke();self.assertFalse(self.access.authorized(token))
        code=self.access.rotate();new=self.access.pair(code)
        self.assertNotEqual(new,token);self.assertFalse(self.access.authorized(token))
        self.access.close();self.assertFalse(self.access.authorized(new));self.assertIsNone(self.access.view)
        with self.assertRaises(ValueError):self.access.rotate()
    @unittest.skipUnless(os.name=='nt','Windows native fixture control panel')
    def test_windows_control_panel_builds_and_closes_without_reading_data(self):
        import tkinter as tk
        import readonly_launcher
        with patch.object(tk.Tk,'mainloop',lambda app:app.destroy()),patch.object(readonly_launcher,'create_server',side_effect=AssertionError('no source selected')):
            self.assertEqual(readonly_launcher.main(),0)
    def test_real_flag_cannot_enable_plaintext_real_data(self):
        with patch.object(bridge,'REAL_DATA_ENABLED',True),patch.object(bridge,'load_p1',side_effect=AssertionError('must not read sources')):
            with self.assertRaises(ValueError):bridge.create_server('unused')
    def test_cli_does_not_accept_data_paths_lan_or_proxy_options(self):
        for args in (['--data-dir','PRIVATE_CANARY'],['--p1-code','unused','--lan'],['--p1-code','unused','--real'],['--p1-code','unused','--trust-proxy']):
            with self.subTest(args=args),redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):bridge.main(args)


@unittest.skipUnless(P1_CODE,'set GOOSE_P1_CODE to the pinned PR #3 source; dedicated CI requires it')
class DependencyTests(unittest.TestCase):
    def test_actual_p1_functions_and_versions_are_used(self):
        p=dependency.load_p1(P1_CODE)
        self.assertEqual(p.revision,dependency.P1_COMMIT)
        self.assertTrue(p.model.build_view_model.__code__.co_filename.endswith('report_model.py'))
        with patch.object(p.validation,'validate_snapshot',wraps=p.validation.validate_snapshot) as v,patch.object(p.usage,'market_usage',wraps=p.usage.market_usage) as u,patch.object(p.model,'build_view_model',wraps=p.model.build_view_model) as m:
            view=fixture.build_fixture_view(p)
        v.assert_called_once();self.assertEqual(u.call_count,2);m.assert_called_once()
        self.assertEqual(view['permissions'],dict(valuation=False,profit_loss=False,advice=False))
    def test_bad_fixture_cannot_bypass_original_ohlc_validator(self):
        p=dependency.load_p1(P1_CODE);original=fixture.fictional_inputs
        def malformed(*a):
            s,m=original(*a);m[0]['rows'][0]['low']='999';return s,m
        with patch.object(fixture,'fictional_inputs',side_effect=malformed),self.assertRaises(ValueError):fixture.build_fixture_view(p)
    def test_wrong_version_is_rejected_and_crlf_zip_uses_original_sources(self):
        root=Path(P1_CODE).resolve();root=root/'p1' if (root/'p1').is_dir() else root
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)/'p1'/'goose_research';folder.mkdir(parents=True)
            for name in dependency.FILES:
                data=(root/'goose_research'/name).read_bytes().replace(b'\r\n',b'\n').replace(b'\n',b'\r\n')
                (folder/name).write_bytes(data)
            p=dependency.load_p1(Path(temp));self.assertEqual(fixture.build_fixture_view(p)['mode'],'real_readonly')
            (folder/'validation.py').write_text('raise RuntimeError("PRIVATE_CANARY")',encoding='utf-8')
            with self.assertRaises(ValueError) as error:dependency.load_p1(Path(temp))
            self.assertNotIn('PRIVATE_CANARY',str(error.exception))
    def test_projection_strips_original_account_ids_audit_urls_and_volume_history(self):
        p=dependency.load_p1(P1_CODE);original=fixture.fictional_inputs
        def with_audit(*a):
            s,m=original(*a);m[0]['url']='https://example.invalid/PRIVATE_CANARY';return s,m
        with patch.object(fixture,'fictional_inputs',side_effect=with_audit):view=fixture.build_fixture_view(p)
        raw=json.dumps(view)
        for value in ('PRIVATE_CANARY','FICTION_HB','response_sha256','raw_count','volume','requested_url'):
            self.assertNotIn(value,raw)
        self.assertIsNone(view['accounts'][1]['positions'][0]['broker_cost'])
        self.assertEqual(view['accounts'][1]['positions'][0]['available'],0)
        self.assertIsNone(view['quotes'][1]['close_unit'])
        self.assertFalse(view['quotes'][1]['historical_cost_reference'])
    def test_missing_and_stale_are_not_fake_zero_or_new_prices(self):
        p=dependency.load_p1(P1_CODE)
        missing=fixture.build_fixture_view(p,'missing');stale=fixture.build_fixture_view(p,'stale')
        self.assertTrue(all(q['close'] is None for q in missing['quotes']))
        self.assertEqual(stale['state'],'stale');self.assertGreaterEqual(stale['quotes'][0]['lag_days'],30)
        self.assertIsNone(missing['total_assets']);self.assertIsNone(stale['total_assets'])


@unittest.skipUnless(P1_CODE,'dedicated Windows/Linux CI provides pinned P1 sources')
class HttpTests(unittest.TestCase):
    def setUp(self):
        self.server=bridge.create_server(P1_CODE,port=0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
    def request(self,path='/api/v1/view',method='GET',body=None,headers=None):
        c=HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        h={} if headers is None else dict(headers)
        if method=='POST':
            h.setdefault('Origin',self.server.origin);h.setdefault('Content-Type','application/json')
            body=json.dumps(body if body is not None else {}).encode() if not isinstance(body,(bytes,str)) else body
        try:
            c.request(method,path,body=body,headers=h);r=c.getresponse();return r.status,dict(r.getheaders()),r.read()
        finally:c.close()
    def login(self):
        status,h,raw=self.request('/session/pair','POST',{'code':self.server.access.pair_code})
        self.assertEqual(status,200);self.assertNotIn(self.server.access.session,raw.decode())
        self.assertIn('HttpOnly',h['Set-Cookie']);self.assertIn('SameSite=Strict',h['Set-Cookie'])
        return {'Cookie':h['Set-Cookie'].split(';')[0]}
    def test_unauthorized_get_head_and_unrelated_tokens_leak_nothing(self):
        for method,headers in [('GET',{}),('HEAD',{}),('GET',{'Cookie':'role=admin'}),('GET',{'Authorization':'Bearer fake'})]:
            status,h,raw=self.request(method=method,headers=headers)
            self.assertEqual(status,401);self.assertNotIn(b'accounts',raw);self.assertNotIn(b'25.50',raw)
            self.assertIn('no-store',h['Cache-Control'])
    def test_authenticated_view_is_minimal_and_api_is_readonly(self):
        auth=self.login();status,h,raw=self.request(headers=auth)
        self.assertEqual(status,200);view=json.loads(raw);self.assertTrue(view['test_data'])
        self.assertEqual(view['mode'],'real_readonly');self.assertIsNone(view['total_assets'])
        self.assertNotIn('Access-Control-Allow-Origin',h)
        for method in ('POST','PUT','PATCH','DELETE','OPTIONS','TRACE','CONNECT'):
            status,_,_=self.request(method=method,body={},headers=auth);self.assertEqual(status,405)
    def test_logout_desktop_rotation_and_expiry_revoke_access(self):
        auth=self.login();self.assertEqual(self.request('/session/logout','POST',{},auth)[0],200)
        self.assertEqual(self.request(headers=auth)[0],401)
        self.server.access.rotate();auth=self.login();self.server.access.rotate()
        self.assertEqual(self.request(headers=auth)[0],401)
        auth=self.login();self.server.access.expires=0;self.assertEqual(self.request(headers=auth)[0],401)
    def test_origin_host_csrf_and_duplicate_cookie_are_rejected(self):
        auth=self.login()
        self.assertEqual(self.request(headers={**auth,'Host':'attacker.invalid'})[0],404)
        self.assertEqual(self.request(headers={**auth,'Origin':'https://attacker.invalid'})[0],403)
        self.assertEqual(self.request(headers={**auth,'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.request('/session/logout','POST',{},dict(auth,Origin='null'))[0],403)
        self.assertEqual(self.request(headers={'Cookie':auth['Cookie']+'; '+auth['Cookie']})[0],401)
        self.assertEqual(self.request(headers=auth)[0],200)
    def test_static_entry_has_no_demo_and_paths_are_whitelisted(self):
        status,h,body=self.request('/readonly/');self.assertEqual(status,200)
        for forbidden in (b'demo-data.js',b'app.js',b'model.js"',b'manifest.webmanifest',b'sw.js'):
            # readonly-model.js is an independent strict model, not model.js.
            if forbidden!=b'model.js"':self.assertNotIn(forbidden,body)
        for path in ('/','/index.html','/demo-data.js','/model.js','/app.js','/sw.js','/readonly_server.py','/../README.md','/%2e%2e/README.md','/api/v1/view?token=fake'):
            self.assertEqual(self.request(path)[0],404,path)
        self.assertIn("worker-src 'none'",h['Content-Security-Policy'])
    def test_invalid_auth_payload_and_code_replay_do_not_grant_session(self):
        code=self.server.access.pair_code
        self.assertEqual(self.request('/session/pair','POST',b'{"code":"12345678","code":"12345678"}')[0],400)
        self.assertEqual(self.request('/session/pair','POST',{'code':code,'role':'admin'})[0],401)
        auth=self.login();self.assertEqual(self.request('/session/pair','POST',{'code':code})[0],401)
        self.assertEqual(self.request(headers=auth)[0],200)
    def test_source_version_errors_do_not_echo_private_input(self):
        with self.assertRaises(ValueError) as e:bridge.create_server('/not-found/PRIVATE_CANARY',port=0)
        self.assertNotIn('PRIVATE_CANARY',str(e.exception))
    def test_close_invalidates_state_and_stops_listener(self):
        auth=self.login();self.server.access.close()
        self.assertEqual(self.request(headers=auth)[0],401)
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)
        with self.assertRaises(OSError):self.request(headers=auth)
        self.assertIsNone(self.server.access.view)


if __name__=='__main__':unittest.main()

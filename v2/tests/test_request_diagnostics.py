"""Loopback HTTP integration of fixed phone observations; no real tailnet/data.

The HTTP connection represents only Serve's local hop after TLS termination.
This does not establish reachability from a physical iPhone.
"""
from contextlib import redirect_stdout, redirect_stderr
from http.client import HTTPConnection
import io
import json
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tailscale_transport as phone
import test_phone as phone_tests


@unittest.skipUnless(os.environ.get('GOOSE_P1_CODE'), 'dedicated CI provides pinned P1 sources')
class RequestDiagnosticsTests(unittest.TestCase):
    # Reuse real HTTP setup/request helpers without importing a TestCase class
    # into this module or inheriting/duplicating its unrelated test methods.
    request = phone_tests.PhoneHttpTests.request
    tearDown = phone_tests.PhoneHttpTests.tearDown

    def setUp(self):
        phone_tests.PhoneHttpTests.setUp(self)
        self.cli.verified_version = (1, 104, 2)
        self.cli.self_ips = ('100.101.102.105',)

    def raw_request(self, *, raw_headers=(), omitted_headers=()):
        # HTTPConnection's dict API cannot express a missing default or duplicate
        # header, so exercise those security boundaries with the raw header API.
        defaults = [('Host', phone_tests.AUTHORITY), ('X-Forwarded-Host', phone_tests.AUTHORITY),
                    ('X-Forwarded-Proto', 'https'), ('X-Forwarded-For', phone_tests.DEVICE.ips[0]),
                    ('Tailscale-User-Login', phone_tests.DEVICE.login)]
        omitted = {name.casefold() for name in omitted_headers}
        headers = [(name, value) for name, value in defaults if name.casefold() not in omitted]
        headers.extend(raw_headers)
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            conn.putrequest('GET', self.gate.prefix + '/readonly/', skip_host=True, skip_accept_encoding=True)
            for name, value in headers: conn.putheader(name, value)
            conn.endheaders()
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def observed(self, *, path='/readonly/', method='GET', headers=None,
                 raw_headers=None, omitted_headers=()):
        ticket = self.gate.trace.begin()
        response = (self.raw_request(raw_headers=raw_headers or (), omitted_headers=omitted_headers)
                    if raw_headers is not None or omitted_headers else self.request(path, method=method, headers=headers))
        if response[0] == 200 and method == 'GET' and path == '/readonly/':
            # Client receipt can precede the server's post-write trace callback
            # by one scheduling slice, especially on the native Windows runner.
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                with self.gate.trace.lock:
                    if 'phone_page_sent' in self.gate.trace.codes: break
                time.sleep(.005)
        codes = self.gate.trace.finish(ticket)
        self.assertTrue(set(codes) <= phone.ERROR_CODES)
        return response, set(codes)

    def test_1042_missing_expiry_serves_actual_page_and_records_complete_identity_path(self):
        del self.cli.who['Node']['KeyExpiry']
        (status, headers, body), codes = self.observed()
        self.assertEqual(status, 200); self.assertIn(b'<!doctype html>', body)
        self.assertIn('no-store', headers['Cache-Control'])
        self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived', 'phone_proxy_headers_ok',
                                 'phone_whois_started', 'phone_identity_verified', 'phone_page_sent'})

    def test_windows_head_probe_and_unapproved_devices_do_not_pollute_phone_observation(self):
        for method, source in [('HEAD', '100.101.102.105'), ('GET', '100.101.102.104')]:
            with self.subTest(method=method):
                (status, _headers, body), codes = self.observed(method=method, headers={'X-Forwarded-For': source})
                self.assertEqual(status, 403)
                if method == 'HEAD':
                    self.assertEqual(codes, {'phone_request_not_observed'})
                    self.assertEqual(body, b'')
                    self.assertEqual(self.gate.trace.history_report(), ())
                else:
                    self.assertEqual(codes, {'proxy_request_arrived', 'proxy_xff_not_selected',
                                             'phone_proxy_headers_rejected'})
                    self.assertNotIn('phone_candidate_arrived', codes)
                    self.assertNotIn('phone_identity_verified', codes)

    def test_selected_address_with_wrong_proxy_headers_is_observed_but_rejected(self):
        for headers in ({'X-Forwarded-Proto': 'http'}, {'X-Forwarded-Host': 'invalid.example'},
                        {'Tailscale-Funnel-Request': '?1'}, {'Forwarded': 'for=fixture'}):
            (status, _headers, _body), codes = self.observed(headers=headers)
            self.assertEqual(status, 403)
            self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived',
                                     'transport_denied', 'phone_proxy_headers_rejected'})

    def test_wrong_user_header_cannot_reach_whois_and_has_exact_proxy_rejection(self):
        with patch.object(self.cli, 'whois', side_effect=AssertionError('must reject before whois')):
            (status, _headers, _body), codes = self.observed(headers={'Tailscale-User-Login': 'other@example.invalid'})
        self.assertEqual(status, 403)
        self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived',
                                 'proxy_login_mismatch', 'phone_proxy_headers_rejected'})

    def test_whois_timeout_cli_and_json_failures_have_distinct_fixed_codes(self):
        for failure, expected in [('cli_whois_timeout', 'phone_whois_timeout'),
                                  ('cli_whois_exit', 'phone_whois_cli_failed'),
                                  ('cli_whois_json', 'phone_whois_json_failed'),
                                  ('cli_whois_encoding', 'phone_whois_json_failed'),
                                  ('identity_unavailable', 'phone_whois_json_failed')]:
            with patch.object(self.cli, 'whois', side_effect=phone.TailnetError(failure)):
                (status, _headers, _body), codes = self.observed()
            self.assertEqual(status, 403)
            self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived', 'phone_proxy_headers_ok',
                                     'phone_whois_started', failure, expected})

    def test_actual_whois_identity_mismatch_is_rejected_after_lookup(self):
        self.cli.who['Node']['StableID'] = 'nOtherFixturePhone'
        (status, _headers, _body), codes = self.observed()
        self.assertEqual(status, 403)
        self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived', 'phone_proxy_headers_ok',
                                 'phone_whois_started', 'whois_stable_id_mismatch', 'phone_identity_rejected'})

    def test_route_and_origin_rejections_are_distinct_from_identity_failures(self):
        for path, headers, expected_status, expected_code in [
                ('/not-a-published-route', None, 404, 'phone_host_route_rejected'),
                ('/readonly/', {'Host': 'invalid.example'}, 404, 'phone_host_route_rejected'),
                ('/readonly/', {'Origin': 'https://invalid.example'}, 403, 'phone_origin_rejected')]:
            (status, _headers, _body), codes = self.observed(path=path, headers=headers)
            self.assertEqual(status, expected_status)
            self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived', 'phone_proxy_headers_ok',
                                     'phone_whois_started', 'phone_identity_verified', expected_code})

    def test_unauthenticated_api_and_phone_head_never_claim_page_was_sent(self):
        for path, method, expected_status in [('/api/v1/view', 'GET', 401), ('/readonly/', 'HEAD', 200)]:
            (status, _headers, _body), codes = self.observed(path=path, method=method)
            self.assertEqual(status, expected_status)
            self.assertIn('phone_identity_verified', codes)
            self.assertNotIn('phone_page_sent', codes)

    def test_direct_backend_path_cannot_forge_a_phone_observation(self):
        ticket = self.gate.trace.begin()
        status, _headers, _body = self.request('/readonly/', prefix=False)
        self.assertEqual(status, 403)
        self.assertEqual(self.gate.trace.finish(ticket), ('phone_request_not_observed',))

    def test_observations_and_console_contain_only_fixed_codes_without_secrets(self):
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            (_status, _headers, _body), codes = self.observed(headers={
                'Cookie': 'PRIVATE_COOKIE_CANARY=PRIVATE_TOKEN_CANARY',
                'Authorization': 'Bearer PRIVATE_AUTH_CANARY'})
        self.assertEqual(output.getvalue(), '')
        encoded = json.dumps(sorted(codes))
        encoded += json.dumps(self.gate.trace.history_report())
        for secret in ('PRIVATE', phone_tests.AUTHORITY, phone_tests.DEVICE.node_id,
                       phone_tests.DEVICE.login, self.gate.prefix, self.server.access.pair_code):
            self.assertNotIn(secret, encoded)

    def test_windowless_403_history_survives_new_check_without_claiming_fresh_phone_request(self):
        self.cli.who['Node']['StableID'] = 'nOtherFixturePhone'
        status, _headers, body = self.request('/readonly/')
        self.assertEqual(status, 403); self.assertNotIn(b'accounts', body)
        expected = {'proxy_request_arrived', 'phone_candidate_arrived', 'phone_proxy_headers_ok',
                    'phone_whois_started', 'whois_stable_id_mismatch', 'phone_identity_rejected'}
        history = {'history_' + code for code in expected}
        self.assertEqual(set(self.gate.trace.history_report()), history)
        self.assertTrue(history <= phone.ERROR_CODES)
        ticket = self.gate.trace.begin()
        self.assertEqual(self.gate.trace.finish(ticket), ('phone_request_not_observed',))
        self.assertEqual(set(self.gate.trace.history_report()), history)
        self.gate.trace.cancel()
        self.assertEqual(self.gate.trace.history_report(), ())

    def test_expanded_legal_ipv6_is_normalized_for_candidate_and_identity(self):
        (status, _headers, body), codes = self.observed(headers={
            'X-Forwarded-For': 'fd7a:115c:a1e0:0:0:0:0:103'})
        self.assertEqual(status, 200); self.assertIn(b'<!doctype html>', body)
        self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived', 'phone_proxy_headers_ok',
                                 'phone_whois_started', 'phone_identity_verified', 'phone_page_sent'})

    def test_missing_duplicate_invalid_xff_are_recorded_without_phone_attribution_or_whois(self):
        cases = [((), ('X-Forwarded-For',), 'proxy_xff_missing'),
                 ((('X-Forwarded-For', phone_tests.DEVICE.ips[0]),), (), 'proxy_xff_duplicate'),
                 ((('X-Forwarded-For', 'PRIVATE_INVALID_ADDRESS_CANARY'),), ('X-Forwarded-For',), 'proxy_xff_invalid')]
        for raw_headers, omitted, expected in cases:
            with self.subTest(expected=expected), patch.object(self.cli, 'whois', side_effect=AssertionError('must reject before whois')):
                (status, _headers, body), codes = self.observed(raw_headers=raw_headers, omitted_headers=omitted)
            self.assertEqual(status, 403); self.assertNotIn(b'accounts', body)
            self.assertEqual(codes, {'proxy_request_arrived', expected, 'phone_proxy_headers_rejected'})
            self.assertNotIn('phone_candidate_arrived', codes)
            self.assertNotIn('phone_identity_verified', codes)

    def test_login_missing_duplicate_and_mismatch_keep_distinct_codes_and_no_whois(self):
        cases = [((), ('Tailscale-User-Login',), 'proxy_login_missing'),
                 ((('Tailscale-User-Login', phone_tests.DEVICE.login),), (), 'proxy_login_duplicate'),
                 ((('Tailscale-User-Login', 'other@example.invalid'),), ('Tailscale-User-Login',), 'proxy_login_mismatch')]
        for raw_headers, omitted, expected in cases:
            with self.subTest(expected=expected), patch.object(self.cli, 'whois', side_effect=AssertionError('must reject before whois')):
                (status, _headers, body), codes = self.observed(raw_headers=raw_headers, omitted_headers=omitted)
            self.assertEqual(status, 403); self.assertNotIn(b'accounts', body)
            self.assertEqual(codes, {'proxy_request_arrived', 'phone_candidate_arrived',
                                     expected, 'phone_proxy_headers_rejected'})
            self.assertNotIn('phone_whois_started', codes)
            self.assertNotIn('phone_identity_verified', codes)


if __name__ == '__main__': unittest.main()

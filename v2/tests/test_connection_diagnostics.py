"""Connection checks use mocked DNS, TCP, TLS and HTTP; no tailnet or data."""
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
from pathlib import Path
import socket
import ssl
import sys
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import connection_diagnostics as diagnostics

AUTHORITY = 'fixture-pc.fixture-tail.ts.net:8443'
HOST = AUTHORITY.rsplit(':', 1)[0]
SELF_IP = '100.101.102.1'
SELF_V6 = 'fd7a:115c:a1e0::1'


def dns_rows(*addresses):
    return [(socket.AF_INET6 if ':' in address else socket.AF_INET,
             socket.SOCK_STREAM, socket.IPPROTO_TCP, '', (address, 8443))
            for address in addresses]


class RequestTraceTests(unittest.TestCase):
    def setUp(self):
        self.now = [0]
        self.trace = diagnostics.RequestTrace(clock=lambda: self.now[0])

    def test_unstarted_finished_and_expired_windows_ignore_new_requests(self):
        self.assertIsNone(self.trace.candidate())
        ticket = self.trace.begin()
        self.now[0] = 40
        self.assertIsNone(self.trace.candidate())
        self.trace.record(ticket, 'phone_page_sent')
        self.assertEqual(self.trace.finish(ticket), ('phone_request_not_observed',))
        self.assertIsNone(self.trace.candidate())

    def test_late_previous_epoch_cannot_pollute_or_finish_current_check(self):
        previous = self.trace.begin()
        self.assertEqual(self.trace.candidate(), previous)
        current = self.trace.begin()
        self.trace.record(previous, 'phone_identity_rejected')
        self.assertEqual(self.trace.finish(previous), ('check_cancelled',))
        self.assertEqual(self.trace.candidate(), current)
        self.trace.record(current, 'phone_identity_verified')
        self.assertEqual(self.trace.finish(current),
                         ('phone_candidate_arrived', 'phone_identity_verified'))
        self.trace.record(current, 'phone_reply_failed')
        self.assertNotIn('phone_reply_failed', self.trace.codes)

    def test_parallel_requests_preserve_success_and_failure_stages(self):
        ticket = self.trace.begin()
        codes = ('phone_proxy_headers_ok', 'phone_identity_verified',
                 'phone_page_sent', 'phone_whois_timeout', 'phone_reply_failed')
        observed_tickets = []
        def record(code):
            observed_tickets.append(self.trace.candidate())
            for _ in range(20): self.trace.record(ticket, code)
        workers = [threading.Thread(target=record, args=(code,)) for code in codes]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(observed_tickets, [ticket] * len(codes))
        self.assertEqual(set(self.trace.finish(ticket)), {*codes, 'phone_candidate_arrived'})

    def test_cancel_discards_codes_and_prevents_late_requests(self):
        ticket = self.trace.begin(); self.trace.candidate()
        self.trace.record(ticket, 'phone_identity_verified')
        self.trace.cancel()
        self.trace.record(ticket, 'phone_page_sent')
        self.assertIsNone(self.trace.candidate())
        self.assertEqual(self.trace.finish(ticket), ('check_cancelled',))
        next_ticket = self.trace.begin()
        self.assertEqual(self.trace.finish(next_ticket), ('phone_request_not_observed',))

    def test_unknown_strings_and_missing_tickets_do_not_enter_diagnostics(self):
        ticket = self.trace.begin()
        secret = 'PRIVATE_CANARY https://private.invalid/path?code=01234567'
        self.trace.record(ticket, secret)
        self.trace.record(None, 'phone_page_sent')
        self.assertEqual(self.trace.finish(ticket), ('phone_request_not_observed',))
        self.assertNotIn('PRIVATE', repr(self.trace.codes))


class LocalHttpsTests(unittest.TestCase):
    def mocks(self, stack, *, status=403, marker='false'):
        resolve = stack.enter_context(patch.object(diagnostics, '_resolve', return_value=dns_rows(SELF_IP)))
        raw = Mock(name='fictional_raw_socket')
        dial = stack.enter_context(patch.object(diagnostics.socket, 'create_connection', return_value=raw))
        context = Mock(spec=ssl.SSLContext)
        context.check_hostname = True; context.verify_mode = ssl.CERT_REQUIRED
        tls = Mock(name='fictional_tls_socket'); context.wrap_socket.return_value = tls
        default_context = stack.enter_context(patch.object(diagnostics.ssl, 'create_default_context', return_value=context))
        response = Mock(name='fictional_head_response', status=status)
        response.getheader.return_value = marker
        response.read.side_effect = AssertionError('HEAD check must never read a body')
        connection = Mock(name='fictional_https_connection'); connection.getresponse.return_value = response
        constructor = stack.enter_context(patch.object(diagnostics, 'HTTPSConnection', return_value=connection))
        return resolve, raw, dial, context, default_context, response, connection, constructor, tls

    def test_no_self_address_and_dns_failures_never_open_a_socket(self):
        with patch.object(diagnostics, '_resolve') as resolve, patch.object(diagnostics.socket, 'create_connection') as dial:
            self.assertEqual(diagnostics.check_local_https(AUTHORITY, ()), ('check_self_address_missing',))
            resolve.assert_not_called(); dial.assert_not_called()
        for value in (None, [], [('malformed',)], dns_rows('not-an-ip')):
            with self.subTest(value=value), patch.object(diagnostics, '_resolve', return_value=value), patch.object(diagnostics.socket, 'create_connection') as dial:
                self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP,)), ('check_local_dns_failed',))
                dial.assert_not_called()

    def test_dns_non_self_or_mixed_answers_are_rejected_before_tcp_or_tls(self):
        for addresses in (('100.101.102.99',), ('198.51.100.9',), (SELF_IP, '198.51.100.9')):
            with self.subTest(addresses=addresses), patch.object(diagnostics, '_resolve', return_value=dns_rows(*addresses)), \
                 patch.object(diagnostics.socket, 'create_connection') as dial, patch.object(diagnostics.ssl, 'create_default_context') as tls:
                self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP, SELF_V6)), ('check_local_dns_mismatch',))
                dial.assert_not_called(); tls.assert_not_called()

    def test_head_403_preserves_hostname_sni_and_default_certificate_validation(self):
        for marker in ('true', 'false'):
            with self.subTest(marker=marker), ExitStack() as stack:
                resolve, raw, dial, context, default_context, response, connection, constructor, tls = self.mocks(stack, marker=marker)
                self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP,)), ('check_local_https_ok',))
                resolve.assert_called_once_with(HOST, 8443)
                dial.assert_called_once_with((SELF_IP, 8443), timeout=4)
                default_context.assert_called_once_with()
                self.assertTrue(context.check_hostname); self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
                context.wrap_socket.assert_called_once_with(raw, server_hostname=HOST)
                constructor.assert_called_once_with(HOST, 8443, timeout=4, context=context)
                self.assertIs(connection.sock, tls)
                connection.request.assert_called_once_with('HEAD', '/readonly/', headers={'Host': AUTHORITY})
                self.assertNotIn('Cookie', connection.request.call_args.kwargs['headers'])
                self.assertNotIn('body', connection.request.call_args.kwargs)
                self.assertNotIn('/api/', connection.request.call_args.args[1])
                self.assertNotIn('/session/', connection.request.call_args.args[1])
                response.read.assert_not_called(); connection.close.assert_called_once()
                raw.close.assert_not_called()

    def test_only_approved_ipv6_is_dialed_with_hostname_kept_for_tls(self):
        with ExitStack() as stack:
            resolve, raw, dial, context, *_ = self.mocks(stack)
            resolve.return_value = dns_rows('fd7a:115c:a1e0:0:0:0:0:1')
            self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_V6,)), ('check_local_https_ok',))
            dial.assert_called_once_with((SELF_V6, 8443), timeout=4)
            context.wrap_socket.assert_called_once_with(raw, server_hostname=HOST)

    def test_tcp_failure_is_distinct_from_dns_and_tls(self):
        with ExitStack() as stack:
            _, raw, dial, _, default_context, _, connection, *_ = self.mocks(stack)
            dial.side_effect = TimeoutError('PRIVATE_TCP_CANARY')
            self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP,)), ('check_local_tcp_failed',))
            default_context.assert_not_called(); connection.request.assert_not_called()

    def test_certificate_or_tls_failure_closes_raw_socket_without_http(self):
        for failure in (ssl.SSLCertVerificationError('PRIVATE_CERT_CANARY'), ssl.SSLError('PRIVATE_TLS_CANARY'), OSError('PRIVATE_TLS_CANARY')):
            with self.subTest(kind=type(failure).__name__), ExitStack() as stack:
                _, raw, _, context, _, _, connection, *_ = self.mocks(stack)
                context.wrap_socket.side_effect = failure
                self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP,)), ('check_local_tls_failed',))
                raw.close.assert_called_once(); connection.close.assert_called_once()
                connection.request.assert_not_called()

    def test_tls_setup_failure_closes_raw_socket_before_connection_exists(self):
        with ExitStack() as stack:
            _, raw, _, _, default_context, _, connection, constructor, _ = self.mocks(stack)
            default_context.side_effect = OSError('PRIVATE_CA_CANARY')
            self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP,)), ('check_local_tls_failed',))
            raw.close.assert_called_once(); constructor.assert_not_called()
            connection.request.assert_not_called()

    def test_non_application_response_does_not_claim_backend_or_phone_success(self):
        for status, marker in ((403, None), (403, 'unknown'), (200, 'false'), (502, None)):
            with self.subTest(status=status, marker=marker), ExitStack() as stack:
                _, _, _, _, _, response, connection, *_ = self.mocks(stack, status=status, marker=marker)
                self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP,)), ('check_local_tls_ok', 'check_local_http_failed'))
                response.read.assert_not_called(); connection.close.assert_called_once()

    def test_http_failure_after_verified_tls_retains_tls_evidence_and_closes(self):
        for failure in (OSError('PRIVATE_HTTP_CANARY'), diagnostics.HTTPException('PRIVATE_HTTP_CANARY')):
            with self.subTest(kind=type(failure).__name__), ExitStack() as stack:
                _, _, _, _, _, _, connection, *_ = self.mocks(stack)
                connection.getresponse.side_effect = failure
                self.assertEqual(diagnostics.check_local_https(AUTHORITY, (SELF_IP,)), ('check_local_tls_ok', 'check_local_http_failed'))
                connection.close.assert_called_once()

    def test_self_probe_produces_only_fixed_codes_and_no_sensitive_output(self):
        output = io.StringIO(); errors = io.StringIO()
        with ExitStack() as stack, redirect_stdout(output), redirect_stderr(errors):
            _, _, _, _, _, _, connection, *_ = self.mocks(stack)
            connection.getresponse.side_effect = OSError('PRIVATE_PATH_COOKIE_URL_CANARY')
            result = diagnostics.check_local_https(AUTHORITY, (SELF_IP,))
        self.assertTrue(set(result) <= diagnostics.CONNECTION_CODES)
        self.assertEqual(output.getvalue(), ''); self.assertEqual(errors.getvalue(), '')
        for secret in ('PRIVATE', AUTHORITY, SELF_IP, '/readonly/', 'Cookie'):
            self.assertNotIn(secret, repr(result))


if __name__ == '__main__': unittest.main()

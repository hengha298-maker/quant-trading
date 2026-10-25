"""Share-scoped fixed-code history is separate from request-window evidence."""
from pathlib import Path
import sys
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from connection_diagnostics import CONNECTION_CODES, RequestTrace


class IdentityTraceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.now = [0]
        self.trace = RequestTrace(clock=lambda: self.now[0])

    def test_remembers_trusted_stages_before_first_window(self):
        self.trace.remember('phone_proxy_headers_rejected')
        self.assertEqual(self.trace.history_report(), ('history_phone_proxy_headers_rejected',))
        ticket = self.trace.begin()
        self.assertEqual(self.trace.finish(ticket), ('phone_request_not_observed',))
        self.assertEqual(self.trace.history_report(), ('history_phone_proxy_headers_rejected',))

    def test_begin_finish_and_timeout_do_not_discard_share_history(self):
        previous = self.trace.begin()
        self.trace.remember('phone_identity_rejected')
        self.trace.record(previous, 'phone_identity_rejected')
        self.now[0] = 41
        self.assertIsNone(self.trace.candidate())
        self.assertEqual(self.trace.finish(previous), ('phone_identity_rejected',))
        self.trace.remember('phone_reply_failed')
        current = self.trace.begin()
        self.assertEqual(self.trace.finish(current), ('phone_request_not_observed',))
        self.assertEqual(self.trace.history_report(),
                         ('history_phone_identity_rejected', 'history_phone_reply_failed'))

    def test_history_prefix_copy_is_deterministic_unique_and_distinct_from_window(self):
        ticket = self.trace.begin()
        for code in ('phone_page_sent', 'phone_identity_verified', 'phone_page_sent'):
            self.trace.remember(code)
        self.trace.record(ticket, 'phone_identity_verified')
        self.assertEqual(self.trace.finish(ticket), ('phone_identity_verified',))
        report = self.trace.history_report()
        self.assertEqual('\n'.join(report), 'history_phone_identity_verified\nhistory_phone_page_sent')
        self.assertEqual(len(report), len(set(report)))
        self.assertTrue(all(code.startswith('history_') for code in report))

    def test_unknown_sensitive_values_and_recursive_history_codes_are_rejected(self):
        allowed = CONNECTION_CODES | {'history_phone_page_sent'}
        trace = RequestTrace(allowed_codes=allowed)
        for value in ('PRIVATE_IDENTITY_CANARY', 'https://private.invalid/path?cookie=CANARY',
                      'phone_page_sent\nPRIVATE_PATH', None, 12, [], {}, 'history_phone_page_sent'):
            trace.remember(value)
        self.assertEqual(trace.history_report(), ())
        trace.remember('phone_page_sent')
        self.assertEqual(trace.history_report(), ('history_phone_page_sent',))

    def test_custom_fixed_identity_codes_are_accepted_without_mutating_whitelist(self):
        identity_code = 'whois_fixed_identity_mismatch'
        allowed = CONNECTION_CODES | {identity_code}
        trace = RequestTrace(allowed_codes=allowed)
        ticket = trace.begin()
        trace.record(ticket, identity_code); trace.remember(identity_code)
        self.assertEqual(trace.finish(ticket), (identity_code,))
        self.assertEqual(trace.history_report(), ('history_' + identity_code,))
        self.assertEqual(allowed, CONNECTION_CODES | {identity_code})
        self.assertNotIn(identity_code, CONNECTION_CODES)

    def test_cancel_clears_history_and_blocks_late_remember_until_new_begin(self):
        ticket = self.trace.begin()
        self.trace.remember('phone_identity_verified')
        self.trace.cancel()
        self.trace.remember('phone_reply_failed')
        self.trace.record(ticket, 'phone_page_sent')
        self.assertEqual(self.trace.history_report(), ())
        self.assertEqual(self.trace.finish(ticket), ('check_cancelled',))
        current = self.trace.begin()
        self.trace.remember('phone_page_sent')
        self.assertEqual(self.trace.history_report(), ('history_phone_page_sent',))
        self.assertEqual(self.trace.finish(current), ('phone_request_not_observed',))

    def test_parallel_stages_are_preserved_and_stop_rejects_delayed_workers(self):
        codes = ('phone_proxy_headers_ok', 'phone_identity_verified', 'phone_identity_rejected',
                 'phone_page_sent', 'phone_whois_timeout')
        workers = [threading.Thread(target=lambda code=code: [self.trace.remember(code) for _ in range(20)])
                   for code in codes]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(timeout=2); self.assertFalse(worker.is_alive())
        self.assertEqual(set(self.trace.history_report()), {'history_' + code for code in codes})
        ready = threading.Event(); release = threading.Event()
        def delayed():
            ready.set(); release.wait(2)
            self.trace.remember('phone_reply_failed')
        delayed_worker = threading.Thread(target=delayed)
        delayed_worker.start(); self.assertTrue(ready.wait(1))
        self.trace.cancel(); release.set(); delayed_worker.join(timeout=2)
        self.assertFalse(delayed_worker.is_alive())
        self.assertEqual(self.trace.history_report(), ())


if __name__ == '__main__': unittest.main()

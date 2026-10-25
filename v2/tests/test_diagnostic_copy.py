"""Fixed build markers and fresh diagnostic copies; no assets or network."""
import os
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import phone_launcher as launcher
import test_connection_controls as controls
from tailscale_transport import ERROR_CODES

BUILD = 'diagnostics_identity_v4'


class DiagnosticFormatTests(unittest.TestCase):
    def test_empty_results_always_identify_build_and_explicitly_say_no_result(self):
        self.assertEqual(launcher.DIAGNOSTIC_PROTOCOL, 4)
        self.assertEqual(launcher.BUILD_CODE, BUILD)
        for empty in (None, (), [], '', (BUILD,), BUILD):
            self.assertEqual(launcher.format_diagnostic(empty), BUILD + '\ndiag_no_result')

    def test_build_is_first_once_and_every_identity_or_history_reason_is_preserved(self):
        codes = ('whois_stable_id_mismatch', BUILD, 'whois_machine_authorized_missing',
                 'whois_stable_id_mismatch', 'history_proxy_request_arrived',
                 'history_whois_address_mismatch', BUILD)
        expected = (BUILD, 'whois_stable_id_mismatch', 'whois_machine_authorized_missing',
                    'history_proxy_request_arrived', 'history_whois_address_mismatch')
        copied = launcher.format_diagnostic(codes)
        self.assertEqual(copied.splitlines(), list(expected))
        self.assertTrue(set(copied.splitlines()) <= ERROR_CODES)

    def test_unknown_sensitive_values_are_filtered_before_build_prefix_and_deduplication(self):
        values = ('PRIVATE_IDENTITY_CANARY', 'https://private.invalid/path?code=01234567',
                  'Cookie=PRIVATE_TOKEN', None, 101, [], {}, 'phone_page_sent')
        copied = launcher.format_diagnostic(values)
        self.assertEqual(copied, BUILD + '\ntransport_internal_error\nphone_page_sent')
        for private in ('PRIVATE', 'https://', 'Cookie', '01234567'):
            self.assertNotIn(private, copied)

    def test_copy_reformatting_is_idempotent_and_never_duplicates_build(self):
        initial = launcher.format_diagnostic(('check_whois_failed', 'whois_login_mismatch'))
        self.assertEqual(launcher.format_diagnostic(initial), initial)
        pending = launcher.format_diagnostic(('diag_check_in_progress',))
        self.assertEqual(pending, BUILD + '\ndiag_check_in_progress')
        self.assertNotIn('diag_no_result', pending)
        self.assertEqual(launcher.format_diagnostic(pending), pending)


@unittest.skipUnless(os.name == 'nt', 'Windows native fresh diagnostic clipboard')
class DiagnosticCopyNativeTests(unittest.TestCase):
    run_panel = controls.ConnectionControlTests.run_panel

    def _callTestMethod(self, method):
        flag = 'GOOSE_DIAGNOSTIC_COPY_GUI_CHILD'
        case = self._testMethodName
        if os.environ.get(flag) == case:
            return super()._callTestMethod(method)
        environment = os.environ.copy(); environment[flag] = case
        try:
            result = subprocess.run(
                [sys.executable, '-X', 'utf8', '-m', 'unittest',
                 f'test_diagnostic_copy.DiagnosticCopyNativeTests.{case}', '-v'],
                cwd=Path(__file__).resolve().parent, env=environment,
                capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=40)
        except subprocess.TimeoutExpired:
            self.fail('Native fresh-clipboard GUI child exceeded 40 seconds')
        details = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, 'Native fresh-clipboard GUI child failed:\n' + details)
        self.assertIn('Ran 1 test', result.stderr, 'Native GUI case did not execute:\n' + details)
        self.assertNotIn('skipped=', result.stderr, 'Native GUI case must execute:\n' + details)

    def test_initial_and_pending_copy_replace_old_clipboard_with_current_build_and_state(self):
        def scenario(panel, instances, starts):
            self.assertIn(BUILD, panel.app.title())
            panel.app.clipboard_clear(); panel.app.clipboard_append('OLD_RESULTS_SENTINEL')
            panel.button('复制诊断码（仅固定代码）').invoke()
            self.assertEqual(panel.app.clipboard_get(), BUILD + '\ndiag_no_result')

            panel.select_and_start()
            panel.app.clipboard_clear(); panel.app.clipboard_append('OLD_START_SENTINEL')
            panel.button('复制诊断码（仅固定代码）').invoke()
            self.assertEqual(panel.app.clipboard_get(), BUILD + '\ndiag_no_result')

            owner = panel.check(); owner.release.set()
            panel.wait(lambda: owner.completed.is_set() and '完整固定码可复制' in panel.status())
            panel.button('复制诊断码（仅固定代码）').invoke()
            self.assertEqual(panel.app.clipboard_get(),
                             BUILD + '\ncheck_whois_ok\ncheck_local_https_ok\nphone_page_sent')

            owner.release.clear(); owner.entered.clear(); owner.completed.clear()
            self.assertIs(panel.check(), owner)
            self.assertFalse(owner.completed.is_set())
            panel.button('复制诊断码（仅固定代码）').invoke()
            self.assertEqual(panel.app.clipboard_get(), BUILD + '\ndiag_check_in_progress')
            self.assertEqual(len(starts), 1)
            panel.stop()
        self.run_panel(scenario)


if __name__ == '__main__': unittest.main()

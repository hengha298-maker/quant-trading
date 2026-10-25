"""Owner identity report before files or listener creation; fictional CLI only."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tailscale_transport as phone
from test_phone import AUTHORITY, DEVICE, FakeCLI


class IdentityPreflightTests(unittest.TestCase):
    def test_all_blockers_reported_before_any_p1_files_or_server_are_read(self):
        cli = FakeCLI(); cli.verified_version = (1, 104, 2)
        cli.who['Node']['StableID'] = 'nWrongFixture'
        del cli.who['Node']['MachineAuthorized']
        cli.who['UserProfile']['LoginName'] = 'other@example.invalid'
        share = phone.PhoneShare(cli); output = io.StringIO()
        with patch.object(phone, 'create_server', side_effect=AssertionError('no data read')) as fixture, \
             patch.object(phone, 'create_private_server', side_effect=AssertionError('no data read')) as private, \
             patch.object(phone.subprocess, 'Popen', side_effect=AssertionError('no listener')) as process, \
             redirect_stdout(output), self.assertRaises(phone.TailnetError) as failure:
            share.start('FICTIONAL_CODE', DEVICE, snapshot_file='FICTIONAL_SNAPSHOT', market_files=[])
        self.assertEqual(failure.exception.code, 'whois_stable_id_mismatch')
        self.assertEqual(share.identity_report, ('whois_stable_id_mismatch',
                         'whois_machine_authorized_missing', 'whois_login_mismatch'))
        fixture.assert_not_called(); private.assert_not_called(); process.assert_not_called()
        self.assertIsNone(share.server)
        self.assertEqual(output.getvalue(), 'whois_stable_id_mismatch\n')

    def test_no_blocker_is_silently_normalized_or_used_to_open_files(self):
        cli = FakeCLI(); cli.verified_version = (1, 104, 2)
        cli.who['Node']['User'] = str(DEVICE.user_id)
        cli.who['UserProfile']['ID'] = str(DEVICE.user_id)
        cli.who['Node']['MachineAuthorized'] = 'true'
        share = phone.PhoneShare(cli)
        with patch.object(phone, 'create_private_server', side_effect=AssertionError('no files')), \
             redirect_stdout(io.StringIO()), self.assertRaises(phone.TailnetError):
            share.start('FICTIONAL_CODE', DEVICE, snapshot_file='FICTIONAL_SNAPSHOT', market_files=[])
        self.assertEqual(set(share.identity_report), {'whois_node_user_type',
                         'whois_machine_authorized_type', 'whois_profile_id_type'})

    def test_owner_check_reuses_all_predicates_without_reading_assets_or_needing_phone(self):
        cli = FakeCLI(); cli.verified_version = (1, 104, 2)
        cli.who['Node']['MachineAuthorized'] = False
        cli.who['Node']['Expired'] = True
        share = phone.PhoneShare(cli)
        share.server = SimpleNamespace(authority=AUTHORITY)
        share.gate = phone.TailnetGate(cli, DEVICE); share.gate.ready = True
        share.gate.trace.remember('proxy_xff_not_selected')
        # No sleeping or network; finish the bounded observation immediately.
        share.cancel = SimpleNamespace(is_set=lambda: False, wait=lambda _seconds: False)
        with patch.object(phone, 'check_local_https', return_value=('check_local_https_ok',)), \
             patch.object(phone, 'create_server', side_effect=AssertionError('no reads')), \
             patch.object(phone, 'create_private_server', side_effect=AssertionError('no reads')):
            report = share.check_connection()
        for code in ('check_whois_failed', 'whois_machine_unauthorized', 'whois_key_expired_flag',
                     'phone_request_not_observed', 'history_proxy_xff_not_selected'):
            self.assertIn(code, report)
        self.assertNotIn('device_denied', report)
        self.assertTrue(set(report) <= phone.ERROR_CODES)


if __name__ == '__main__': unittest.main()

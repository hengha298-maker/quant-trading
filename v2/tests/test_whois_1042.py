"""Official whois expiry encodings; fictional identities and no tailnet IO.

v1.104.2 tailcfg.Node.KeyExpiry has json:\",omitzero\"; v1.90.9 does not.
No missing-field inference is permitted for an unverified CLI version.
"""
from email.message import Message
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tailscale_transport as phone
from test_phone import AUTHORITY, DEVICE, FakeCLI, identity, status


class Whois1042Tests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCLI()
        self.cli.verified_version = (1, 104, 2)
        self.gate = phone.TailnetGate(self.cli, DEVICE)
        self.gate.ready = True

    def assert_denied(self, code='device_denied'):
        with self.assertRaises(phone.TailnetError) as error:
            self.gate.verify_identity(DEVICE.ips[0])
        self.assertEqual(error.exception.code, code)

    def test_1042_omitted_zero_expiry_is_valid_for_both_approved_addresses(self):
        del self.cli.who['Node']['KeyExpiry']
        for ip in DEVICE.ips:
            self.assertEqual(self.gate.verify_identity(ip), DEVICE.node_id)

    def test_old_and_unknown_versions_cannot_infer_disabled_expiry(self):
        del self.cli.who['Node']['KeyExpiry']
        for version in ((1, 90, 9), (1, 104, 1), (1, 104, 3), (1, 106, 0), '1.104.2', None):
            self.cli.verified_version = version
            self.assert_denied()
        del self.cli.verified_version
        self.assert_denied()

    def test_explicit_old_zero_timestamp_and_valid_future_remain_supported(self):
        for version in ((1, 90, 9), (1, 104, 2), None):
            self.cli.verified_version = version
            for value in ('0001-01-01T00:00:00Z', '2099-01-01T00:00:00Z'):
                self.cli.who['Node']['KeyExpiry'] = value
                self.assertEqual(self.gate.verify_identity(DEVICE.ips[0]), DEVICE.node_id)

    def test_explicit_null_types_invalid_and_expired_values_are_denied(self):
        for value in (None, False, 0, [], {}, '', 'invalid', '2000-01-01T00:00:00Z'):
            self.cli.who['Node']['KeyExpiry'] = value
            self.assert_denied()

    def test_expired_flag_is_strict_and_cannot_be_bypassed_by_omitted_expiry(self):
        del self.cli.who['Node']['KeyExpiry']
        for value in (True, None, 0, 'false'):
            self.cli.who['Node']['Expired'] = value
            self.assert_denied()
        self.cli.who['Node']['Expired'] = False
        self.assertEqual(self.gate.verify_identity(DEVICE.ips[0]), DEVICE.node_id)
        del self.cli.who['Node']['Expired']
        self.assertEqual(self.gate.verify_identity(DEVICE.ips[0]), DEVICE.node_id)

    def test_omitted_expiry_does_not_bypass_identity_authorization_or_address_checks(self):
        for key, value in [('StableID', 'nOther'), ('User', 202), ('User', True),
                           ('MachineAuthorized', False), ('MachineAuthorized', None),
                           ('Tags', ['tag:fixture']), ('Addresses', ['100.101.102.104/32']),
                           ('Addresses', ['100.101.102.0/24'])]:
            self.cli.who = identity(); del self.cli.who['Node']['KeyExpiry']
            self.cli.who['Node'][key] = value
            self.assert_denied()
        for key, value in [('ID', 202), ('LoginName', 'other@example.invalid')]:
            self.cli.who = identity(); del self.cli.who['Node']['KeyExpiry']
            self.cli.who['UserProfile'][key] = value
            self.assert_denied()
        with patch.object(self.cli, 'whois', side_effect=AssertionError('unapproved IP must not query')):
            with self.assertRaises(phone.TailnetError) as error:
                self.gate.verify_identity('100.101.102.104')
            self.assertEqual(error.exception.code, 'device_denied')

    def test_version_is_cached_only_after_successful_complete_inspection(self):
        cli = object.__new__(phone.Tailscale)
        with patch.object(cli, 'run', side_effect=['1.104.2', status()]):
            cli.inspect()
        self.assertEqual(cli.verified_version, (1, 104, 2))
        malformed = status(); malformed['Self']['Online'] = False
        cli = object.__new__(phone.Tailscale)
        with patch.object(cli, 'run', side_effect=['1.104.2', malformed]), self.assertRaises(phone.TailnetError):
            cli.inspect()
        self.assertFalse(hasattr(cli, 'verified_version'))

    def test_proxy_header_and_route_guards_still_precede_identity_lookup(self):
        request = SimpleNamespace(headers=Message(), path=self.gate.prefix + '/readonly/',
                                  client_address=('127.0.0.1', 42), server=SimpleNamespace(authority=AUTHORITY))
        request.headers['Host'] = AUTHORITY
        request.headers['X-Forwarded-Host'] = AUTHORITY
        request.headers['X-Forwarded-Proto'] = 'https'
        request.headers['X-Forwarded-For'] = DEVICE.ips[0]
        request.headers['Tailscale-User-Login'] = DEVICE.login
        del self.cli.who['Node']['KeyExpiry']
        self.assertEqual(self.gate.verify(request), DEVICE.node_id)
        self.assertEqual(request.path, '/readonly/')
        request.path = self.gate.prefix + '/readonly/'
        request.headers['X-Forwarded-For'] = DEVICE.ips[0]  # duplicate header
        with patch.object(self.gate, 'verify_identity', side_effect=AssertionError('must reject before whois')):
            with self.assertRaises(phone.TailnetError) as error:
                self.gate.verify(request)
            self.assertEqual(error.exception.code, 'proxy_header_denied')

    def test_cli_diagnostic_codes_survive_and_malformed_identity_has_fixed_code(self):
        with patch.object(self.cli, 'whois', side_effect=phone.TailnetError('cli_whois_timeout')):
            self.assert_denied('cli_whois_timeout')
        for document in (None, [], {}, {'Node': {}, 'UserProfile': {}},
                         {'Node': {'Addresses': ['PRIVATE_INVALID_CANARY']}, 'UserProfile': {}}):
            self.cli.who = document
            self.assert_denied('identity_unavailable')


if __name__ == '__main__': unittest.main()

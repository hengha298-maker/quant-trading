"""Actual official-Go serialization with synthetic inputs, never live data.

The checked-in fixture was emitted by tailcfg.Node/UserProfile,
ipnstate.PeerStatus/Status and apitype.WhoIsResponse from v1.104.2.
These tests are offline schema/authorization checks, NOT iPhone acceptance.
"""
from copy import deepcopy
import ipaddress
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tailscale_transport as phone
from identity_diagnostics import WHOIS_CODES
from tailscale_1042_fixture import OFFICIAL_1042


CASES = {case['Name']: case for case in OFFICIAL_1042['Cases']}


class OfficialIdentitySchemaTests(unittest.TestCase):
    def setup_gate(self, case):
        # Use the real production inspector to select the device from the
        # official Status type output, then the real Gate for official whois.
        cli = object.__new__(phone.Tailscale)
        status = deepcopy(CASES['authorized_nonexpiring']['Status'])
        with patch.object(cli, 'run', side_effect=['1.104.2', status]):
            _, devices = cli.inspect()
        self.assertEqual(len(devices), 1)
        gate = phone.TailnetGate(cli, devices[0])
        return gate, cli

    def test_fixture_provenance_and_id_types_are_official_encoder_outputs(self):
        self.assertEqual(OFFICIAL_1042['GoVersion'], 'go1.27.3')
        self.assertIn('fictional', OFFICIAL_1042['Origin'])
        for case in CASES.values():
            node = case['Whois']['Node']; profile = case['Whois']['UserProfile']
            peer = next(iter(case['Status']['Peer'].values()))
            user = case['Status']['User'][str(peer['UserID'])]
            self.assertIs(type(node['StableID']), str)
            self.assertEqual(node['StableID'], peer['ID'])
            self.assertIs(type(node['User']), int)
            self.assertIs(type(profile['ID']), int)
            self.assertIs(type(peer['UserID']), int)
            self.assertEqual(node['User'], peer['UserID'])
            self.assertEqual(profile['ID'], user['ID'])
            self.assertEqual(profile['ID'], node['User'])
            self.assertEqual(profile['LoginName'], user['LoginName'])
            self.assertEqual({str(ipaddress.ip_interface(address).ip) for address in node['Addresses']}, set(peer['TailscaleIPs']))
            self.assertTrue(all(ipaddress.ip_network(address).prefixlen == ipaddress.ip_network(address).max_prefixlen for address in node['Addresses']))

    def test_authorized_nonexpiring_official_node_is_accepted_for_both_addresses(self):
        case = CASES['authorized_nonexpiring']; node = case['Whois']['Node']
        self.assertNotIn('KeyExpiry', node)
        self.assertIs(node['MachineAuthorized'], True)
        gate, cli = self.setup_gate(case)
        with patch.object(cli, 'whois', return_value=deepcopy(case['Whois'])):
            for address in gate.device.ips:
                self.assertEqual(gate.identity_failures(address), ())
                self.assertEqual(gate.verify_identity(address), gate.device.node_id)

    def test_authorized_future_expiry_official_node_is_accepted(self):
        case = CASES['authorized_future']; node = case['Whois']['Node']
        self.assertEqual(node['KeyExpiry'], '2099-01-01T00:00:00Z')
        gate, cli = self.setup_gate(case)
        with patch.object(cli, 'whois', return_value=deepcopy(case['Whois'])):
            self.assertEqual(gate.identity_failures(gate.device.ips[0]), ())
            self.assertEqual(gate.verify_identity(gate.device.ips[0]), gate.device.node_id)

    def test_official_false_authorization_is_omitted_and_must_not_become_true(self):
        case = CASES['unauthorized_omitted']
        self.assertNotIn('MachineAuthorized', case['Whois']['Node'])
        gate, cli = self.setup_gate(case)
        with patch.object(cli, 'whois', return_value=deepcopy(case['Whois'])):
            failures = gate.identity_failures(gate.device.ips[0])
            self.assertEqual(failures, ('whois_machine_authorized_missing',))
            self.assertTrue(set(failures) <= WHOIS_CODES)
            with self.assertRaises(phone.TailnetError) as denied:
                gate.verify_identity(gate.device.ips[0])
            self.assertEqual(denied.exception.code, 'whois_machine_authorized_missing')

    def test_official_expired_time_and_flag_are_both_reported_and_denied(self):
        case = CASES['expired']
        self.assertIs(case['Whois']['Node']['Expired'], True)
        gate, cli = self.setup_gate(case)
        with patch.object(cli, 'whois', return_value=deepcopy(case['Whois'])):
            failures = gate.identity_failures(gate.device.ips[0])
            self.assertIn('whois_key_expired_flag', failures)
            self.assertIn('whois_key_expiry_invalid_or_expired', failures)
            self.assertTrue(set(failures) <= WHOIS_CODES)
            with self.assertRaises(phone.TailnetError):
                gate.verify_identity(gate.device.ips[0])
        with patch.object(cli, 'run', side_effect=['1.104.2', deepcopy(case['Status'])]):
            _, devices = cli.inspect()
        self.assertEqual(devices, [])


if __name__ == '__main__':
    unittest.main()

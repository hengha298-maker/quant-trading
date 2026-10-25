"""Actual official-Go serialization with synthetic inputs, never live data.

The checked-in fixture was emitted by tailcfg.Node/UserProfile,
ipnstate.PeerStatus/Status and apitype.WhoIsResponse from v1.104.2.
These tests are offline schema/authorization checks, NOT iPhone acceptance.
"""
from copy import deepcopy
from contextlib import contextmanager
from http.client import HTTPMessage
import ipaddress
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tailscale_transport as phone
from identity_diagnostics import WHOIS_CODES, identity_failures as raw_identity_failures
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

    @contextmanager
    def identity_inputs(self, cli, case):
        # The fallback must read a fresh status document, independently of the
        # selection snapshot. No tailscaled process or network is contacted.
        def fresh_status(*args):
            self.assertEqual(args, ('status', '--json'))
            return deepcopy(case['Status'])
        with patch.object(cli, 'whois', return_value=deepcopy(case['Whois'])), \
             patch.object(cli, 'run', side_effect=fresh_status) as status_read:
            yield status_read

    def serve_handler(self, gate, address):
        authority = 'fixture-pc.fixture-tail.ts.net:8443'
        headers = HTTPMessage()
        for name, value in {'X-Forwarded-For': address,
                            'X-Forwarded-Proto': 'https',
                            'X-Forwarded-Host': authority,
                            'Tailscale-User-Login': gate.device.login}.items():
            headers[name] = value
        return SimpleNamespace(headers=headers, path=gate.prefix + '/readonly/',
                               client_address=('127.0.0.1', 54321),
                               server=SimpleNamespace(authority=authority))

    def test_fixture_provenance_and_id_types_are_official_encoder_outputs(self):
        self.assertEqual(OFFICIAL_1042['GoVersion'], 'go1.27.3')
        self.assertIn('fictional', OFFICIAL_1042['Origin'])
        for case in CASES.values():
            node = case['Whois']['Node']; profile = case['Whois']['UserProfile']
            if not case['Status']['Peer']:
                self.assertEqual(case['Name'], 'unauthorized_omitted')
                continue
            map_key, peer = next(iter(case['Status']['Peer'].items()))
            user = case['Status']['User'][str(peer['UserID'])]
            self.assertEqual(map_key, peer['PublicKey'])
            self.assertEqual(node['Key'], peer['PublicKey'])
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
        with self.identity_inputs(cli, case):
            for address in gate.device.ips:
                self.assertEqual(gate.identity_failures(address), ())
                self.assertEqual(gate.verify_identity(address), gate.device.node_id)

    def test_authorized_future_expiry_official_node_is_accepted(self):
        case = CASES['authorized_future']; node = case['Whois']['Node']
        self.assertEqual(node['KeyExpiry'], '2099-01-01T00:00:00Z')
        gate, cli = self.setup_gate(case)
        with self.identity_inputs(cli, case):
            self.assertEqual(gate.identity_failures(gate.device.ips[0]), ())
            self.assertEqual(gate.verify_identity(gate.device.ips[0]), gate.device.node_id)

    def test_omission_without_current_peer_binding_is_denied(self):
        # The official encoder omits a false Go input; that omission is not
        # itself evidence of either approval or rejection. This scenario also
        # has no current network-map peer, so no positive replacement proof.
        case = CASES['unauthorized_omitted']
        self.assertNotIn('MachineAuthorized', case['Whois']['Node'])
        self.assertEqual(case['Status']['Peer'], {})
        gate, cli = self.setup_gate(case)
        raw_failures = raw_identity_failures(case['Whois'], gate.device,
            gate.device.ips[0], (1, 104, 2), phone._not_expired)
        self.assertEqual(raw_failures, ('whois_machine_authorized_missing',))
        with self.identity_inputs(cli, case) as status_read:
            failures = gate.identity_failures(gate.device.ips[0])
            self.assertIn('whois_machine_authorized_missing', failures)
            self.assertTrue(set(failures) <= WHOIS_CODES)
            with self.assertRaises(phone.TailnetError) as denied:
                gate.verify_identity(gate.device.ips[0])
            self.assertEqual(denied.exception.code, 'whois_machine_authorized_missing')
            self.assertGreaterEqual(status_read.call_count, 2)

    def test_omitted_peer_authorization_requires_fresh_key_and_trusted_serve(self):
        case = CASES['authorized_peer_omitted']
        self.assertNotIn('MachineAuthorized', case['Whois']['Node'])
        self.assertNotEqual(case['Whois']['Node']['Key'], 'nodekey:' + '0' * 64)
        gate, cli = self.setup_gate(case)
        gate.ready = True
        with self.identity_inputs(cli, case) as status_read:
            for address in gate.device.ips:
                # Owner preflight is eligible, but only the verified inbound
                # Serve request supplies the authenticated-traffic evidence.
                self.assertEqual(gate.identity_failures(address), ())
                with self.assertRaises(phone.TailnetError) as denied:
                    gate.verify_identity(address)
                self.assertEqual(denied.exception.code, 'whois_authorization_requires_serve')
                self.assertEqual(gate.verify(self.serve_handler(gate, address)), gate.device.node_id)
            self.assertEqual(status_read.call_count, 6)

    def test_omitted_authorization_with_zero_public_key_is_denied(self):
        case = CASES['zero_key_peer_omitted']
        self.assertEqual(case['Whois']['Node']['Key'], 'nodekey:' + '0' * 64)
        gate, cli = self.setup_gate(case)
        gate.ready = True
        with self.identity_inputs(cli, case):
            failures = gate.identity_failures(gate.device.ips[0])
            self.assertIn('whois_machine_authorized_missing', failures)
            self.assertTrue(set(failures) <= WHOIS_CODES)
            with self.assertRaises(phone.TailnetError):
                gate.verify(self.serve_handler(gate, gate.device.ips[0]))

    def test_explicit_false_cannot_use_a_current_peer_to_override_denial(self):
        # Explicit false cannot be emitted by this Go omitempty field. This
        # deliberately malformed/local-API scenario checks the hard denial.
        case = deepcopy(CASES['authorized_peer_omitted'])
        case['Whois']['Node']['MachineAuthorized'] = False
        gate, cli = self.setup_gate(case)
        gate.ready = True
        with self.identity_inputs(cli, case):
            self.assertIn('whois_machine_unauthorized', gate.identity_failures(gate.device.ips[0]))
            with self.assertRaises(phone.TailnetError) as denied:
                gate.verify(self.serve_handler(gate, gate.device.ips[0]))
            self.assertEqual(denied.exception.code, 'whois_machine_unauthorized')

    def test_official_expired_time_and_flag_are_both_reported_and_denied(self):
        case = CASES['expired']
        self.assertIs(case['Whois']['Node']['Expired'], True)
        gate, cli = self.setup_gate(case)
        with self.identity_inputs(cli, case):
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

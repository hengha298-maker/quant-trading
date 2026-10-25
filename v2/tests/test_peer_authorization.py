"""Offline authorization regressions with official-type, fictional inputs.

These tests exercise the trusted local status/WhoIs boundary and Serve's
loopback request guards. They make no claim about a real iPhone or tailnet.
No P1 source, asset document, local daemon or network connection is used.
"""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from http.client import HTTPMessage
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import identity_diagnostics as identity
import tailscale_transport as phone
from tailscale_1042_fixture import OFFICIAL_1042


KEY = 'nodekey:' + '1' * 64
OTHER_KEY = 'nodekey:' + '2' * 64
SELF_KEY = 'nodekey:' + '3' * 64
ZERO_KEY = 'nodekey:' + '0' * 64


def authorized_inputs():
    """Deep-copy an official schema, then deliberately bind synthetic keys.

    Omission by itself never supplies authorization; each accepting test also
    supplies the current peer's three-way nonzero key and exact identity.
    """
    case = deepcopy(next(case for case in OFFICIAL_1042['Cases']
                         if case['Name'] == 'authorized_future'))
    document, status = case['Whois'], case['Status']
    peer = next(iter(status['Peer'].values()))
    document['Node'].pop('MachineAuthorized', None)
    document['Node']['Key'] = KEY
    peer['PublicKey'] = KEY
    status['Peer'] = {KEY: peer}
    status['Self']['PublicKey'] = SELF_KEY
    profile = document['UserProfile']
    device = phone.Device(document['Node']['StableID'], profile['ID'],
                          profile['LoginName'], peer['HostName'],
                          tuple(peer['TailscaleIPs']))
    return document, status, device


def serve_handler(gate, **changes):
    authority = 'fixture-pc.fixture-tail.ts.net:8443'
    values = {'X-Forwarded-For': gate.device.ips[0],
              'X-Forwarded-Proto': 'https',
              'X-Forwarded-Host': authority,
              'Tailscale-User-Login': gate.device.login}
    values.update(changes.pop('headers', {}))
    headers = HTTPMessage()
    for name, value in values.items():
        if value is not None:
            headers[name] = value
    args = dict(headers=headers, path=gate.prefix + '/readonly/',
                client_address=('127.0.0.1', 54321),
                server=SimpleNamespace(authority=authority))
    args.update(changes)
    return SimpleNamespace(**args)


class PeerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.document, self.status, self.device = authorized_inputs()

    def failures(self, status=None, document=None, *, use_none_status=False):
        result = identity.peer_authorization_failures(
            None if use_none_status else (self.status if status is None else status),
            self.document if document is None else document,
            self.device, phone._not_expired)
        self.assertIs(type(result), tuple)
        self.assertEqual(len(result), len(set(result)))
        self.assertTrue(set(result) <= identity.WHOIS_CODES)
        return result

    def peer(self, status=None):
        return next(iter((self.status if status is None else status)['Peer'].values()))

    def test_full_current_binding_accepts_without_treating_omission_as_true(self):
        self.assertNotIn('MachineAuthorized', self.document['Node'])
        self.assertEqual(self.failures(), ())
        self.assertEqual(identity.identity_failures(self.document, self.device,
            self.device.ips[0], (1, 104, 2), phone._not_expired),
            ('whois_machine_authorized_missing',))

    def test_status_document_and_peer_shapes_fail_closed(self):
        self.assertIn('peer_auth_status_invalid', self.failures(use_none_status=True))
        for status in ([], 'PRIVATE_STATUS_CANARY', True):
            with self.subTest(status_type=type(status).__name__):
                self.assertIn('peer_auth_status_invalid', self.failures(status))
        for peers in (None, [], {KEY: None}):
            status = deepcopy(self.status); status['Peer'] = peers
            with self.subTest(peers_type=type(peers).__name__):
                self.assertIn('peer_auth_status_invalid', self.failures(status))
        for document in ({}, {'Node': []}):
            self.assertIn('peer_auth_status_invalid', self.failures(document=document))

    def test_daemon_must_still_be_running(self):
        for value in ('Stopped', 'NeedsLogin', None, True):
            status = deepcopy(self.status); status['BackendState'] = value
            self.assertIn('peer_auth_not_running', self.failures(status))

    def test_self_must_be_online_in_current_netmap(self):
        for own in (None, [], {}):
            status = deepcopy(self.status); status['Self'] = own
            self.assertIn('peer_auth_self_invalid', self.failures(status))
        for field in ('Online', 'InNetworkMap'):
            for value in (False, None, 1, 'true'):
                status = deepcopy(self.status); status['Self'][field] = value
                with self.subTest(field=field, value=value):
                    self.assertIn('peer_auth_self_invalid', self.failures(status))

    def test_self_owner_requires_exact_integer_user_id(self):
        for value in (102, True, '101', None):
            status = deepcopy(self.status); status['Self']['UserID'] = value
            self.assertIn('peer_auth_owner_mismatch', self.failures(status))

    def test_local_expired_or_malformed_self_expiry_cannot_supply_fresh_evidence(self):
        for field, value in (('Expired', True), ('Expired', None), ('Expired', 0),
                             ('KeyExpiry', '2000-01-01T00:00:00Z'),
                             ('KeyExpiry', 'PRIVATE_EXPIRY_CANARY'),
                             ('KeyExpiry', None), ('KeyExpiry', 1)):
            status = deepcopy(self.status); status['Self'][field] = value
            with self.subTest(field=field, value=value):
                self.assertIn('peer_auth_self_invalid', self.failures(status))

    def test_owner_profile_requires_exact_id_and_login(self):
        for field, value in (('ID', 102), ('ID', True), ('ID', '101'),
                             ('LoginName', 'other@example.invalid'),
                             ('LoginName', None)):
            status = deepcopy(self.status)
            status['User'][str(self.device.user_id)][field] = value
            with self.subTest(field=field, value=value):
                self.assertIn('peer_auth_login_mismatch', self.failures(status))
        for users in ({}, None, []):
            status = deepcopy(self.status); status['User'] = users
            self.assertIn('peer_auth_login_mismatch', self.failures(status))

    def test_peer_must_exist_once_for_selected_stable_id(self):
        status = deepcopy(self.status); status['Peer'] = {}
        self.assertIn('peer_auth_peer_missing', self.failures(status))
        status = deepcopy(self.status); self.peer(status)['ID'] = 'nOtherPhone'
        self.assertIn('peer_auth_peer_missing', self.failures(status))
        status = deepcopy(self.status)
        status['Peer'][OTHER_KEY] = deepcopy(self.peer(status))
        status['Peer'][OTHER_KEY]['PublicKey'] = OTHER_KEY
        self.assertIn('peer_auth_peer_ambiguous', self.failures(status))

    def test_current_peer_must_remain_online_in_netmap_and_ios(self):
        for field, expected in (('InNetworkMap', 'peer_auth_not_in_netmap'),
                                ('Online', 'peer_auth_offline')):
            for value in (False, None, 1, 'true'):
                status = deepcopy(self.status); self.peer(status)[field] = value
                with self.subTest(field=field, value=value):
                    self.assertIn(expected, self.failures(status))
        for value in ('windows', 'ios', None, 1):
            status = deepcopy(self.status); self.peer(status)['OS'] = value
            self.assertIn('peer_auth_not_ios', self.failures(status))

    def test_peer_whois_identity_and_user_must_match_selection(self):
        document = deepcopy(self.document); document['Node']['StableID'] = 'nOtherPhone'
        self.assertIn('peer_auth_identity_mismatch', self.failures(document=document))
        for value in (102, True, '101', None):
            status = deepcopy(self.status); self.peer(status)['UserID'] = value
            self.assertIn('peer_auth_user_mismatch', self.failures(status))
        document = deepcopy(self.document); document['Node']['User'] = 102
        self.assertIn('peer_auth_user_mismatch', self.failures(document=document))

    def test_map_peer_and_whois_keys_must_all_be_nonzero_valid_node_keys(self):
        for place in ('map', 'peer', 'whois'):
            for bad in (ZERO_KEY, '', None, 'nodekey:PRIVATE_KEY_CANARY', 'nodekey:' + 'A' * 64):
                status, document = deepcopy(self.status), deepcopy(self.document)
                if place == 'map': status['Peer'] = {bad: self.peer(status)}
                elif place == 'peer': self.peer(status)['PublicKey'] = bad
                else: document['Node']['Key'] = bad
                with self.subTest(place=place, bad=bad):
                    self.assertIn('peer_auth_key_invalid', self.failures(status, document))

    def test_any_public_key_disagreement_rejects_even_valid_keys(self):
        for place in ('map', 'peer', 'whois'):
            status, document = deepcopy(self.status), deepcopy(self.document)
            if place == 'map': status['Peer'] = {OTHER_KEY: self.peer(status)}
            elif place == 'peer': self.peer(status)['PublicKey'] = OTHER_KEY
            else: document['Node']['Key'] = OTHER_KEY
            with self.subTest(place=place):
                self.assertIn('peer_auth_key_mismatch', self.failures(status, document))

    def test_ip_text_order_and_equivalent_ipv6_are_not_credentials(self):
        status = deepcopy(self.status)
        self.peer(status)['TailscaleIPs'] = ['fd7a:115c:a1e0:0:0:0:0:103', self.device.ips[0]]
        document = deepcopy(self.document)
        document['Node']['Addresses'].reverse()
        self.assertEqual(self.failures(status, document), ())

    def test_full_selected_address_set_is_required_from_status_and_whois(self):
        for addresses in ([self.device.ips[0]], list(self.device.ips) + ['100.101.102.104'],
                          ['192.0.2.1', self.device.ips[1]]):
            status = deepcopy(self.status); self.peer(status)['TailscaleIPs'] = addresses
            self.assertIn('peer_auth_addresses_changed', self.failures(status))
        document = deepcopy(self.document); document['Node']['Addresses'].pop()
        self.assertIn('peer_auth_addresses_changed', self.failures(document=document))

    def test_malformed_or_duplicate_status_addresses_and_non_host_whois_prefix_reject(self):
        for addresses in (None, [], self.device.ips, [True], ['PRIVATE_ADDRESS_CANARY'],
                          [self.device.ips[0], self.device.ips[0]]):
            status = deepcopy(self.status); self.peer(status)['TailscaleIPs'] = addresses
            self.assertIn('peer_auth_addresses_invalid', self.failures(status))
        for addresses in (None, ['100.101.102.0/24'], ['100.101.102.103/24'], [True]):
            document = deepcopy(self.document); document['Node']['Addresses'] = addresses
            self.assertIn('peer_auth_addresses_invalid', self.failures(document=document))

    def test_tagged_shared_or_malformed_peer_eligibility_is_denied(self):
        for field, value in (('Tags', ['tag:fixture']), ('Tags', None),
                             ('Tags', 'tag:fixture'), ('AltSharerUserID', 102),
                             ('AltSharerUserID', True), ('AltSharerUserID', '0'),
                             ('ShareeNode', True), ('ShareeNode', None), ('ShareeNode', 0)):
            status = deepcopy(self.status); self.peer(status)[field] = value
            with self.subTest(field=field, value=value):
                self.assertIn('peer_auth_tagged_or_shared', self.failures(status))

    def test_expired_or_malformed_peer_expiry_is_denied(self):
        for value in (True, 1, None, 'false'):
            status = deepcopy(self.status); self.peer(status)['Expired'] = value
            self.assertIn('peer_auth_expired', self.failures(status))
        for value in ('2000-01-01T00:00:00Z', 'PRIVATE_EXPIRY_CANARY', None, 1):
            status = deepcopy(self.status); self.peer(status)['KeyExpiry'] = value
            self.assertIn('peer_auth_expired', self.failures(status))

    def test_outputs_are_only_fixed_codes_with_no_input_or_console_leak(self):
        status = deepcopy(self.status)
        self.peer(status)['PublicKey'] = 'PRIVATE_KEY_CANARY'
        self.peer(status)['TailscaleIPs'] = ['PRIVATE_ADDRESS_CANARY']
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            failures = self.failures(status)
        self.assertEqual(output.getvalue(), '')
        encoded = json.dumps(failures)
        for secret in ('PRIVATE', self.device.node_id, self.device.login, KEY, *self.device.ips):
            self.assertNotIn(secret, encoded)


class PeerAuthorizationGateTests(unittest.TestCase):
    def setUp(self):
        self.document, self.status, self.device = authorized_inputs()
        self.cli = SimpleNamespace(verified_version=(1, 104, 2),
            self_ips=('100.101.102.1',),
            whois=Mock(side_effect=lambda ip: deepcopy(self.document)),
            run=Mock(side_effect=lambda *args: deepcopy(self.status)))
        self.gate = phone.TailnetGate(self.cli, self.device)
        self.gate.ready = True

    def verify(self, **changes):
        return self.gate.verify(serve_handler(self.gate, **changes))

    def test_missing_1042_auth_fetches_fresh_status_for_every_serve_request(self):
        self.assertEqual(self.verify(), self.device.node_id)
        self.assertEqual(self.verify(), self.device.node_id)
        self.assertEqual(self.cli.whois.call_count, 2)
        self.assertEqual(self.cli.run.call_count, 2)
        for call in self.cli.run.call_args_list:
            self.assertEqual(call.args, ('status', '--json'))
        self.assertNotIn('MachineAuthorized', self.document['Node'])

    def test_eligible_owner_preflight_never_grants_default_identity_binding(self):
        self.assertEqual(self.gate.identity_failures(self.device.ips[0]), ())
        with self.assertRaises(phone.TailnetError) as denied:
            self.gate.verify_identity(self.device.ips[0])
        self.assertEqual(denied.exception.code, 'whois_authorization_requires_serve')
        self.assertEqual(self.cli.run.call_count, 2)

    def test_explicit_authorization_true_keeps_existing_path_without_status_fallback(self):
        self.document['Node']['MachineAuthorized'] = True
        self.assertEqual(self.verify(), self.device.node_id)
        self.assertEqual(self.gate.verify_identity(self.device.ips[0]), self.device.node_id)
        self.cli.run.assert_not_called()

    def test_explicit_false_or_malformed_authorization_cannot_use_peer_evidence(self):
        for value, code in ((False, 'whois_machine_unauthorized'),
                            (0, 'whois_machine_authorized_type'),
                            (None, 'whois_machine_authorized_type'),
                            ('true', 'whois_machine_authorized_type')):
            with self.subTest(value=value):
                self.document['Node']['MachineAuthorized'] = value
                with self.assertRaises(phone.TailnetError) as denied:
                    self.verify()
                self.assertEqual(denied.exception.code, code)
        self.cli.run.assert_not_called()

    def test_missing_auth_on_unaudited_versions_never_reads_or_accepts_peer_evidence(self):
        for version in ((1, 104, 1), (1, 104, 3), (1, 90, 9), None, '1.104.2'):
            self.cli.verified_version = version
            with self.subTest(version=version):
                failures = self.gate.identity_failures(self.device.ips[0])
                self.assertIn('whois_machine_authorized_missing_unverified_version', failures)
                with self.assertRaises(phone.TailnetError): self.verify()
        self.cli.run.assert_not_called()

    def test_missing_cli_or_fresh_status_error_cannot_reuse_old_eligibility(self):
        self.assertEqual(self.verify(), self.device.node_id)
        del self.cli.run
        self.assertIn('whois_machine_authorized_missing', self.gate.identity_failures(self.device.ips[0]))
        with self.assertRaises(phone.TailnetError): self.verify()
        self.cli.run = Mock(side_effect=phone.TailnetError('cli_status_timeout'))
        failures = self.gate.identity_failures(self.device.ips[0])
        self.assertIn('whois_machine_authorized_missing', failures)
        self.assertIn('cli_status_timeout', failures)
        with self.assertRaises(phone.TailnetError): self.verify()

    def test_peer_removed_offline_or_rotated_after_success_is_immediately_denied(self):
        for mutation, expected in (
                (lambda status: status.update(Peer={}), 'peer_auth_peer_missing'),
                (lambda status: next(iter(status['Peer'].values())).update(Online=False), 'peer_auth_offline'),
                (lambda status: next(iter(status['Peer'].values())).update(InNetworkMap=False), 'peer_auth_not_in_netmap'),
                (lambda status: next(iter(status['Peer'].values())).update(PublicKey=OTHER_KEY), 'peer_auth_key_mismatch')):
            with self.subTest(expected=expected):
                self.status = authorized_inputs()[1]
                self.assertEqual(self.verify(), self.device.node_id)
                mutation(self.status)
                failures = self.gate.identity_failures(self.device.ips[0])
                self.assertIn(expected, failures)
                with self.assertRaises(phone.TailnetError): self.verify()

    def test_zero_public_keys_never_supply_missing_authorization(self):
        peer = next(iter(self.status['Peer'].values()))
        peer['PublicKey'] = ZERO_KEY
        self.status['Peer'] = {ZERO_KEY: peer}
        self.document['Node']['Key'] = ZERO_KEY
        self.assertIn('peer_auth_key_invalid', self.gate.identity_failures(self.device.ips[0]))
        with self.assertRaises(phone.TailnetError): self.verify()

    def test_jail_or_unsigned_peerapi_node_never_passes_complete_peer_evidence(self):
        for field, value, code in (
                ('IsJailed', True, 'whois_jailed_node'),
                ('IsJailed', 1, 'whois_is_jailed_type'),
                ('IsJailed', None, 'whois_is_jailed_type'),
                ('UnsignedPeerAPIOnly', True, 'whois_peer_api_only'),
                ('UnsignedPeerAPIOnly', 'false', 'whois_peer_api_only_type'),
                ('UnsignedPeerAPIOnly', None, 'whois_peer_api_only_type')):
            self.document = authorized_inputs()[0]
            self.document['Node'][field] = value
            with self.subTest(field=field, value=value):
                self.assertIn(code, self.gate.identity_failures(self.device.ips[0]))
                with self.assertRaises(phone.TailnetError) as denied: self.verify()
                # The public exception preserves the first original failure;
                # the owner report above retains the precise jail/type code.
                self.assertEqual(denied.exception.code, 'whois_machine_authorized_missing')
        self.cli.run.assert_not_called()

    def test_every_trusted_ingress_guard_precedes_whois_and_status(self):
        cases = [dict(client_address=('192.0.2.1', 54321)), dict(path='/readonly/'),
                 dict(headers={'X-Forwarded-Proto': 'http'}),
                 dict(headers={'X-Forwarded-Host': 'other.example.invalid'}),
                 dict(headers={'X-Forwarded-For': '100.101.102.104'}),
                 dict(headers={'Tailscale-User-Login': 'other@example.invalid'}),
                 dict(headers={'Tailscale-Funnel-Request': '?1'}),
                 dict(headers={'Forwarded': 'for=PRIVATE_CANARY'})]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(phone.TailnetError):
                self.verify(**changes)
        self.gate.ready = False
        with self.assertRaises(phone.TailnetError): self.verify()
        self.cli.whois.assert_not_called()
        self.cli.run.assert_not_called()


if __name__ == '__main__':
    unittest.main()

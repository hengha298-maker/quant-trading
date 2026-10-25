"""Collect strict whois field failures without exposing identity values."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import identity_diagnostics as diagnostics
import tailscale_transport as phone
from test_phone import DEVICE, FakeCLI, identity


class IdentityFieldDiagnosticsTests(unittest.TestCase):
    def failures(self, document, *, version=(1, 104, 2)):
        result = diagnostics.identity_failures(document, DEVICE, DEVICE.ips[0], version, phone._not_expired)
        self.assertTrue(set(result) <= diagnostics.WHOIS_CODES)
        self.assertEqual(len(result), len(set(result)))
        return result

    def test_valid_identity_original_and_1042_zero_encodings_have_no_failures(self):
        self.assertEqual(self.failures(identity()), ())
        document = identity(); del document['Node']['KeyExpiry']
        self.assertEqual(self.failures(document), ())
        document['Node']['KeyExpiry'] = '0001-01-01T00:00:00Z'
        self.assertEqual(self.failures(document, version=(1, 90, 9)), ())

    def test_node_and_profile_missing_or_wrong_types_are_precise(self):
        for document in (None, [], 'PRIVATE_ROOT_CANARY'):
            self.assertEqual(self.failures(document), ('whois_document_type',))
        self.assertEqual(self.failures({}), ('whois_node_missing', 'whois_profile_missing'))
        self.assertEqual(self.failures({'Node': None, 'UserProfile': []}),
                         ('whois_node_type', 'whois_profile_type'))

    def test_identity_fields_never_coerce_types_or_strip_prefixes(self):
        fields = [('Node', 'StableID', 'whois_stable_id', 123, 'without-expected-prefix'),
                  ('Node', 'User', 'whois_node_user', str(DEVICE.user_id), DEVICE.user_id + 1),
                  ('UserProfile', 'ID', 'whois_profile_id', str(DEVICE.user_id), DEVICE.user_id + 1),
                  ('UserProfile', 'LoginName', 'whois_login', None, 'other@example.invalid')]
        for section, key, prefix, wrong_type, mismatch in fields:
            for kind, value in [('missing', None), ('type', wrong_type), ('mismatch', mismatch)]:
                with self.subTest(section=section, key=key, kind=kind):
                    document = identity()
                    if kind == 'missing': del document[section][key]
                    else: document[section][key] = value
                    self.assertEqual(self.failures(document), (prefix + '_' + kind,))
        for key in ('User',):
            document = identity(); document['Node'][key] = True
            self.assertEqual(self.failures(document), ('whois_node_user_type',))
        document = identity(); document['UserProfile']['ID'] = True
        self.assertEqual(self.failures(document), ('whois_profile_id_type',))

    def test_machine_authorization_missing_false_and_wrong_type_are_not_equivalent(self):
        document = identity(); del document['Node']['MachineAuthorized']
        self.assertEqual(self.failures(document), ('whois_machine_authorized_missing',))
        for value, code in [(False, 'whois_machine_unauthorized'), (None, 'whois_machine_authorized_type'),
                            (1, 'whois_machine_authorized_type'), ('true', 'whois_machine_authorized_type')]:
            document['Node']['MachineAuthorized'] = value
            self.assertEqual(self.failures(document), (code,))

    def test_tagged_and_shared_nodes_remain_rejected(self):
        for key, value, code in [('Tags', ['tag:fixture'], 'whois_tagged_node'),
                                 ('Tags', None, 'whois_tags_type'), ('Tags', [1], 'whois_tags_type'),
                                 ('Sharer', 202, 'whois_shared_node'), ('Sharer', None, 'whois_sharer_type'),
                                 ('Sharer', False, 'whois_sharer_type')]:
            document = identity(); document['Node'][key] = value
            self.assertEqual(self.failures(document), (code,))
        document = identity(); document['Node'].update(Tags=[], Sharer=0)
        self.assertEqual(self.failures(document), ())

    def test_expired_and_key_expiry_failures_are_independent(self):
        document = identity(); document['Node'].update(Expired=True, KeyExpiry=None)
        self.assertEqual(self.failures(document), ('whois_key_expired_flag', 'whois_key_expiry_null'))
        document['Node'].update(Expired=None, KeyExpiry=123)
        self.assertEqual(self.failures(document), ('whois_expired_type', 'whois_key_expiry_type'))
        document['Node'].update(Expired=False, KeyExpiry='2000-01-01T00:00:00Z')
        self.assertEqual(self.failures(document), ('whois_key_expiry_invalid_or_expired',))
        del document['Node']['KeyExpiry']
        self.assertEqual(self.failures(document, version=(1, 90, 9)),
                         ('whois_key_expiry_missing_unverified_version',))
        self.assertEqual(self.failures(document), ())

    def test_addresses_must_contain_exact_host_ip_and_every_entry_must_parse(self):
        document = identity(); del document['Node']['Addresses']
        self.assertEqual(self.failures(document), ('whois_addresses_missing',))
        for value, expected in [(None, ('whois_addresses_type',)),
                                ('100.101.102.103/32', ('whois_addresses_type',)),
                                ([], ('whois_address_mismatch',)),
                                (['100.101.102.0/24'], ('whois_address_mismatch',)),
                                (['100.101.102.104/32'], ('whois_address_mismatch',)),
                                ([None], ('whois_address_entry_type', 'whois_address_mismatch')),
                                (['PRIVATE_BAD_IP_CANARY'], ('whois_addresses_invalid', 'whois_address_mismatch'))]:
            document['Node']['Addresses'] = value
            self.assertEqual(self.failures(document), expected)
        document['Node']['Addresses'] = [DEVICE.ips[0] + '/32', 'PRIVATE_BAD_IP_CANARY']
        self.assertEqual(self.failures(document), ('whois_addresses_invalid',))

    def test_every_failure_is_collected_and_raw_document_is_not_mutated_or_printed(self):
        document = identity()
        document['Node'].update(StableID='PRIVATE_NODE_CANARY', User='PRIVATE_USER_CANARY',
                                MachineAuthorized=False, Tags=['tag:private'], Sharer=202,
                                Expired=True, KeyExpiry=None, Addresses=['PRIVATE_IP_CANARY'])
        document['UserProfile'].update(ID='PRIVATE_ID_CANARY', LoginName='PRIVATE_LOGIN_CANARY')
        before = deepcopy(document); output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            codes = self.failures(document)
        self.assertEqual(set(codes), {'whois_stable_id_mismatch', 'whois_node_user_type',
                                     'whois_machine_unauthorized', 'whois_tagged_node', 'whois_shared_node',
                                     'whois_key_expired_flag', 'whois_key_expiry_null',
                                     'whois_addresses_invalid', 'whois_address_mismatch',
                                     'whois_profile_id_type', 'whois_login_mismatch'})
        self.assertEqual(document, before); self.assertEqual(output.getvalue(), '')
        self.assertNotIn('PRIVATE', json.dumps(codes))

    def test_gate_returns_all_detailed_failures_and_verify_throws_only_first_fixed_code(self):
        cli = FakeCLI(); cli.verified_version = (1, 104, 2)
        cli.who['Node'].update(StableID='PRIVATE_NODE_CANARY', MachineAuthorized=False)
        gate = phone.TailnetGate(cli, DEVICE)
        self.assertEqual(gate.identity_failures(DEVICE.ips[0]),
                         ('whois_stable_id_mismatch', 'whois_machine_unauthorized'))
        with self.assertRaises(phone.TailnetError) as error: gate.verify_identity(DEVICE.ips[0])
        self.assertEqual(error.exception.code, 'whois_stable_id_mismatch')
        self.assertNotIn('PRIVATE', str(error.exception))


if __name__ == '__main__': unittest.main()

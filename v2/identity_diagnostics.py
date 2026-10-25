"""Strict whois rejection predicates; only fixed codes leave this module.

No asset access, persistence, identity normalization, or raw diagnostic output.
"""
import ipaddress

DIAGNOSTIC_PROTOCOL = 3

WHOIS_CODES = frozenset('''whois_document_type whois_node_missing whois_node_type
whois_profile_missing whois_profile_type
whois_stable_id_missing whois_stable_id_type whois_stable_id_mismatch
whois_node_user_missing whois_node_user_type whois_node_user_mismatch
whois_profile_id_missing whois_profile_id_type whois_profile_id_mismatch
whois_login_missing whois_login_type whois_login_mismatch
whois_machine_authorized_missing whois_machine_authorized_type whois_machine_unauthorized
whois_tags_type whois_tagged_node whois_sharer_type whois_shared_node
whois_expired_type whois_key_expired_flag
whois_key_expiry_missing_unverified_version whois_key_expiry_null
whois_key_expiry_type whois_key_expiry_invalid_or_expired
whois_addresses_missing whois_addresses_type whois_address_entry_type
whois_addresses_invalid whois_address_mismatch whois_source_ip_invalid'''.split())


def identity_failures(document, device, ip, version, not_expired):
    """Collect every rejection without returning any document value.

    This retains or strengthens the original strict identity predicates.
    The only absent-field compatibility rule is the audited 1.104.2
    KeyExpiry omitzero encoding; explicit null never means non-expiring.
    """
    if not isinstance(document, dict):
        return ('whois_document_type',)
    failures = []
    node = document.get('Node')
    profile = document.get('UserProfile')
    if 'Node' not in document:
        failures.append('whois_node_missing')
    elif not isinstance(node, dict):
        failures.append('whois_node_type')
    if 'UserProfile' not in document:
        failures.append('whois_profile_missing')
    elif not isinstance(profile, dict):
        failures.append('whois_profile_type')

    def exact(obj, key, kind, expected, prefix):
        if key not in obj:
            failures.append(prefix + '_missing')
        elif type(obj[key]) is not kind:
            failures.append(prefix + '_type')
        elif obj[key] != expected:
            failures.append(prefix + '_mismatch')

    if isinstance(node, dict):
        exact(node, 'StableID', str, device.node_id, 'whois_stable_id')
        exact(node, 'User', int, device.user_id, 'whois_node_user')
        if 'MachineAuthorized' not in node:
            failures.append('whois_machine_authorized_missing')
        elif type(node['MachineAuthorized']) is not bool:
            failures.append('whois_machine_authorized_type')
        elif node['MachineAuthorized'] is not True:
            failures.append('whois_machine_unauthorized')

        if 'Tags' in node:
            if not isinstance(node['Tags'], list) or any(type(value) is not str for value in node['Tags']):
                failures.append('whois_tags_type')
            elif node['Tags']:
                failures.append('whois_tagged_node')
        if 'Sharer' in node:
            if type(node['Sharer']) is not int:
                failures.append('whois_sharer_type')
            elif node['Sharer'] != 0:
                failures.append('whois_shared_node')

        if 'Expired' in node:
            if type(node['Expired']) is not bool:
                failures.append('whois_expired_type')
            elif node['Expired'] is True:
                failures.append('whois_key_expired_flag')
        if 'KeyExpiry' not in node:
            if version != (1, 104, 2):
                failures.append('whois_key_expiry_missing_unverified_version')
        elif node['KeyExpiry'] is None:
            failures.append('whois_key_expiry_null')
        elif type(node['KeyExpiry']) is not str:
            failures.append('whois_key_expiry_type')
        else:
            try:
                valid_expiry = not_expired(node['KeyExpiry']) is True
            except (ValueError, TypeError, AttributeError, OverflowError):
                valid_expiry = False
            if not valid_expiry:
                failures.append('whois_key_expiry_invalid_or_expired')

        if 'Addresses' not in node:
            failures.append('whois_addresses_missing')
        elif not isinstance(node['Addresses'], list):
            failures.append('whois_addresses_type')
        else:
            try:
                requested = ipaddress.ip_address(ip)
            except ValueError:
                requested = None
                failures.append('whois_source_ip_invalid')
            matched = False
            for address in node['Addresses']:
                if type(address) is not str:
                    failures.append('whois_address_entry_type')
                    continue
                try:
                    network = ipaddress.ip_network(address, strict=False)
                except ValueError:
                    failures.append('whois_addresses_invalid')
                    continue
                if requested is not None and network.prefixlen == network.max_prefixlen and requested in network:
                    matched = True
            if not matched:
                failures.append('whois_address_mismatch')

    if isinstance(profile, dict):
        exact(profile, 'ID', int, device.user_id, 'whois_profile_id')
        exact(profile, 'LoginName', str, device.login, 'whois_login')
    return tuple(dict.fromkeys(failures))

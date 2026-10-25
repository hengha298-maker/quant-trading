"""Strict whois rejection predicates; only fixed codes leave this module.

No asset access, persistence, identity normalization, or raw diagnostic output.
"""
import ipaddress
import re

DIAGNOSTIC_PROTOCOL = 4

WHOIS_CODES = frozenset('''whois_document_type whois_node_missing whois_node_type
whois_profile_missing whois_profile_type
whois_stable_id_missing whois_stable_id_type whois_stable_id_mismatch
whois_node_user_missing whois_node_user_type whois_node_user_mismatch
whois_profile_id_missing whois_profile_id_type whois_profile_id_mismatch
whois_login_missing whois_login_type whois_login_mismatch
whois_machine_authorized_missing whois_machine_authorized_type whois_machine_unauthorized
whois_machine_authorized_missing_unverified_version whois_authorization_requires_serve
whois_is_jailed_type whois_jailed_node whois_peer_api_only_type whois_peer_api_only
peer_auth_status_invalid peer_auth_not_running peer_auth_self_invalid
peer_auth_owner_mismatch peer_auth_peer_missing peer_auth_peer_ambiguous
peer_auth_not_in_netmap peer_auth_offline peer_auth_not_ios
peer_auth_identity_mismatch peer_auth_user_mismatch peer_auth_login_mismatch
peer_auth_key_invalid peer_auth_key_mismatch peer_auth_addresses_invalid
peer_auth_addresses_changed peer_auth_tagged_or_shared peer_auth_expired
whois_tags_type whois_tagged_node whois_sharer_type whois_shared_node
whois_expired_type whois_key_expired_flag
whois_key_expiry_missing_unverified_version whois_key_expiry_null
whois_key_expiry_type whois_key_expiry_invalid_or_expired
whois_addresses_missing whois_addresses_type whois_address_entry_type
whois_addresses_invalid whois_address_mismatch whois_source_ip_invalid'''.split())


def identity_failures(document, device, ip, version, not_expired):
    """Collect every rejection without returning any document value.

    Missing MachineAuthorized remains a failure here. Only the transport can
    combine fresh control-plane key admission with verified Serve ingress.
    The audited 1.104.2 KeyExpiry omitzero rule never treats null as zero.
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

        for key, type_code, denied_code in (
            ('IsJailed', 'whois_is_jailed_type', 'whois_jailed_node'),
            ('UnsignedPeerAPIOnly', 'whois_peer_api_only_type', 'whois_peer_api_only'),
        ):
            if key in node:
                if type(node[key]) is not bool:
                    failures.append(type_code)
                elif node[key]:
                    failures.append(denied_code)

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


def peer_authorization_failures(status, document, device, not_expired):
    """Corroborate a legacy peer against a FRESH trusted local status result.

    InNetworkMap alone is not approval. Require the nonzero node public key
    distributed by control in THREE places, plus all selected-device identity
    and eligibility predicates. This proves current key admission, not an
    admin API authorized flag or permission for a particular TCP port. The
    transport must additionally require actual authenticated Serve ingress.
    No identities, keys, addresses or exception values leave this function.
    """
    if not isinstance(status, dict) or not isinstance(document, dict):
        return ('peer_auth_status_invalid',)
    node = document.get('Node')
    if not isinstance(node, dict):
        return ('peer_auth_status_invalid',)
    failures = []
    if status.get('BackendState') != 'Running':
        failures.append('peer_auth_not_running')
    own = status.get('Self')
    if (not isinstance(own, dict) or own.get('Online') is not True or
            own.get('InNetworkMap') is not True):
        failures.append('peer_auth_self_invalid')
    elif type(own.get('UserID')) is not int or own['UserID'] != device.user_id:
        failures.append('peer_auth_owner_mismatch')
    if isinstance(own, dict):
        own_expiry = own.get('KeyExpiry', '0001-01-01T00:00:00Z')
        try:
            valid_own_expiry = type(own_expiry) is str and not_expired(own_expiry) is True
        except (ValueError, TypeError, AttributeError, OverflowError):
            valid_own_expiry = False
        if own.get('Expired', False) is not False or not valid_own_expiry:
            failures.append('peer_auth_self_invalid')
    users = status.get('User')
    profile = users.get(str(device.user_id)) if isinstance(users, dict) else None
    if (not isinstance(profile, dict) or type(profile.get('ID')) is not int or
            profile['ID'] != device.user_id or profile.get('LoginName') != device.login):
        failures.append('peer_auth_login_mismatch')
    peers = status.get('Peer')
    if not isinstance(peers, dict) or any(not isinstance(p, dict) for p in peers.values()):
        return tuple(dict.fromkeys(failures + ['peer_auth_status_invalid']))
    matching = [(key, p) for key, p in peers.items() if p.get('ID') == device.node_id]
    if not matching:
        return tuple(dict.fromkeys(failures + ['peer_auth_peer_missing']))
    if len(matching) != 1:
        return tuple(dict.fromkeys(failures + ['peer_auth_peer_ambiguous']))
    map_key, peer = matching[0]
    if peer.get('ID') != node.get('StableID'):
        failures.append('peer_auth_identity_mismatch')
    if (type(peer.get('UserID')) is not int or peer['UserID'] != device.user_id or
            peer['UserID'] != node.get('User')):
        failures.append('peer_auth_user_mismatch')
    if peer.get('InNetworkMap') is not True:
        failures.append('peer_auth_not_in_netmap')
    if peer.get('Online') is not True:
        failures.append('peer_auth_offline')
    if peer.get('OS') != 'iOS':
        failures.append('peer_auth_not_ios')
    keys = (map_key, peer.get('PublicKey'), node.get('Key'))
    if any(type(key) is not str or not re.fullmatch(r'nodekey:[0-9a-f]{64}', key)
           or key == 'nodekey:' + '0' * 64 for key in keys):
        failures.append('peer_auth_key_invalid')
    elif len(set(keys)) != 1:
        failures.append('peer_auth_key_mismatch')
    addresses = peer.get('TailscaleIPs')
    try:
        if not isinstance(addresses, list) or not addresses or any(type(a) is not str for a in addresses):
            raise ValueError()
        addresses = [str(ipaddress.ip_address(a)) for a in addresses]
        node_addresses = node.get('Addresses')
        if not isinstance(node_addresses, list) or any(type(a) is not str for a in node_addresses):
            raise ValueError()
        networks = [ipaddress.ip_network(a, strict=True) for a in node_addresses]
        if any(n.prefixlen != n.max_prefixlen for n in networks) or len(set(addresses)) != len(addresses):
            raise ValueError()
        if (set(addresses) != set(device.ips) or
                {str(n.network_address) for n in networks} != set(device.ips)):
            failures.append('peer_auth_addresses_changed')
    except (ValueError, TypeError):
        failures.append('peer_auth_addresses_invalid')
    tags = peer.get('Tags', [])
    sharer = peer.get('AltSharerUserID', 0)
    if (not isinstance(tags, list) or tags or type(sharer) is not int or sharer != 0 or
            peer.get('ShareeNode', False) is not False):
        failures.append('peer_auth_tagged_or_shared')
    if peer.get('Expired', False) is not False:
        failures.append('peer_auth_expired')
    if 'KeyExpiry' in peer:
        value = peer['KeyExpiry']
        try:
            valid_expiry = type(value) is str and not_expired(value) is True
        except (ValueError, TypeError, AttributeError, OverflowError):
            valid_expiry = False
        if not valid_expiry:
            failures.append('peer_auth_expired')
    return tuple(dict.fromkeys(failures))

#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_zone
short_description: Manage Technitium DNS Server zones
description:
  - Creates, converts, enables, disables and deletes authoritative zones.
  - Also manages per-zone options such as zone transfer policy, notify policy,
    TSIG keys and catalog zone membership.
options:
  zone:
    description:
      - The zone name. A bare IP address or CIDR network creates the matching
        reverse zone.
    type: str
    required: true
    aliases: [name]
  state:
    description: Whether the zone should exist.
    type: str
    choices: [present, absent]
    default: present
  type:
    description:
      - Zone type. An existing zone of a different type is converted when
        I(convert=true), otherwise the module fails rather than silently
        replacing the zone.
    type: str
    choices: [Primary, Secondary, Stub, Forwarder, SecondaryForwarder, Catalog, SecondaryCatalog]
    default: Primary
  convert:
    description: Allow converting an existing zone to I(type).
    type: bool
    default: false
  enabled:
    description: Whether the zone is enabled.
    type: bool
    default: true
  catalog:
    description:
      - Name of the catalog zone this zone should be a member of.
      - In a cluster, making zones members of the cluster catalog zone
        (C(cluster-catalog.<cluster domain>)) provisions them on every node.
    type: str
  primary_name_server_addresses:
    description:
      - Addresses of the primary name server, for C(Secondary), C(Stub),
        C(SecondaryForwarder) and C(SecondaryCatalog) zones.
    type: list
    elements: str
  zone_transfer_protocol:
    description: Transport used by secondary zones to transfer from the primary.
    type: str
    choices: [Tcp, Tls, Quic]
  tsig_key_name:
    description: TSIG key name used by secondary zones for the transfer.
    type: str
  use_soa_serial_date_scheme:
    description: Use the date based SOA serial scheme for new zones.
    type: bool
  validate_zone:
    description: Enable ZONEMD validation after each transfer, C(Secondary) zones only.
    type: bool
  forwarder:
    description:
      - Forwarder address for a C(Forwarder) zone. The special value
        C(this-server) forwards internally to this DNS server.
    type: str
  forwarder_protocol:
    description: Transport used by a C(Forwarder) zone.
    type: str
    choices: [Udp, Tcp, Tls, Https, Quic]
  initialize_forwarder:
    description: Create the initial FWD record when creating a C(Forwarder) zone.
    type: bool
  dnssec_validation:
    description: Perform DNSSEC validation for a C(Forwarder) zone.
    type: bool
  options:
    description:
      - Raw C(/api/zones/options/set) parameters, applied after the zone exists.
      - Use for anything not exposed above, for example C(zoneTransfer),
        C(zoneTransferNameServers), C(notify), C(notifyNameServers),
        C(update), C(queryAccess).
    type: dict
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Primary zone published to the whole cluster via the cluster catalog
  technitium_dns_zone:
    zone: internal.example.com
    type: Primary
    catalog: cluster-catalog.example.com
    options:
      zoneTransfer: AllowOnlyZoneNameServers
      notify: ZoneNameServers

- name: Reverse zone
  technitium_dns_zone:
    zone: 192.168.10.0/24
    type: Primary

- name: Conditional forwarder
  technitium_dns_zone:
    zone: corp.example.net
    type: Forwarder
    forwarder: 10.0.0.53
    forwarder_protocol: Udp

- name: Remove a zone
  technitium_dns_zone:
    zone: old.example.com
    state: absent
'''

RETURN = r'''
zone:
  description: The zone entry as reported by the server after the change.
  returned: success
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    TechnitiumError,
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    diff_dict,
    run_module,
)


def find_zone(client, name):
    """Look up a zone by name. ``zones/list`` returns every zone when unpaginated."""
    listing = client.get('/api/zones/list')
    wanted = name.rstrip('.').lower()
    for zone in listing.get('zones') or []:
        if (zone.get('name') or '').rstrip('.').lower() == wanted:
            return zone
    return None


def create_params(module):
    params = module.params
    return dict(
        zone=params['zone'],
        type=params['type'],
        catalog=params['catalog'],
        useSoaSerialDateScheme=params['use_soa_serial_date_scheme'],
        primaryNameServerAddresses=params['primary_name_server_addresses'],
        zoneTransferProtocol=params['zone_transfer_protocol'],
        tsigKeyName=params['tsig_key_name'],
        validateZone=params['validate_zone'],
        initializeForwarder=params['initialize_forwarder'],
        protocol=params['forwarder_protocol'],
        forwarder=params['forwarder'],
        dnssecValidation=params['dnssec_validation'],
    )


def apply_options(client, zone_name, check_mode):
    """Reconcile /api/zones/options/set against the current zone options."""
    desired = client.module.params.get('options') or {}
    catalog = client.module.params.get('catalog')
    if catalog is not None:
        desired = dict(desired)
        desired.setdefault('catalog', catalog)
    if not desired:
        return False, {}, {}

    current = client.get('/api/zones/options/get', params=dict(zone=zone_name))
    changes, before, after = diff_dict(current, desired)
    if not changes:
        return False, {}, {}
    if not check_mode:
        changes['zone'] = zone_name
        client.call('/api/zones/options/set', params=changes)
    return True, before, after


def run(client):
    module = client.module
    params = module.params
    requested = params['zone']

    existing = find_zone(client, requested)
    # For a zone created from a bare IP/CIDR, the server assigns the actual
    # reverse zone name (e.g. "10.10.10.0/24" -> "10.10.10.in-addr.arpa").
    # Every call below - enable/disable/options/convert/delete - needs that
    # real name, not the CIDR shorthand only zones/create accepts.
    effective_name = existing['name'] if existing else requested

    if params['state'] == 'absent':
        if not existing:
            return dict(changed=False, zone=None)
        if not module.check_mode:
            client.call('/api/zones/delete', params=dict(zone=effective_name))
        return dict(changed=True, zone=None,
                    diff=dict(before=existing, after=None))

    changed = False
    before = {}
    after = {}

    if not existing:
        # Best-effort in check mode: there is no create response to recover
        # a CIDR zone's real name from, so a not-yet-created reverse zone is
        # simply reported as would-be-created.
        created = module.check_mode
        if not module.check_mode:
            try:
                response = client.call('/api/zones/create', params=create_params(module))
                effective_name = response.get('domain', requested)
                created = True
            except TechnitiumError as exc:
                # A CIDR/IP zone that already exists under its computed reverse
                # name looks unmatched here (the pre-check only compares exact
                # names), and the server rejects the recreate. It also tells us
                # the real name in the error, so recover instead of failing -
                # this is what makes reverse zones from CIDR/IP idempotent.
                prefix = 'Zone already exists: '
                if not exc.msg.startswith(prefix):
                    raise
                effective_name = exc.msg[len(prefix):].strip()
                existing = find_zone(client, effective_name)
        if created:
            changed = True
            after['type'] = params['type']

    if existing and existing.get('type') != params['type']:
        if not params['convert']:
            raise TechnitiumError(
                'Zone %s exists as type %s but type %s was requested. Set convert=true '
                'to convert it, or remove the zone first.'
                % (effective_name, existing.get('type'), params['type'])
            )
        if not module.check_mode:
            client.call('/api/zones/convert',
                        params=dict(zone=effective_name, type=params['type']))
        changed = True
        before['type'] = existing.get('type')
        after['type'] = params['type']

    # Enable/disable is separate from creation: a freshly created zone is enabled.
    currently_disabled = bool(existing.get('disabled')) if existing else False
    if params['enabled'] == currently_disabled:
        if not module.check_mode:
            endpoint = '/api/zones/enable' if params['enabled'] else '/api/zones/disable'
            client.call(endpoint, params=dict(zone=effective_name))
        changed = True
        before['disabled'] = currently_disabled
        after['disabled'] = not params['enabled']

    if not module.check_mode or existing:
        options_changed, options_before, options_after = apply_options(
            client, effective_name, module.check_mode)
        if options_changed:
            changed = True
            before.update(options_before)
            after.update(options_after)

    zone = find_zone(client, effective_name) if not module.check_mode else existing
    return dict(changed=changed, zone=zone, diff=dict(before=before, after=after))


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        zone=dict(type='str', required=True, aliases=['name']),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
        type=dict(type='str', default='Primary', choices=[
            'Primary', 'Secondary', 'Stub', 'Forwarder',
            'SecondaryForwarder', 'Catalog', 'SecondaryCatalog',
        ]),
        convert=dict(type='bool', default=False),
        enabled=dict(type='bool', default=True),
        catalog=dict(type='str'),
        primary_name_server_addresses=dict(type='list', elements='str'),
        zone_transfer_protocol=dict(type='str', choices=['Tcp', 'Tls', 'Quic']),
        tsig_key_name=dict(type='str'),
        use_soa_serial_date_scheme=dict(type='bool'),
        validate_zone=dict(type='bool'),
        forwarder=dict(type='str'),
        forwarder_protocol=dict(type='str', choices=['Udp', 'Tcp', 'Tls', 'Https', 'Quic']),
        initialize_forwarder=dict(type='bool'),
        dnssec_validation=dict(type='bool'),
        options=dict(type='dict'),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        required_together=api_required_together(),
        required_one_of=api_required_one_of(),
        supports_check_mode=True,
    )
    run_module(module, run)


if __name__ == '__main__':
    main()

#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_dhcp_scope
short_description: Manage Technitium DNS Server DHCP scopes
description:
  - Creates, updates, enables, disables and deletes DHCP scopes, including
    exclusions, static routes and reserved leases.
  - Scope settings are compared against C(/api/dhcp/scopes/get) so reruns are green.
notes:
  - DHCP scopes are not replicated by clustering. Two nodes serving the same subnet
    need deliberately split ranges, or one node serving DHCP at a time.
options:
  name:
    description: Scope name.
    type: str
    required: true
  state:
    description: Whether the scope should exist.
    type: str
    choices: [present, absent]
    default: present
  enabled:
    description: Whether the scope is enabled.
    type: bool
    default: true
  starting_address:
    description: First address of the scope. Required when creating.
    type: str
  ending_address:
    description: Last address of the scope. Required when creating.
    type: str
  subnet_mask:
    description: Subnet mask of the scope. Required when creating.
    type: str
  router_address:
    description: Default gateway handed to clients (option 3).
    type: str
  dns_servers:
    description: DNS servers handed to clients (option 6). Ignored when I(use_this_dns_server=true).
    type: list
    elements: str
  use_this_dns_server:
    description: Hand out this DNS server's own address as the client DNS server.
    type: bool
  domain_name:
    description: Client domain name (option 15).
    type: str
  domain_search_list:
    description: Domain search suffixes (option 119).
    type: list
    elements: str
  dns_updates:
    description: Let the DHCP server maintain forward and reverse DNS records for clients.
    type: bool
  dns_ttl:
    description: TTL used for the DNS records the DHCP server creates.
    type: int
  lease_time_days:
    description: Lease time, days component.
    type: int
  lease_time_hours:
    description: Lease time, hours component.
    type: int
  lease_time_minutes:
    description: Lease time, minutes component.
    type: int
  ntp_servers:
    description: NTP servers handed to clients (option 42).
    type: list
    elements: str
  exclusions:
    description: Address ranges never handed out dynamically.
    type: list
    elements: dict
    suboptions:
      starting_address:
        description: First excluded address.
        type: str
        required: true
      ending_address:
        description: Last excluded address.
        type: str
        required: true
  static_routes:
    description: Classless static routes handed to clients (option 121).
    type: list
    elements: dict
    suboptions:
      destination:
        description: Destination network address.
        type: str
        required: true
      subnet_mask:
        description: Destination subnet mask.
        type: str
        required: true
      router:
        description: Gateway for the destination.
        type: str
        required: true
  reserved_leases:
    description: Addresses reserved for specific MAC addresses.
    type: list
    elements: dict
    suboptions:
      host_name:
        description: Host name recorded with the reservation.
        type: str
      hardware_address:
        description: Client MAC address.
        type: str
        required: true
      address:
        description: Reserved IP address.
        type: str
        required: true
      comments:
        description: Free text comment.
        type: str
  allow_only_reserved_leases:
    description: Serve only reserved leases, never dynamic ones.
    type: bool
  options:
    description:
      - Raw C(/api/dhcp/scopes/set) parameters for anything not exposed above,
        for example C(bootFileName), C(vendorInfo) or C(genericOptions).
    type: dict
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: LAN scope pointing clients at the DNS VIP
  technitium_dns_dhcp_scope:
    name: LAN
    starting_address: 192.168.10.100
    ending_address: 192.168.10.200
    subnet_mask: 255.255.255.0
    router_address: 192.168.10.1
    dns_servers:
      - 192.168.10.4
    domain_name: internal.example.com
    dns_updates: true
    lease_time_days: 1
    exclusions:
      - starting_address: 192.168.10.100
        ending_address: 192.168.10.109
    reserved_leases:
      - host_name: printer
        hardware_address: 00-11-22-33-44-55
        address: 192.168.10.120
'''

RETURN = r'''
scope:
  description: The scope configuration after the change.
  returned: success
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    TechnitiumError,
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    pipe_rows,
    run_module,
    values_equal,
)

SIMPLE_FIELDS = {
    'starting_address': 'startingAddress',
    'ending_address': 'endingAddress',
    'subnet_mask': 'subnetMask',
    'router_address': 'routerAddress',
    'dns_servers': 'dnsServers',
    'use_this_dns_server': 'useThisDnsServer',
    'domain_name': 'domainName',
    'domain_search_list': 'domainSearchList',
    'dns_updates': 'dnsUpdates',
    'dns_ttl': 'dnsTtl',
    'lease_time_days': 'leaseTimeDays',
    'lease_time_hours': 'leaseTimeHours',
    'lease_time_minutes': 'leaseTimeMinutes',
    'ntp_servers': 'ntpServers',
    'allow_only_reserved_leases': 'allowOnlyReservedLeases',
}

# Structured fields: (api name, row field order, comparison key order)
STRUCTURED_FIELDS = {
    'exclusions': ('exclusions',
                   ['startingAddress', 'endingAddress'],
                   {'starting_address': 'startingAddress',
                    'ending_address': 'endingAddress'}),
    'static_routes': ('staticRoutes',
                      ['destination', 'subnetMask', 'router'],
                      {'destination': 'destination',
                       'subnet_mask': 'subnetMask',
                       'router': 'router'}),
    'reserved_leases': ('reservedLeases',
                        ['hostName', 'hardwareAddress', 'address', 'comments'],
                        {'host_name': 'hostName',
                         'hardware_address': 'hardwareAddress',
                         'address': 'address',
                         'comments': 'comments'}),
}


def normalize_mac(value):
    """The API accepts 00-11-22 and 00:11:22 and returns one canonical form."""
    if value is None:
        return None
    return str(value).replace(':', '-').replace('.', '-').upper()


def build_structured(param_name, rows):
    """Convert the friendly suboption rows into API rows."""
    api_name, order, mapping = STRUCTURED_FIELDS[param_name]
    converted = []
    for row in rows:
        api_row = {}
        for source, target in mapping.items():
            value = row.get(source)
            if target == 'hardwareAddress':
                value = normalize_mac(value)
            api_row[target] = value
        converted.append(api_row)
    return api_name, order, converted


def structured_equal(current_rows, desired_rows, order):
    def key(row):
        return tuple(
            normalize_mac(row.get(field)) if field == 'hardwareAddress'
            else ('' if row.get(field) is None else str(row.get(field)))
            for field in order
        )

    return sorted(key(row) for row in (current_rows or [])) == \
        sorted(key(row) for row in (desired_rows or []))


def find_scope(client, name):
    listing = client.get('/api/dhcp/scopes/list')
    for scope in listing.get('scopes') or []:
        if scope.get('name') == name:
            return scope
    return None


def run(client):
    module = client.module
    params = module.params
    name = params['name']

    summary = find_scope(client, name)

    if params['state'] == 'absent':
        if not summary:
            return dict(changed=False, scope=None)
        if not module.check_mode:
            client.call('/api/dhcp/scopes/delete', params=dict(name=name))
        return dict(changed=True, scope=None, diff=dict(before=summary, after=None))

    current = {}
    if summary:
        current = client.get('/api/dhcp/scopes/get', params=dict(name=name))
    else:
        missing = [field for field in ('starting_address', 'ending_address', 'subnet_mask')
                   if not params[field]]
        if missing:
            raise TechnitiumError(
                'Creating DHCP scope %s requires %s' % (name, ', '.join(missing)))

    changes = {}
    before = {}
    after = {}

    for param_name, api_name in SIMPLE_FIELDS.items():
        value = params[param_name]
        if value is None:
            continue
        if not summary or not values_equal(current.get(api_name), value):
            changes[api_name] = value
            before[api_name] = current.get(api_name)
            after[api_name] = value

    for param_name in STRUCTURED_FIELDS:
        rows = params[param_name]
        if rows is None:
            continue
        api_name, order, converted = build_structured(param_name, rows)
        if not structured_equal(current.get(api_name), converted, order):
            changes[api_name] = pipe_rows(converted, order)
            before[api_name] = current.get(api_name)
            after[api_name] = converted

    for api_name, value in (params['options'] or {}).items():
        if not values_equal(current.get(api_name), value):
            changes[api_name] = value
            before[api_name] = current.get(api_name)
            after[api_name] = value

    changed = bool(changes) or not summary

    if changed and not module.check_mode:
        changes['name'] = name
        client.call('/api/dhcp/scopes/set', params=changes)

    # Scope enable/disable is a separate call and a new scope starts disabled.
    currently_enabled = bool(summary.get('enabled')) if summary else False
    if params['enabled'] != currently_enabled:
        changed = True
        before['enabled'] = currently_enabled
        after['enabled'] = params['enabled']
        if not module.check_mode:
            endpoint = '/api/dhcp/scopes/enable' if params['enabled'] \
                else '/api/dhcp/scopes/disable'
            client.call(endpoint, params=dict(name=name))

    scope = current
    if changed and not module.check_mode:
        scope = client.get('/api/dhcp/scopes/get', params=dict(name=name))

    return dict(changed=changed, scope=scope, diff=dict(before=before, after=after))


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
        enabled=dict(type='bool', default=True),
        starting_address=dict(type='str'),
        ending_address=dict(type='str'),
        subnet_mask=dict(type='str'),
        router_address=dict(type='str'),
        dns_servers=dict(type='list', elements='str'),
        use_this_dns_server=dict(type='bool'),
        domain_name=dict(type='str'),
        domain_search_list=dict(type='list', elements='str'),
        dns_updates=dict(type='bool'),
        dns_ttl=dict(type='int'),
        lease_time_days=dict(type='int'),
        lease_time_hours=dict(type='int'),
        lease_time_minutes=dict(type='int'),
        ntp_servers=dict(type='list', elements='str'),
        allow_only_reserved_leases=dict(type='bool'),
        exclusions=dict(type='list', elements='dict', options=dict(
            starting_address=dict(type='str', required=True),
            ending_address=dict(type='str', required=True),
        )),
        static_routes=dict(type='list', elements='dict', options=dict(
            destination=dict(type='str', required=True),
            subnet_mask=dict(type='str', required=True),
            router=dict(type='str', required=True),
        )),
        reserved_leases=dict(type='list', elements='dict', options=dict(
            host_name=dict(type='str'),
            hardware_address=dict(type='str', required=True),
            address=dict(type='str', required=True),
            comments=dict(type='str'),
        )),
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

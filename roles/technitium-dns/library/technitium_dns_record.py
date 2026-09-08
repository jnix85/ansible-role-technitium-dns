#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_record
short_description: Manage resource records in a Technitium DNS Server zone
description:
  - Adds, updates and removes a single resource record, identified by its domain
    name, type and record data.
  - Record data is given as I(data) using the raw API field names
    (C(ipAddress), C(cname), C(exchange), C(preference), C(text), C(nameServer),
    C(target), ...), so every record type the server supports is reachable.
  - The record is matched on its data, so changing the TTL updates in place while
    changing the data adds a new record to the record set. Use I(exclusive) when
    the record set should hold exactly this one record.
options:
  domain:
    description: The fully qualified record name.
    type: str
    required: true
    aliases: [name]
  zone:
    description:
      - The zone the record belongs to. Defaults to the closest authoritative zone.
    type: str
  type:
    description: The record type, for example C(A), C(AAAA), C(CNAME), C(MX), C(TXT), C(SRV), C(FWD), C(APP).
    type: str
    required: true
  state:
    description: Whether the record should exist.
    type: str
    choices: [present, absent]
    default: present
  data:
    description:
      - Record data using the API's field names for the record type.
      - Required when I(state=present).
    type: dict
  ttl:
    description: Record TTL in seconds. Defaults to the server's configured default.
    type: int
  exclusive:
    description:
      - Remove any other record of the same name and type, leaving only this one.
      - This is what makes a record set fully declarative.
    type: bool
    default: false
  disabled:
    description: Whether the record is disabled.
    type: bool
  comments:
    description: Free text comment stored with the record.
    type: str
  expiry_ttl:
    description: Delete the record automatically this many seconds after last modification.
    type: int
  update_ptr:
    description: Also maintain the reverse PTR record. C(A) and C(AAAA) records only.
    type: bool
  create_ptr_zone:
    description: Create the reverse zone if it does not exist, when I(update_ptr=true).
    type: bool
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Address record with reverse PTR
  technitium_dns_record:
    zone: internal.example.com
    domain: ns1.internal.example.com
    type: A
    ttl: 300
    data:
      ipAddress: 192.168.10.5
    update_ptr: true
    create_ptr_zone: true

- name: The only MX record for the zone
  technitium_dns_record:
    zone: example.com
    domain: example.com
    type: MX
    exclusive: true
    data:
      exchange: mail.example.com
      preference: 10

- name: SPF record
  technitium_dns_record:
    domain: example.com
    type: TXT
    data:
      text: "v=spf1 -all"

- name: Remove a record
  technitium_dns_record:
    domain: old.example.com
    type: A
    state: absent
    data:
      ipAddress: 192.168.10.99
'''

RETURN = r'''
records:
  description: The record set for this domain and type after the change.
  returned: success
  type: list
  elements: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    run_module,
    values_equal,
)

def fetch_records(client, domain, zone, record_type):
    params = dict(domain=domain, listZone=False)
    if zone:
        params['zone'] = zone
    response = client.get('/api/zones/records/get', params=params)
    wanted_name = domain.rstrip('.').lower()
    records = []
    for record in response.get('records') or []:
        if (record.get('name') or '').rstrip('.').lower() != wanted_name:
            continue
        if (record.get('type') or '').upper() != record_type.upper():
            continue
        records.append(record)
    return records


def rdata_matches(record, desired_data):
    """True when every declared data field matches the record's rData."""
    rdata = record.get('rData') or {}
    for key, value in desired_data.items():
        if value is None:
            continue
        if not values_equal(rdata.get(key), value):
            return False
    return True


def record_needs_update(module, record):
    """Compare the mutable, non-identifying attributes of a matched record."""
    changes = {}
    params = module.params
    if params['ttl'] is not None and not values_equal(record.get('ttl'), params['ttl']):
        changes['ttl'] = params['ttl']
    if params['disabled'] is not None and bool(record.get('disabled')) != params['disabled']:
        changes['disable'] = params['disabled']
    if params['comments'] is not None and not values_equal(
            record.get('comments'), params['comments']):
        changes['comments'] = params['comments']
    if params['expiry_ttl'] is not None and not values_equal(
            record.get('expiryTtl'), params['expiry_ttl']):
        changes['expiryTtl'] = params['expiry_ttl']
    return changes


def base_params(module):
    params = dict(domain=module.params['domain'], type=module.params['type'])
    if module.params['zone']:
        params['zone'] = module.params['zone']
    return params


def add_record(client, overwrite=False):
    module = client.module
    params = base_params(module)
    params.update(module.params['data'])
    params.update(
        ttl=module.params['ttl'],
        comments=module.params['comments'],
        expiryTtl=module.params['expiry_ttl'],
        ptr=module.params['update_ptr'],
        createPtrZone=module.params['create_ptr_zone'],
        overwrite=overwrite or None,
    )
    client.call('/api/zones/records/add', params=params)


def update_record(client, changes):
    module = client.module
    params = base_params(module)
    # The update call identifies the record by its current data, and applies the
    # changed attributes alongside. Data is unchanged here, so no new* fields.
    params.update(module.params['data'])
    params.update(changes)
    if module.params['update_ptr'] is not None:
        params['ptr'] = module.params['update_ptr']
    client.call('/api/zones/records/update', params=params)


def delete_record(client, record):
    module = client.module
    params = base_params(module)
    rdata = record.get('rData') or {}
    for key, value in rdata.items():
        params.setdefault(key, value)
    client.call('/api/zones/records/delete', params=params)


def run(client):
    module = client.module
    params = module.params
    records = fetch_records(client, params['domain'], params['zone'], params['type'])
    matched = [record for record in records if rdata_matches(record, params['data'] or {})]

    changed = False
    before = list(records)

    if params['state'] == 'absent':
        for record in matched:
            changed = True
            if not module.check_mode:
                delete_record(client, record)
    else:
        if not matched:
            changed = True
            if not module.check_mode:
                add_record(client, overwrite=params['exclusive'])
        else:
            changes = record_needs_update(module, matched[0])
            if changes:
                changed = True
                if not module.check_mode:
                    update_record(client, changes)

        if params['exclusive'] and matched:
            # When nothing matched, the add above used overwrite=true and already
            # replaced the whole record set. Otherwise prune everything else.
            keep = matched[0]
            for record in records:
                if record is keep:
                    continue
                changed = True
                if not module.check_mode:
                    delete_record(client, record)

    after = before
    if changed and not module.check_mode:
        after = fetch_records(client, params['domain'], params['zone'], params['type'])

    # diff.before/after must be dicts (or strings) for Ansible's --diff
    # renderer; a bare list makes it fail with "'list' object has no
    # attribute 'splitlines'" instead of showing anything.
    return dict(changed=changed, records=after,
                diff=dict(before={'records': before}, after={'records': after}))


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        domain=dict(type='str', required=True, aliases=['name']),
        zone=dict(type='str'),
        type=dict(type='str', required=True),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
        data=dict(type='dict'),
        ttl=dict(type='int'),
        exclusive=dict(type='bool', default=False),
        disabled=dict(type='bool'),
        comments=dict(type='str'),
        expiry_ttl=dict(type='int'),
        update_ptr=dict(type='bool'),
        create_ptr_zone=dict(type='bool'),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        required_together=api_required_together(),
        required_one_of=api_required_one_of(),
        required_if=[('state', 'present', ('data',))],
        supports_check_mode=True,
    )
    run_module(module, run)


if __name__ == '__main__':
    main()

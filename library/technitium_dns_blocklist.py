#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_blocklist
short_description: Manage the Technitium DNS Server allowed and blocked zones
description:
  - Adds and removes domains from the server's Allowed or Blocked zone.
  - With I(exclusive=true) the listed domains become the complete contents of the
    zone, which makes the list fully declarative.
notes:
  - This manages the manually maintained Allowed/Blocked zones. The automatically
    downloaded block lists are configured with the C(blockListUrls) setting via
    M(technitium_dns_settings).
options:
  list:
    description: Which list to manage.
    type: str
    choices: [allowed, blocked]
    required: true
  domains:
    description: The domains to add or remove.
    type: list
    elements: str
    required: true
  state:
    description: Whether the domains should be in the list.
    type: str
    choices: [present, absent]
    default: present
  exclusive:
    description:
      - Remove any domain in the list that is not in I(domains).
      - Only meaningful with I(state=present).
    type: bool
    default: false
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Never block these, whatever the block lists say
  technitium_dns_blocklist:
    list: allowed
    domains:
      - updates.example.com
      - telemetry.internal.example.com

- name: The blocked zone contains exactly these domains
  technitium_dns_blocklist:
    list: blocked
    exclusive: true
    domains:
      - ads.example.net
'''

RETURN = r'''
domains:
  description: The domains added or removed.
  returned: success
  type: list
  elements: str
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    run_module,
)


def list_domains(client, which):
    """Return every domain in the list.

    ``/api/<list>/list`` browses the zone tree one label at a time, so the export
    endpoint is the only reliable way to read the complete set. It returns a plain
    text file with one domain per line.
    """
    text = client.download_text('/api/%s/export' % which)
    return set(
        line.strip().rstrip('.').lower()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith('#')
    )


def run(client):
    module = client.module
    params = module.params
    which = params['list']
    desired = set(domain.rstrip('.').lower() for domain in params['domains'])

    existing = list_domains(client, which)

    to_add = set()
    to_remove = set()

    if params['state'] == 'present':
        to_add = desired - existing
        if params['exclusive']:
            to_remove = existing - desired
    else:
        to_remove = desired & existing

    changed = bool(to_add or to_remove)

    if changed and not module.check_mode:
        if to_add:
            # The import call takes the whole batch in one request.
            field = 'allowedZones' if which == 'allowed' else 'blockedZones'
            client.call('/api/%s/import' % which,
                        params={field: sorted(to_add)})
        for domain in sorted(to_remove):
            client.call('/api/%s/delete' % which, params=dict(domain=domain))

    return dict(
        changed=changed,
        domains=sorted(to_add | to_remove),
        added=sorted(to_add),
        removed=sorted(to_remove),
        diff=dict(before=sorted(existing),
                  after=sorted((existing | to_add) - to_remove)),
    )


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        list=dict(type='str', choices=['allowed', 'blocked'], required=True),
        domains=dict(type='list', elements='str', required=True),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
        exclusive=dict(type='bool', default=False),
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

#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_info
short_description: Gather facts from a Technitium DNS Server
description:
  - Read-only. Returns server settings, zones, cluster state, DHCP scopes, apps,
    users, groups and permission sections.
  - Useful for asserting the state of a cluster, and for discovering the exact
    setting names to feed back into M(technitium_dns_settings).
options:
  gather:
    description: Which sets of facts to return.
    type: list
    elements: str
    choices: [status, settings, zones, cluster, dhcp, apps, users, groups, permissions, all]
    default: [status, settings, cluster]
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Check that every cluster node is connected
  technitium_dns_info:
    gather: [cluster]
  register: technitium

- name: Fail if a node is not connected
  ansible.builtin.assert:
    that:
      - technitium.cluster.nodes
        | rejectattr('state', 'in', ['Self', 'Connected'])
        | list | length == 0
    fail_msg: >-
      Cluster nodes not connected:
      {{ technitium.cluster.nodes | rejectattr('state', 'in', ['Self', 'Connected'])
         | map(attribute='name') | join(', ') }}
'''

RETURN = r'''
status:
  description: Unauthenticated server status, including hasDefaultCredentials.
  returned: when requested
  type: dict
settings:
  description: Full DNS server settings.
  returned: when requested
  type: dict
zones:
  description: All zones.
  returned: when requested
  type: list
  elements: dict
cluster:
  description: Cluster state.
  returned: when requested
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    run_module,
)

GATHERERS = {
    'settings': lambda c: c.get('/api/settings/get'),
    'zones': lambda c: c.get('/api/zones/list').get('zones', []),
    'cluster': lambda c: c.call('/api/admin/cluster/state',
                                params=dict(includeServerIpAddresses=True), method='GET'),
    'dhcp': lambda c: c.get('/api/dhcp/scopes/list').get('scopes', []),
    'apps': lambda c: c.get('/api/apps/list').get('apps', []),
    'users': lambda c: c.get('/api/admin/users/list').get('users', []),
    'groups': lambda c: c.get('/api/admin/groups/list').get('groups', []),
    'permissions': lambda c: c.get('/api/admin/permissions/list').get('permissions', []),
}


def run(client):
    requested = client.module.params['gather']
    if 'all' in requested:
        requested = ['status'] + sorted(GATHERERS)

    result = dict(changed=False)
    for name in requested:
        if name == 'status':
            result['status'] = client.status()
        else:
            result[name] = GATHERERS[name](client)
    return result


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        gather=dict(
            type='list', elements='str',
            default=['status', 'settings', 'cluster'],
            choices=['status', 'settings', 'zones', 'cluster', 'dhcp', 'apps',
                     'users', 'groups', 'permissions', 'all'],
        ),
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

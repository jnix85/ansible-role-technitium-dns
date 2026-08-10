#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_group
short_description: Manage Technitium DNS Server user groups
description:
  - Creates, updates and deletes groups and their membership.
options:
  name:
    description: The group name.
    type: str
    required: true
    aliases: [group]
  state:
    description: Whether the group should exist.
    type: str
    choices: [present, absent]
    default: present
  description:
    description: Group description.
    type: str
  members:
    description: Exact list of usernames that should be members of the group.
    type: list
    elements: str
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Group for zone operators
  technitium_dns_group:
    name: Zone Operators
    description: May edit zones but not server settings
    members:
      - operator
'''

RETURN = r'''
group:
  description: The group after the change.
  returned: success
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    TechnitiumError,
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    run_module,
    values_equal,
)


def get_group(client, name):
    try:
        return client.get('/api/admin/groups/get',
                          params=dict(group=name, includeUsers=True))
    except TechnitiumError:
        return None


def run(client):
    module = client.module
    params = module.params
    name = params['name']

    current = get_group(client, name)

    if params['state'] == 'absent':
        if not current:
            return dict(changed=False, group=None)
        if not module.check_mode:
            client.call('/api/admin/groups/delete', params=dict(group=name))
        return dict(changed=True, group=None, diff=dict(before=current, after=None))

    changed = False
    before = {}
    after = {}

    if not current:
        changed = True
        after['name'] = name
        if not module.check_mode:
            client.call('/api/admin/groups/create', params=dict(
                group=name, description=params['description']))
            current = get_group(client, name)

    current = current or {}
    updates = {}

    if params['description'] is not None and not values_equal(
            current.get('description'), params['description']):
        updates['description'] = params['description']
        before['description'] = current.get('description')
        after['description'] = params['description']

    if params['members'] is not None:
        current_members = sorted(current.get('members') or [])
        desired_members = sorted(params['members'])
        if current_members != desired_members:
            updates['members'] = params['members']
            before['members'] = current_members
            after['members'] = desired_members

    if updates:
        changed = True
        if not module.check_mode:
            updates['group'] = name
            current = client.call('/api/admin/groups/set', params=updates)

    return dict(changed=changed, group=current, diff=dict(before=before, after=after))


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True, aliases=['group']),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
        description=dict(type='str'),
        members=dict(type='list', elements='str'),
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

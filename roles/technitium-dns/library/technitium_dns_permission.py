#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_permission
short_description: Manage Technitium DNS Server section and zone permissions
description:
  - Sets the view/modify/delete permissions granted to users and groups, either for
    a console section (Dashboard, Zones, Settings, Administration, ...) or for an
    individual zone.
  - The permission tables are replaced wholesale by the API, so the lists given here
    are the complete desired state for whichever of I(users)/I(groups) you specify.
options:
  section:
    description:
      - The console section to manage. Mutually exclusive with I(zone).
      - Use M(technitium_dns_info) with C(gather=permissions) to list valid sections.
    type: str
  zone:
    description: The zone whose permissions to manage. Mutually exclusive with I(section).
    type: str
  users:
    description: Per-user permissions. Omit to leave user permissions untouched.
    type: list
    elements: dict
    suboptions:
      name:
        description: Username.
        type: str
        required: true
      view:
        description: Grant view permission.
        type: bool
        default: false
      modify:
        description: Grant modify permission.
        type: bool
        default: false
      delete:
        description: Grant delete permission.
        type: bool
        default: false
  groups:
    description: Per-group permissions. Omit to leave group permissions untouched.
    type: list
    elements: dict
    suboptions:
      name:
        description: Group name.
        type: str
        required: true
      view:
        description: Grant view permission.
        type: bool
        default: false
      modify:
        description: Grant modify permission.
        type: bool
        default: false
      delete:
        description: Grant delete permission.
        type: bool
        default: false
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Let zone operators modify zones but not delete them
  technitium_dns_permission:
    section: Zones
    groups:
      - name: Administrators
        view: true
        modify: true
        delete: true
      - name: Zone Operators
        view: true
        modify: true

- name: Delegate a single zone
  technitium_dns_permission:
    zone: lab.example.com
    groups:
      - name: Lab Team
        view: true
        modify: true
'''

RETURN = r'''
permissions:
  description: The permission table after the change.
  returned: success
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    run_module,
)


def desired_table(rows):
    return dict(
        (row['name'], (bool(row['view']), bool(row['modify']), bool(row['delete'])))
        for row in rows
    )


def current_table(rows, name_key):
    return dict(
        (row.get(name_key), (bool(row.get('canView')), bool(row.get('canModify')),
                             bool(row.get('canDelete'))))
        for row in rows or []
    )


def encode_table(rows):
    cells = []
    for row in rows:
        cells.extend([
            row['name'],
            'true' if row['view'] else 'false',
            'true' if row['modify'] else 'false',
            'true' if row['delete'] else 'false',
        ])
    return '|'.join(cells)


def run(client):
    module = client.module
    params = module.params

    if params['zone']:
        get_path = '/api/zones/permissions/get'
        set_path = '/api/zones/permissions/set'
        selector = dict(zone=params['zone'], includeUsersAndGroups=False)
        set_selector = dict(zone=params['zone'])
    else:
        get_path = '/api/admin/permissions/get'
        set_path = '/api/admin/permissions/set'
        selector = dict(section=params['section'], includeUsersAndGroups=False)
        set_selector = dict(section=params['section'])

    current = client.get(get_path, params=selector)

    changes = dict(set_selector)
    before = {}
    after = {}

    if params['users'] is not None:
        desired = desired_table(params['users'])
        existing = current_table(current.get('userPermissions'), 'username')
        if desired != existing:
            changes['userPermissions'] = encode_table(params['users'])
            before['userPermissions'] = existing
            after['userPermissions'] = desired

    if params['groups'] is not None:
        desired = desired_table(params['groups'])
        existing = current_table(current.get('groupPermissions'), 'name')
        if desired != existing:
            changes['groupPermissions'] = encode_table(params['groups'])
            before['groupPermissions'] = existing
            after['groupPermissions'] = desired

    changed = len(changes) > len(set_selector)
    if changed and not module.check_mode:
        current = client.call(set_path, params=changes)

    return dict(changed=changed, permissions=current,
                diff=dict(before=before, after=after))


def main():
    permission_row = dict(
        name=dict(type='str', required=True),
        view=dict(type='bool', default=False),
        modify=dict(type='bool', default=False),
        delete=dict(type='bool', default=False),
    )
    argument_spec = api_argument_spec()
    argument_spec.update(
        section=dict(type='str'),
        zone=dict(type='str'),
        users=dict(type='list', elements='dict', options=permission_row),
        groups=dict(type='list', elements='dict', options=permission_row),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        required_together=api_required_together(),
        required_one_of=api_required_one_of() + [['section', 'zone']],
        mutually_exclusive=[['section', 'zone']],
        supports_check_mode=True,
    )
    run_module(module, run)


if __name__ == '__main__':
    main()

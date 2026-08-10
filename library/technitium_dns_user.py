#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_user
short_description: Manage Technitium DNS Server user accounts
description:
  - Creates, updates and deletes local user accounts and their group membership.
notes:
  - Passwords cannot be read back, so I(password) is only used when creating the
    account unless I(update_password=always).
  - In a cluster, the Administration section is replicated from the primary node,
    so manage users on the primary only.
options:
  username:
    description: The account's username.
    type: str
    required: true
    aliases: [user]
  state:
    description: Whether the account should exist.
    type: str
    choices: [present, absent]
    default: present
  password:
    description: Password for the account. Required when creating.
    type: str
  update_password:
    description:
      - C(on_create) only sets the password when the account is created.
      - C(always) resets it on every run, which always reports changed.
    type: str
    choices: [on_create, always]
    default: on_create
  display_name:
    description: Display name shown in the web console.
    type: str
  disabled:
    description: Whether the account is disabled.
    type: bool
  session_timeout_seconds:
    description: Idle session timeout for the account.
    type: int
  groups:
    description: Exact list of groups the account should be a member of.
    type: list
    elements: str
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Read-only operator account
  technitium_dns_user:
    username: operator
    display_name: NOC Operator
    password: "{{ vault_operator_password }}"
    groups:
      - DNS Administrators
'''

RETURN = r'''
user:
  description: The account after the change.
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


def get_user(client, username):
    try:
        return client.get('/api/admin/users/get',
                          params=dict(user=username, includeGroups=True))
    except TechnitiumError:
        # The API returns an error rather than an empty result for unknown users.
        return None


def run(client):
    module = client.module
    params = module.params
    username = params['username']

    current = get_user(client, username)

    if params['state'] == 'absent':
        if not current:
            return dict(changed=False, user=None)
        if not module.check_mode:
            client.call('/api/admin/users/delete', params=dict(user=username))
        return dict(changed=True, user=None, diff=dict(before=current, after=None))

    changed = False
    before = {}
    after = {}

    if not current:
        if not params['password']:
            raise TechnitiumError('password is required to create user %s' % username)
        changed = True
        after['username'] = username
        if not module.check_mode:
            client.call('/api/admin/users/create', params=dict(
                user=username,
                pass_=params['password'],
                displayName=params['display_name'],
            ))
            current = get_user(client, username)

    updates = {}
    current = current or {}

    if params['display_name'] is not None and not values_equal(
            current.get('displayName'), params['display_name']):
        updates['displayName'] = params['display_name']
        before['displayName'] = current.get('displayName')
        after['displayName'] = params['display_name']

    if params['disabled'] is not None and bool(current.get('disabled')) != params['disabled']:
        updates['disabled'] = params['disabled']
        before['disabled'] = current.get('disabled')
        after['disabled'] = params['disabled']

    if params['session_timeout_seconds'] is not None and not values_equal(
            current.get('sessionTimeoutSeconds'), params['session_timeout_seconds']):
        updates['sessionTimeoutSeconds'] = params['session_timeout_seconds']
        before['sessionTimeoutSeconds'] = current.get('sessionTimeoutSeconds')
        after['sessionTimeoutSeconds'] = params['session_timeout_seconds']

    if params['groups'] is not None:
        current_groups = sorted(current.get('memberOfGroups') or [])
        desired_groups = sorted(params['groups'])
        if current_groups != desired_groups:
            updates['memberOfGroups'] = params['groups']
            before['memberOfGroups'] = current_groups
            after['memberOfGroups'] = desired_groups

    if params['password'] and params['update_password'] == 'always':
        updates['newPass'] = params['password']
        before['password'] = 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
        after['password'] = 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'

    if updates:
        changed = True
        if not module.check_mode:
            updates['user'] = username
            current = client.call('/api/admin/users/set', params=updates)

    return dict(changed=changed, user=current, diff=dict(before=before, after=after))


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        username=dict(type='str', required=True, aliases=['user']),
        state=dict(type='str', choices=['present', 'absent'], default='present'),
        password=dict(type='str', no_log=True),
        update_password=dict(type='str', choices=['on_create', 'always'], default='on_create'),
        display_name=dict(type='str'),
        disabled=dict(type='bool'),
        session_timeout_seconds=dict(type='int'),
        groups=dict(type='list', elements='str'),
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

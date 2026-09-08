#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_bootstrap
short_description: Take over a fresh Technitium DNS Server install
description:
  - A newly installed Technitium DNS Server accepts C(admin)/C(admin). This module
    detects that state via the unauthenticated C(/api/status) endpoint, sets the
    real administrator password, and hands back a token for the rest of the play.
  - Idempotent - once the default credentials are gone it only obtains a token.
options:
  admin_username:
    description: The administrator account to manage.
    type: str
    default: admin
  admin_password:
    description: The password the administrator account should have.
    type: str
    required: true
  default_password:
    description: The password a fresh install ships with.
    type: str
    default: admin
  create_api_token:
    description:
      - Create a named, non-expiring API token instead of returning a session token.
      - A session token expires on its own and leaves nothing behind, so it is the
        default. Use a named token when something outside Ansible needs API access.
    type: bool
    default: false
  api_token_name:
    description: Name for the token created when I(create_api_token=true).
    type: str
    default: ansible
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Set the admin password and get a token for this play
  technitium_dns_bootstrap:
    api_url: http://127.0.0.1:5380
    admin_password: "{{ vault_technitium_admin_password }}"
  register: technitium_bootstrap
  no_log: true

- name: Use the token
  technitium_dns_settings:
    api_token: "{{ technitium_bootstrap.token }}"
    settings:
      dnsServerDomain: ns1.example.com
'''

RETURN = r'''
token:
  description: A token usable as C(api_token) by the other modules.
  returned: success
  type: str
password_changed:
  description: Whether the default password was replaced on this run.
  returned: always
  type: bool
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    TechnitiumClient,
    TechnitiumError,
    api_argument_spec,
    to_api_value,
)


def main():
    argument_spec = api_argument_spec()
    # Credentials come from the dedicated options below, not the shared ones.
    for name in ('api_token', 'api_username', 'api_password'):
        argument_spec.pop(name)
    argument_spec.update(
        admin_username=dict(type='str', default='admin'),
        admin_password=dict(type='str', required=True, no_log=True),
        default_password=dict(type='str', default='admin', no_log=True),
        create_api_token=dict(type='bool', default=False),
        api_token_name=dict(type='str', default='ansible'),
    )
    module = AnsibleModule(argument_spec=argument_spec, supports_check_mode=True)

    params = module.params
    # The client's own auth is unused here; every call below passes credentials
    # explicitly, because which password is valid is exactly what we are deciding.
    module.params['api_token'] = None
    module.params['api_username'] = None
    module.params['api_password'] = None
    client = TechnitiumClient(module)

    try:
        status = client.status()
    except TechnitiumError as exc:
        module.fail_json(msg='Could not query %s/api/status: %s'
                             % (module.params['api_url'], exc.msg))

    has_defaults = bool(status.get('hasDefaultCredentials'))
    password = params['default_password'] if has_defaults else params['admin_password']

    try:
        token = client.login_as(params['admin_username'], password)
    except TechnitiumError as exc:
        module.fail_json(
            msg='Could not log in to %s as %s: %s'
                % (params['api_url'], params['admin_username'], exc.msg)
        )

    changed = has_defaults and params['admin_password'] != params['default_password']

    if changed and not module.check_mode:
        client.use_token(token)
        # 'pass' is the CURRENT password (what we just logged in with);
        # 'newPass' is the one being set. Sending the new password as 'pass'
        # and omitting 'newPass' is rejected with "Parameter 'newPass' missing.".
        client.call('/api/user/changePassword', params={
            'pass': to_api_value(password),
            'newPass': to_api_value(params['admin_password']),
        })
        token = client.login_as(params['admin_username'], params['admin_password'])

    client.use_token(token)

    if params['create_api_token'] and not module.check_mode:
        token = client.create_api_token(
            params['admin_username'], params['admin_password'], params['api_token_name'])

    module.exit_json(changed=changed, token=token, password_changed=changed)


if __name__ == '__main__':
    main()

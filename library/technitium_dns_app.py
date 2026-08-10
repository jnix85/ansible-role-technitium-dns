#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_app
short_description: Install, update, configure and remove Technitium DNS apps
description:
  - Installs a DNS app from the DNS App Store or from a URL, keeps it at the
    requested version, and manages its JSON configuration.
options:
  name:
    description: The app name exactly as it appears in the app store.
    type: str
    required: true
  state:
    description:
      - C(present) installs the app if missing.
      - C(latest) also updates it when the store has a newer version.
      - C(absent) uninstalls it.
    type: str
    choices: [present, latest, absent]
    default: present
  url:
    description:
      - Download URL for the app zip. Defaults to the store URL for I(name).
    type: str
  config:
    description:
      - The app's configuration. A mapping is serialized to JSON; a string is sent
        as-is. Compared against the current config so reruns are green.
    type: raw
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Install the Split Horizon app and configure it
  technitium_dns_app:
    name: Split Horizon
    state: latest
    config:
      networks:
        private:
          - 192.168.0.0/16
'''

RETURN = r'''
app:
  description: The installed app entry.
  returned: success
  type: dict
'''

import json

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.six import string_types
from ansible.module_utils.technitium import (
    TechnitiumError,
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    run_module,
)


def find(entries, name):
    for entry in entries or []:
        if entry.get('name') == name:
            return entry
    return None


def store_entry(client, name):
    listing = client.get('/api/apps/listStoreApps')
    return find(listing.get('storeApps'), name)


def installed_entry(client, name):
    listing = client.get('/api/apps/list')
    return find(listing.get('apps'), name)


def desired_config(params):
    config = params['config']
    if config is None:
        return None
    if isinstance(config, string_types):
        return config
    return json.dumps(config, sort_keys=True)


def config_equal(current, desired):
    """Compare as parsed JSON when possible so formatting differences do not count."""
    if current == desired:
        return True
    try:
        return json.loads(current or 'null') == json.loads(desired or 'null')
    except (ValueError, TypeError):
        return False


def run(client):
    module = client.module
    params = module.params
    name = params['name']

    current = installed_entry(client, name)

    if params['state'] == 'absent':
        if not current:
            return dict(changed=False, app=None)
        if not module.check_mode:
            client.call('/api/apps/uninstall', params=dict(name=name))
        return dict(changed=True, app=None, diff=dict(before=current, after=None))

    changed = False
    before = {}
    after = {}

    if not current or params['state'] == 'latest':
        store = store_entry(client, name)
        url = params['url'] or (store or {}).get('url')
        if not current:
            if not url:
                raise TechnitiumError(
                    'App %r was not found in the DNS App Store; supply url to install '
                    'it from elsewhere.' % name
                )
            changed = True
            after['version'] = (store or {}).get('version')
            if not module.check_mode:
                client.call('/api/apps/downloadAndInstall', params=dict(name=name, url=url))
                current = installed_entry(client, name)
        elif store and store.get('version') and \
                store['version'] != current.get('version'):
            changed = True
            before['version'] = current.get('version')
            after['version'] = store['version']
            if not module.check_mode:
                client.call('/api/apps/downloadAndUpdate', params=dict(name=name, url=url))
                current = installed_entry(client, name)

    desired = desired_config(params)
    if desired is not None:
        existing = None
        if current:
            existing = client.get('/api/apps/config/get',
                                  params=dict(name=name)).get('config')
        if not config_equal(existing, desired):
            changed = True
            before['config'] = existing
            after['config'] = desired
            if not module.check_mode:
                client.call('/api/apps/config/set', params=dict(name=name, config=desired))

    return dict(changed=changed, app=current, diff=dict(before=before, after=after))


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        name=dict(type='str', required=True),
        state=dict(type='str', choices=['present', 'latest', 'absent'], default='present'),
        url=dict(type='str'),
        config=dict(type='raw'),
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

#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_settings
short_description: Manage Technitium DNS Server settings declaratively
description:
  - Reads the current server settings, compares them against the settings you declare,
    and applies only the differences.
  - Settings you do not mention are never touched, so this module can be used
    alongside manual configuration or several times with different subsets.
  - Parameter names are the raw API names documented in the Technitium APIDOCS
    C(/api/settings/set) call, so anything the server supports is reachable without
    waiting for this module to grow an option for it.
options:
  settings:
    description:
      - Mapping of Technitium setting names to desired values.
      - Lists are joined as the API expects. An empty list clears settings that
        support clearing (for example C(forwarders) or C(blockListUrls)).
      - List-of-dict values for C(tsigKeys), C(qpmPrefixLimitsIPv4) and
        C(qpmPrefixLimitsIPv6) are encoded into the API's pipe separated format.
    type: dict
    required: true
  update_write_only:
    description:
      - The API never returns password settings, so they cannot be compared and are
        skipped by default.
      - Set to C(true) to send them anyway, which necessarily reports changed on
        every run. Values are masked in the output.
    type: bool
    default: false
extends_documentation_fragment: []
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Configure forwarders and blocking
  technitium_dns_settings:
    api_url: http://127.0.0.1:5380
    api_token: "{{ technitium_token }}"
    settings:
      dnsServerDomain: ns1.example.com
      forwarders:
        - 9.9.9.9
        - 149.112.112.112
      forwarderProtocol: Tls
      dnssecValidation: true
      recursion: AllowOnlyForPrivateNetworks
      blockListUrls:
        - https://big.oisd.nl/

- name: Stop forwarding and resolve recursively
  technitium_dns_settings:
    settings:
      forwarders: []
'''

RETURN = r'''
changed_settings:
  description: The settings that were applied, with secrets masked.
  returned: always
  type: dict
settings:
  description: The full server settings after the change.
  returned: always
  type: dict
'''

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.technitium import (
    TechnitiumError,
    api_argument_spec,
    api_required_one_of,
    api_required_together,
    diff_dict,
    pipe_rows,
    run_module,
    to_api_value,
)

# Settings the API accepts but never reads back, so they cannot be diffed.
WRITE_ONLY = (
    'webServiceTlsCertificatePassword',
    'dnsTlsCertificatePassword',
    'proxyPassword',
)

# Settings whose values are secret and must not be echoed into the diff.
SECRET = WRITE_ONLY + ('tsigKeys',)

# Multi-row settings and the field order the API expects for each row.
MULTIROW = {
    'tsigKeys': ['keyName', 'sharedSecret', 'algorithmName'],
    'qpmPrefixLimitsIPv4': ['prefix', 'udpLimit', 'tcpLimit'],
    'qpmPrefixLimitsIPv6': ['prefix', 'udpLimit', 'tcpLimit'],
}

# Settings cleared by sending the literal string "false" rather than an empty value.
CLEARABLE = (
    'forwarders',
    'blockListUrls',
    'tsigKeys',
    'recursionNetworkACL',
    'qpmPrefixLimitsIPv4',
    'qpmPrefixLimitsIPv6',
)


def encode_setting(key, value):
    """Turn a declared value into the string the settings API expects."""
    if key in MULTIROW and isinstance(value, list):
        if not value:
            return 'false'
        if all(isinstance(row, dict) for row in value):
            return pipe_rows(value, MULTIROW[key])
    if isinstance(value, list) and not value and key in CLEARABLE:
        return 'false'
    return to_api_value(value)


def mask(key, value):
    return 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER' if key in SECRET else value


def run(client):
    module = client.module
    desired = module.params['settings']
    update_write_only = module.params['update_write_only']

    current = client.get('/api/settings/get')

    comparable = {}
    forced = {}
    for key, value in desired.items():
        if value is None:
            continue
        if key in WRITE_ONLY:
            # Never returned by the API, so there is nothing to compare against.
            if update_write_only:
                forced[key] = value
            continue
        comparable[key] = value

    changes, before, after = diff_dict(current, comparable)
    changes.update(forced)
    for key in forced:
        before[key] = 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'
        after[key] = 'VALUE_SPECIFIED_IN_NO_LOG_PARAMETER'

    result = dict(
        changed=bool(changes),
        changed_settings=dict((k, mask(k, v)) for k, v in after.items()),
        settings=current,
        diff=dict(
            before=dict((k, mask(k, v)) for k, v in before.items()),
            after=dict((k, mask(k, v)) for k, v in after.items()),
        ),
    )

    if not changes or module.check_mode:
        return result

    payload = {}
    for key, value in changes.items():
        encoded = encode_setting(key, value)
        if encoded is None:
            raise TechnitiumError('Setting %s cannot be encoded from value %r' % (key, value))
        payload[key] = encoded

    result['settings'] = client.call('/api/settings/set', params=payload)
    return result


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        settings=dict(type='dict', required=True),
        update_write_only=dict(type='bool', default=False),
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

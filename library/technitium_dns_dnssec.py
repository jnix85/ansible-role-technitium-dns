#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_dnssec
short_description: Sign and unsign Technitium DNS Server primary zones
description:
  - Signs a primary zone with DNSSEC, or removes signing.
  - Signing parameters are only used when the zone is first signed. Changing the
    algorithm of an already signed zone means unsigning and signing again, which
    this module will not do implicitly.
options:
  zone:
    description: The zone to sign.
    type: str
    required: true
  state:
    description: C(signed) signs the zone, C(unsigned) removes DNSSEC from it.
    type: str
    choices: [signed, unsigned]
    default: signed
  algorithm:
    description: Signing algorithm.
    type: str
    choices: [RSA, ECDSA, EDDSA]
    default: ECDSA
  curve:
    description: Curve for C(ECDSA) and C(EDDSA).
    type: str
    choices: [P256, P384, ED25519, ED448]
    default: P256
  hash_algorithm:
    description: Hash algorithm for C(RSA).
    type: str
    choices: [MD5, SHA1, SHA256, SHA512]
  key_size:
    description: Key size in bits for C(RSA).
    type: int
  nx_proof:
    description: Proof of non-existence scheme.
    type: str
    choices: [NSEC, NSEC3]
    default: NSEC3
  iterations:
    description: NSEC3 iterations.
    type: int
  salt_length:
    description: NSEC3 salt length.
    type: int
  dnskey_ttl:
    description: TTL for the DNSKEY record set.
    type: int
  zsk_rollover_days:
    description: Automatic ZSK rollover period in days. C(0) disables rollover.
    type: int
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Sign the zone with ECDSA P-256 and NSEC3
  technitium_dns_dnssec:
    zone: example.com
    state: signed
    algorithm: ECDSA
    curve: P256
    nx_proof: NSEC3
    zsk_rollover_days: 30
'''

RETURN = r'''
dnssec:
  description: DNSSEC properties of the zone after the change.
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
)


def zone_entry(client, name):
    listing = client.get('/api/zones/list', params=dict(filterName=name))
    wanted = name.rstrip('.').lower()
    for zone in listing.get('zones') or []:
        if (zone.get('name') or '').rstrip('.').lower() == wanted:
            return zone
    return None


def run(client):
    module = client.module
    params = module.params
    name = params['zone']

    zone = zone_entry(client, name)
    if not zone:
        raise TechnitiumError('Zone %s does not exist' % name)

    status = zone.get('dnssecStatus') or 'Unsigned'
    is_signed = status.lower() != 'unsigned'

    if params['state'] == 'unsigned':
        if not is_signed:
            return dict(changed=False, dnssec=dict(status=status))
        if not module.check_mode:
            client.call('/api/zones/dnssec/unsign', params=dict(zone=name))
        return dict(changed=True, dnssec=dict(status='Unsigned'),
                    diff=dict(before=dict(dnssecStatus=status),
                              after=dict(dnssecStatus='Unsigned')))

    if is_signed:
        properties = {}
        if not module.check_mode:
            properties = client.get('/api/zones/dnssec/properties/get',
                                    params=dict(zone=name))
        return dict(changed=False, dnssec=properties or dict(status=status))

    if module.check_mode:
        return dict(changed=True, dnssec=dict(status='Signed'),
                    diff=dict(before=dict(dnssecStatus=status),
                              after=dict(dnssecStatus='Signed')))

    client.call('/api/zones/dnssec/sign', params=dict(
        zone=name,
        algorithm=params['algorithm'],
        curve=params['curve'],
        hashAlgorithm=params['hash_algorithm'],
        kskKeySize=params['key_size'],
        zskKeySize=params['key_size'],
        nxProof=params['nx_proof'],
        iterations=params['iterations'],
        saltLength=params['salt_length'],
        dnsKeyTtl=params['dnskey_ttl'],
        zskRolloverDays=params['zsk_rollover_days'],
    ))

    properties = client.get('/api/zones/dnssec/properties/get', params=dict(zone=name))
    return dict(changed=True, dnssec=properties,
                diff=dict(before=dict(dnssecStatus=status),
                          after=dict(dnssecStatus=properties.get('dnssecStatus'))))


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        zone=dict(type='str', required=True),
        state=dict(type='str', choices=['signed', 'unsigned'], default='signed'),
        algorithm=dict(type='str', choices=['RSA', 'ECDSA', 'EDDSA'], default='ECDSA'),
        curve=dict(type='str', choices=['P256', 'P384', 'ED25519', 'ED448'], default='P256'),
        hash_algorithm=dict(type='str', choices=['MD5', 'SHA1', 'SHA256', 'SHA512']),
        key_size=dict(type='int'),
        nx_proof=dict(type='str', choices=['NSEC', 'NSEC3'], default='NSEC3'),
        iterations=dict(type='int'),
        salt_length=dict(type='int'),
        dnskey_ttl=dict(type='int'),
        zsk_rollover_days=dict(type='int'),
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

#!/usr/bin/python
# -*- coding: utf-8 -*-
# Copyright (c) 2026, the ansible-role-technitium-dns authors
# MIT License (see LICENSE)

from __future__ import absolute_import, division, print_function

__metaclass__ = type

DOCUMENTATION = r'''
---
module: technitium_dns_cluster
short_description: Initialize, join and manage a Technitium DNS Server cluster
description:
  - Brings a node to the cluster membership you declare.
  - Clustering replicates the Allowed, Blocked, Apps, Settings and Administration
    sections from the primary node to every secondary node, and provides a cluster
    catalog zone for provisioning zones across the cluster. It does B(not) move an
    IP address between nodes - use keepalived/VRRP for that.
  - The module is idempotent against C(/api/admin/cluster/state), so a node that is
    already the primary, or already joined to the right cluster, reports no change.
notes:
  - Joining a cluster B(overwrites) the secondary's Allowed, Blocked, Apps, Settings
    and Administration configuration with the primary's. Configure those sections on
    the primary only.
  - The cluster domain cannot be changed after initialization.
  - Clustering requires the web service to be reachable over HTTPS. If TLS is not
    enabled the server enables it itself with a self-signed certificate, in which
    case secondaries need I(ignore_certificate_errors).
  - Every node's C(dnsServerDomain) must be a child of the cluster domain, for
    example C(ns1.example.com) in cluster C(example.com). Set it with
    M(technitium_dns_settings) before calling this module.
options:
  state:
    description:
      - C(present) initializes the cluster on a primary, or joins it on a secondary.
      - C(absent) deletes the cluster on a primary, or leaves it on a secondary.
    type: str
    choices: [present, absent]
    default: present
  role:
    description: Whether this node should be the cluster primary or a secondary.
    type: str
    choices: [primary, secondary]
    required: true
  cluster_domain:
    description:
      - The fully qualified domain name identifying the cluster.
      - Required when I(role=primary) and I(state=present). For secondaries it is
        used only to verify the node joined the cluster that was intended.
    type: str
  node_ip_addresses:
    description:
      - IP addresses of this node that the other cluster nodes can reach it on.
      - Required when I(state=present).
    type: list
    elements: str
  primary_url:
    description:
      - HTTPS URL of the primary node's web service, for example
        C(https://ns1.example.com:53443/). Required when I(role=secondary).
    type: str
  primary_ip_address:
    description:
      - IP address of the primary node. Defaults to resolving the primary URL.
    type: str
  primary_username:
    description: Administrator username on the primary node, used to authorize the join.
    type: str
  primary_password:
    description: Password for I(primary_username).
    type: str
  primary_totp:
    description: TOTP code for I(primary_username) when it has 2FA enabled.
    type: str
  ignore_certificate_errors:
    description:
      - Accept a self-signed TLS certificate on the primary node when joining.
      - Only safe on a trusted private network.
    type: bool
    default: false
  options:
    description:
      - Cluster wide heartbeat and configuration refresh intervals. Applied on the
        primary node only, and only when they differ from the current values.
    type: dict
    suboptions:
      heartbeat_refresh_interval_seconds:
        description: Node state refresh interval, 10-300.
        type: int
      heartbeat_retry_interval_seconds:
        description: Node state retry interval after a failure, 10-300.
        type: int
      config_refresh_interval_seconds:
        description: Config refresh interval from the primary, 30-3600.
        type: int
      config_retry_interval_seconds:
        description: Config refresh retry interval after a failure, 30-3600.
        type: int
  force:
    description:
      - With I(state=absent), delete the cluster on a primary that still has
        secondaries (orphaning them), or leave the cluster on a secondary whose
        primary is unreachable.
    type: bool
    default: false
author:
  - ansible-role-technitium-dns authors
'''

EXAMPLES = r'''
- name: Initialize the cluster on the primary node
  technitium_dns_cluster:
    api_url: http://127.0.0.1:5380
    api_username: admin
    api_password: "{{ technitium_admin_password }}"
    role: primary
    cluster_domain: example.com
    node_ip_addresses:
      - 192.168.10.5
    options:
      heartbeat_refresh_interval_seconds: 30
      config_refresh_interval_seconds: 900

- name: Join the cluster as a secondary node
  technitium_dns_cluster:
    role: secondary
    cluster_domain: example.com
    node_ip_addresses:
      - 192.168.10.6
    primary_url: https://ns1.example.com:53443/
    primary_ip_address: 192.168.10.5
    primary_username: admin
    primary_password: "{{ technitium_admin_password }}"
    ignore_certificate_errors: true

- name: Remove this secondary from the cluster
  technitium_dns_cluster:
    role: secondary
    state: absent
'''

RETURN = r'''
cluster:
  description: Cluster state as reported by /api/admin/cluster/state after the run.
  returned: always
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

OPTION_API_NAMES = {
    'heartbeat_refresh_interval_seconds': 'heartbeatRefreshIntervalSeconds',
    'heartbeat_retry_interval_seconds': 'heartbeatRetryIntervalSeconds',
    'config_refresh_interval_seconds': 'configRefreshIntervalSeconds',
    'config_retry_interval_seconds': 'configRetryIntervalSeconds',
}


def self_node(state):
    for node in state.get('nodes') or []:
        if node.get('state') == 'Self':
            return node
    return {}


def apply_options(client, state, check_mode):
    """Push cluster wide intervals, but only the ones that actually differ."""
    desired = client.module.params.get('options') or {}
    changes = {}
    for key, api_name in OPTION_API_NAMES.items():
        value = desired.get(key)
        if value is None:
            continue
        if not values_equal(state.get(api_name), value):
            changes[api_name] = value
    if not changes:
        return state, False
    if check_mode:
        merged = dict(state)
        merged.update(changes)
        return merged, True
    return client.call('/api/admin/cluster/primary/setOptions', params=changes), True


def ensure_primary(client, state, check_mode):
    params = client.module.params
    cluster_domain = params['cluster_domain']

    if state.get('clusterInitialized'):
        current_domain = state.get('clusterDomain')
        if not values_equal(current_domain, cluster_domain):
            raise TechnitiumError(
                'This node is already in cluster %r but %r was requested. The cluster '
                'domain cannot be changed; delete the cluster first.'
                % (current_domain, cluster_domain)
            )
        node_type = self_node(state).get('type')
        if node_type != 'Primary':
            raise TechnitiumError(
                'This node is a %s in cluster %r, not the primary. Promote it with the '
                'cluster promote workflow before managing it as a primary.'
                % (node_type, current_domain)
            )
        state, changed = apply_options(client, state, check_mode)
        return dict(changed=changed, cluster=state)

    if check_mode:
        return dict(changed=True, cluster=dict(state, clusterInitialized=True,
                                               clusterDomain=cluster_domain))

    state = client.call('/api/admin/cluster/init', params=dict(
        clusterDomain=cluster_domain,
        primaryNodeIpAddresses=params['node_ip_addresses'],
    ))
    state, _ = apply_options(client, state, check_mode)
    return dict(changed=True, cluster=state)


def ensure_secondary(client, state, check_mode):
    params = client.module.params

    if state.get('clusterInitialized'):
        current_domain = state.get('clusterDomain')
        if params['cluster_domain'] and not values_equal(current_domain, params['cluster_domain']):
            raise TechnitiumError(
                'This node is already in cluster %r but %r was requested. Have the node '
                'leave its current cluster first.' % (current_domain, params['cluster_domain'])
            )
        node_type = self_node(state).get('type')
        if node_type == 'Primary':
            raise TechnitiumError(
                'This node is the primary of cluster %r, so it cannot join as a '
                'secondary.' % current_domain
            )
        return dict(changed=False, cluster=state)

    if check_mode:
        return dict(changed=True, cluster=dict(state, clusterInitialized=True,
                                               clusterDomain=params['cluster_domain']))

    state = client.call('/api/admin/cluster/initJoin', params=dict(
        secondaryNodeIpAddresses=params['node_ip_addresses'],
        primaryNodeUrl=params['primary_url'],
        primaryNodeIpAddress=params['primary_ip_address'],
        ignoreCertificateErrors=params['ignore_certificate_errors'],
        primaryNodeUsername=params['primary_username'],
        primaryNodePassword=params['primary_password'],
        primaryNodeTotp=params['primary_totp'],
    ))
    return dict(changed=True, cluster=state)


def ensure_absent(client, state, check_mode):
    params = client.module.params

    if not state.get('clusterInitialized'):
        return dict(changed=False, cluster=state)

    if check_mode:
        return dict(changed=True, cluster=dict(state, clusterInitialized=False))

    if params['role'] == 'primary':
        state = client.call('/api/admin/cluster/primary/delete',
                            params=dict(forceDelete=params['force']))
    else:
        state = client.call('/api/admin/cluster/secondary/leave',
                            params=dict(forceLeave=params['force']))
    return dict(changed=True, cluster=state)


def run(client):
    module = client.module
    params = module.params
    state = client.call('/api/admin/cluster/state', method='GET')

    if params['state'] == 'absent':
        return ensure_absent(client, state, module.check_mode)
    if params['role'] == 'primary':
        return ensure_primary(client, state, module.check_mode)
    return ensure_secondary(client, state, module.check_mode)


def main():
    argument_spec = api_argument_spec()
    argument_spec.update(
        state=dict(type='str', choices=['present', 'absent'], default='present'),
        role=dict(type='str', choices=['primary', 'secondary'], required=True),
        cluster_domain=dict(type='str'),
        node_ip_addresses=dict(type='list', elements='str'),
        primary_url=dict(type='str'),
        primary_ip_address=dict(type='str'),
        primary_username=dict(type='str'),
        primary_password=dict(type='str', no_log=True),
        primary_totp=dict(type='str', no_log=True),
        ignore_certificate_errors=dict(type='bool', default=False),
        force=dict(type='bool', default=False),
        options=dict(type='dict', options=dict(
            heartbeat_refresh_interval_seconds=dict(type='int'),
            heartbeat_retry_interval_seconds=dict(type='int'),
            config_refresh_interval_seconds=dict(type='int'),
            config_retry_interval_seconds=dict(type='int'),
        )),
    )
    module = AnsibleModule(
        argument_spec=argument_spec,
        required_together=api_required_together(),
        required_one_of=api_required_one_of(),
        required_if=[('state', 'present', ('node_ip_addresses',))],
        supports_check_mode=True,
    )

    if module.params['state'] == 'present':
        if module.params['role'] == 'primary' and not module.params['cluster_domain']:
            module.fail_json(msg='cluster_domain is required when role=primary and state=present')
        if module.params['role'] == 'secondary':
            missing = [name for name in ('primary_url', 'primary_username', 'primary_password')
                       if not module.params[name]]
            if missing:
                module.fail_json(
                    msg='%s are required when role=secondary and state=present'
                        % ', '.join(missing)
                )

    run_module(module, run)


if __name__ == '__main__':
    main()

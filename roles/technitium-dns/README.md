# ansible-role-technitium-dns

Install and declaratively configure [Technitium DNS Server](https://technitium.com/dns/),
its built-in **clustering**, **keepalived/VRRP** for a floating service address,
and **ACME certificates** it installs and renews itself.

You describe the DNS service you want; the role converges to it. Re-runs are green,
`--check --diff` shows what would change, and everything the server's HTTP API can do
is reachable from variables.

## The two halves of "highly available"

These are different mechanisms and it matters that you keep them apart:

| | Technitium clustering | keepalived / VRRP |
|---|---|---|
| What it does | Replicates configuration between nodes | Moves one IP address between nodes |
| What it protects | Config drift between servers | A single node going down |
| Managed by | `technitium_dns_cluster_*` | `technitium_dns_keepalived_*` |

Clustering gives every node the same Allowed, Blocked, Apps, Settings and
Administration configuration, and a **cluster catalog zone** whose member zones are
provisioned onto every node automatically. It does not fail anything over. VRRP
gives clients one address to point at, and hands that address to a healthy node
when the current holder stops answering. You almost always want both.

## Requirements

- Ansible 2.15+
- Debian 12/13, Ubuntu 22.04/24.04, or RHEL/Rocky/Alma 9/10 with systemd
- Collections, only when the matching feature is enabled:
  `ansible.posix` (keepalived sysctls, firewalld), `community.general` (ufw),
  and `community.crypto` (ACME certificates)

## Quick start

```yaml
- hosts: dns
  become: true
  roles:
    - role: ansible-role-technitium-dns
      vars:
        technitium_dns_admin_password: "{{ vault_admin_password }}"
        technitium_dns_settings:
          forwarders: [9.9.9.9, 149.112.112.112]
          forwarderProtocol: Tls
          dnssecValidation: true
        technitium_dns_zones:
          - zone: internal.example.com
            records:
              - domain: app.internal.example.com
                type: A
                data: {ipAddress: 192.168.10.20}
```

A full three-node cluster with a VIP is in [`examples/`](examples/).

## How configuration is applied

Technitium replicates *some* settings between cluster nodes and not others. The
API documentation marks the replicated ones as "cluster parameter". The role
mirrors that split, because applying a replicated setting per node means fighting
the sync loop:

- **`technitium_dns_node_settings`** — node-local (listen endpoints, web service
  addresses and ports, TLS certificate paths, logging, `dnsServerDomain`).
  Applied on **every host, always**.
- **`technitium_dns_settings`** — cluster-wide (forwarders, recursion, blocking,
  DNSSEC, TSIG keys, QPM limits...). Applied on **the primary node only** when
  clustering is enabled, and on every host when it is not.

The same rule governs zones, users, groups, permissions, apps and the
allowed/blocked lists: authored on the primary, replicated from there. DHCP
scopes are the exception — Technitium does not replicate them, so they are
applied per node and belong in `host_vars`.

Both settings variables take **raw API parameter names**, so anything the server
supports works immediately without waiting for the role to add an option:

```yaml
technitium_dns_settings:
  qpmLimitSampleMinutes: 5
  qpmPrefixLimitsIPv4:
    - {prefix: 32, udpLimit: 600, tcpLimit: 600}
  tsigKeys:
    - keyName: xfr-key
      sharedSecret: "{{ vault_tsig_secret }}"
      algorithmName: hmac-sha256
  forwarders: []   # an empty list clears the setting
```

## Clustering

```yaml
technitium_dns_cluster_enabled: true
technitium_dns_cluster_domain: example.com     # cannot be changed later
technitium_dns_cluster_primary: ns1            # an inventory_hostname
```

Per host, `technitium_dns_node_domain` must be a **child of the cluster domain**
(`ns1.example.com` in cluster `example.com`); the role asserts this before touching
anything. The primary initializes the cluster, then secondaries join one at a time.

Things worth knowing:

- **Joining overwrites** the secondary's Allowed, Blocked, Apps, Settings and
  Administration configuration with the primary's. That is the point, but it means
  a node's local edits in those sections are lost when it joins.
- Clustering requires the web service on **HTTPS**. The role enables it with a
  self-signed certificate by default and sets
  `technitium_dns_cluster_ignore_certificate_errors: true` to match. Point
  `webServiceTlsCertificatePath` at a real certificate and turn that off for
  anything exposed beyond a trusted network.
- The **cluster domain cannot be changed** after initialization. The role fails
  loudly rather than silently doing the wrong thing if you change it later.
- Run with the default `linear` strategy. With `strategy: free` a secondary can
  try to join before the primary has initialized.

To publish a zone to every node, make it a member of the cluster catalog zone:

```yaml
technitium_dns_zones:
  - zone: internal.example.com
    catalog: "{{ technitium_dns_cluster_catalog_zone }}"   # cluster-catalog.example.com
```

## keepalived / VRRP

```yaml
technitium_dns_keepalived_enabled: true
technitium_dns_keepalived_instances:
  - name: DNS_VIP
    interface: "{{ ansible_default_ipv4.interface }}"
    virtual_router_id: 51                  # unique per L2 segment
    priority: "{{ 150 if inventory_hostname == 'ns1' else 100 }}"
    virtual_ipaddresses:
      - address: 192.168.10.4
        prefix: 24
    authentication:
      auth_type: PASS
      auth_pass: "{{ vault_vrrp_password }}"
```

Defaults chosen deliberately:

- **Unicast peering**, derived from the other hosts in the play. Multicast VRRP
  does not work on most cloud and routed networks; set `unicast: false` if you
  want it.
- The health check **queries DNS** rather than checking that the process exists.
  A resolver that is running but wedged is worse than a dead one — it holds the
  VIP and blackholes queries. Set
  `technitium_dns_keepalived_health_check_api_token` to use the server's own
  `/api/dnsClient/healthCheck` endpoint instead, which also distinguishes SERVFAIL.
- The config is written with `validate: keepalived -t -f %s`, so a broken template
  can never take the VIP down, and the handler **reloads** rather than restarts,
  so applying config does not trigger a needless election.
- Transitions are logged to syslog by `notify_vrrp.sh`; hook your own command in
  with `technitium_dns_keepalived_notify_command`.

## TLS certificates via ACME

```yaml
technitium_dns_acme_enabled: true
technitium_dns_acme_account_email: hostmaster@example.com
technitium_dns_acme_terms_agreed: true
technitium_dns_acme_domains:
  - ns1.example.com
technitium_dns_acme_pfx_password: "{{ vault_acme_pfx_password }}"
```

Requests a certificate from an ACME server (Let's Encrypt by default;
override `technitium_dns_acme_directory_url` for staging or another server),
bundles it into a PFX with `community.crypto`, and sets
`webServiceTlsCertificatePath` / `webServiceTlsCertificatePassword` on
Technitium's web service itself — nobody has to carry a certificate onto
these hosts by hand.

Validation is `dns-01`: the `_acme-challenge` TXT record is published
through this role's own `technitium_dns_record` module, against whichever
zone this server is already authoritative for. No port 80, no separate web
server, and it works for internal names Let's Encrypt could never reach over
HTTP.

Things worth knowing:

- **Renewal happens on re-run**, not on a timer. `community.crypto`'s
  `acme_certificate` module only reissues once fewer than
  `technitium_dns_acme_remaining_days` (default 30) remain; every other run
  is a no-op. Schedule the play — cron, AWX, CI — the same way you would any
  other Ansible-managed certificate.
- The PFX passphrase (`technitium_dns_acme_pfx_password`) is required and
  belongs in a vault, same as `technitium_dns_admin_password`.
- The first entry in `technitium_dns_acme_domains` becomes the certificate's
  common name and the filenames under `technitium_dns_acme_cert_dir`; every
  entry becomes a SAN.
- This is node-local, like the rest of `technitium_dns_node_settings` —
  clustering does not replicate TLS certificates, so each node in a cluster
  requests and installs its own.

## Choosing a VIP topology

Two ways to give clients a stable address, both compatible with this role:

- **On-node VRRP** (above): keepalived runs on the Technitium nodes
  themselves. Simplest — no extra hosts. See
  `examples/proxmox-ns-cluster/` for a full example against a real
  five-node topology.
- **Separate director nodes**: `technitium_dns_keepalived_enabled: false`
  here, and a dedicated pair of hosts runs keepalived + IPVS in front of
  the DNS nodes instead (e.g. `ansible-role-keepalived`, a companion role
  not part of this repo). Use this if VRRP on the DNS nodes ever becomes a
  problem — election flapping under load, wanting failover hosts shared
  across multiple services — without changing how this role configures
  Technitium. See `examples/proxmox-director-cluster/` for a full example,
  including the IPVS DR-mode backend prerequisites this role deliberately
  does not manage.

Neither example currently reflects any specific production deployment;
each README says explicitly what it is and is not a template for.

## Installation methods

| `technitium_dns_install_method` | Behaviour |
|---|---|
| `script` (default) | Runs the official installer, which handles the .NET runtime and the systemd unit. Runs only when not installed; set `technitium_dns_force_reinstall: true` to upgrade. |
| `tarball` | The role installs the runtime, creates the service user, unpacks a pinned `DnsServerPortable.tar.gz` and templates its own unit. Use for pinned versions, internal mirrors, or air-gapped installs via `technitium_dns_tarball_local_path`. |

## Modules

The role's tasks are thin wrappers around modules in `library/`, and you can use
them directly in your own playbooks. All support check mode and diff.

| Module | Purpose |
|---|---|
| `technitium_dns_bootstrap` | Replace the default `admin`/`admin` password, return a token |
| `technitium_dns_settings` | Server settings, compared key by key |
| `technitium_dns_zone` | Zones of every type, plus per-zone options |
| `technitium_dns_record` | Resource records, with an `exclusive` mode for declarative record sets |
| `technitium_dns_cluster` | Initialize, join, leave, delete a cluster |
| `technitium_dns_dhcp_scope` | DHCP scopes, exclusions, static routes, reservations |
| `technitium_dns_user` / `_group` / `_permission` | Accounts, groups, section and zone ACLs |
| `technitium_dns_app` | Install, update and configure DNS apps |
| `technitium_dns_blocklist` | Allowed and blocked zone entries |
| `technitium_dns_dnssec` | Zone signing |
| `technitium_dns_info` | Read-only facts, for assertions |

Authenticate with `api_token`, or `api_username`/`api_password`, or the
`TECHNITIUM_API_TOKEN` / `TECHNITIUM_API_URL` environment variables.

```yaml
- name: The zone apex has exactly one MX record
  technitium_dns_record:
    api_url: https://ns1.example.com:53443
    api_token: "{{ token }}"
    zone: example.com
    domain: example.com
    type: MX
    exclusive: true
    data:
      exchange: mail.example.com
      preference: 10
```

`exclusive: true` is what makes a record set declarative: the declared record
becomes the only one of that name and type, and anything else is removed.

## Variables

See [`defaults/main.yml`](defaults/main.yml) — every variable is documented there —
and [`meta/argument_specs.yml`](meta/argument_specs.yml) for the validated interface.

## Samba AD integration (opt-in)

`tasks/samba_ad_zone.yml` and `tasks/samba_ad_records.yml` let another role
(e.g. `ansible-role-samba_dc`) provision a Primary zone for a Samba AD realm
and push its DNS records into this cluster, declaratively — not via Samba's
own dynamic DNS/TSIG. Neither is part of the default `tasks/main.yml` flow;
both are reached only via an explicit `include_role: tasks_from:`. See the
`technitium_dns_samba_*` variables documented in `defaults/main.yml` for the
required inputs.

## Generic ACME DNS-01 challenge record (opt-in)

`tasks/acme_dns01_record.yml` publishes or removes an `_acme-challenge` TXT
record for a DNS-01 certificate request — the remote-caller counterpart to
this role's own `tasks/acme.yml` (which certifies Technitium's own web
console using its local bootstrapped API session and isn't reusable outside
this role). Any role that runs `community.crypto.acme_certificate` itself and
just needs somewhere to publish/remove the challenge record reaches this via
`include_role: name: technitium_dns, tasks_from: acme_dns01_record` — see
`ansible-role-samba_dc`'s `roles/samba_dc/tasks/certificate.yml`. Not part of
the default `tasks/main.yml` flow. See the `technitium_dns_acme_*` variables
documented in `defaults/main.yml` for the required inputs.

## Tags

`technitium_install`, `technitium_service`, `technitium_settings`,
`technitium_cluster`, `technitium_zones`, `technitium_access`,
`technitium_blocking`, `technitium_apps`, `technitium_dhcp`,
`technitium_acme`, `technitium_firewall`, `keepalived`.

## Testing

```bash
python -m pytest tests/unit -q     # API client: encoding, comparison, diff logic
yamllint . && ansible-lint
molecule test -s default           # one node (Docker), converge + idempotence + verify
molecule test -s cluster           # three nodes (Docker), cluster + VIP failover
molecule test -s vm-cluster        # three nodes (VMs), the same, on real interfaces
```

The `cluster` and `vm-cluster` scenarios assert that all three nodes report each
other as connected, that a catalog member zone reaches the secondaries, and that
stopping keepalived on the master moves the VIP to a backup that still answers
queries.

`default` and `cluster` run in Docker, which is fast but a poor substitute for a
real host in two ways this role specifically cares about: the official installer
refuses to run without systemd as PID 1, and containers share a bridge rather
than a real L2 segment, which makes VRRP failover a weak test. `vm-cluster` runs
the same cluster scenario on three Debian VMs (Vagrant + libvirt/KVM) instead,
which also exercises the systemd-resolved stub-listener handling that a stock
Debian host needs and a container never has. It requires
`libvirt-daemon-system`, `vagrant`, and `vagrant-libvirt` on the test host, and
is not part of the default `molecule test` — run it explicitly, or via the
`workflow_dispatch` CI job on a runner with KVM available.

## Licence

MIT. Technitium DNS Server is a separate project with its own licence.

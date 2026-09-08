# proxmox-ns-cluster

Five-node Technitium cluster shaped like the real ns1-ns5 topology
(Proxmox LXCs `ns1`-`ns5` on hosts `pve01`-`pve05`, `10.1.0.21-25/23`), with
the service VIP (`10.1.0.53/23`) held via **on-node VRRP** — keepalived runs
directly on each Technitium node, exactly as documented in the top-level
README's "keepalived / VRRP" section.

This is the simplest topology: no extra hosts, VRRP failover and DNS
clustering both live on the same five nodes.

## What this is not

This is a template for staging a cutover, not something to run against the
live ns1-ns5 hosts. Production DNS on that subnet is currently served by
`proxmox-ansible-roles`' bundled `roles/technitium` + `roles/keepalived`
(different role code, same intent: VIP `10.1.0.53/23`, `virtual_router_id`
53). Point this playbook at throwaway or staging hosts first — new LXCs,
VMs, or the `molecule/vm-cluster` scenario's approach — and only plan an
actual production cutover once you've verified behavior there. Migrating
the live hosts to this role is a separate, deliberately staged task.

## Usage

```
ansible-playbook -i inventory.yml site.yml --syntax-check
ansible-playbook -i inventory.yml site.yml --check --diff
ansible-playbook -i inventory.yml site.yml
```

You'll need a `vault.yml` alongside `site.yml` providing
`vault_technitium_admin_password` and `vault_vrrp_password` (see the
top-level `examples/site.yml` for the same pattern).

## When to prefer the alternative

If VRRP ever becomes a problem on the DNS nodes themselves (e.g. election
flapping under load, wanting to decouple network failover from DNS server
health, or sharing failover hosts across multiple services), see
`../proxmox-director-cluster/` instead — it moves VRRP onto dedicated
director nodes without changing how this role configures Technitium.

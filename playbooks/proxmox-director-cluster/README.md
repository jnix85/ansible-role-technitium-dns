# proxmox-director-cluster

Same five-node Technitium cluster as `../proxmox-ns-cluster/`, but the
service VIP (`10.1.0.53/23`) is owned by two dedicated director hosts
(`dnslb1`/`dnslb2`) running [`ansible-role-keepalived`](../../../ansible-role-keepalived)
in IPVS DR mode, instead of by keepalived running on the DNS nodes
themselves (`technitium_dns_keepalived_enabled: false` here).

This is the "move VRRP off the DNS hosts" path: if on-node VRRP ever
becomes a problem (election flapping under DNS server load, wanting
failover hosts shared across multiple services, wanting to
troubleshoot/restart the load balancer without touching DNS at all), stand
up director nodes and switch to this shape without changing anything about
how `ansible-role-technitium-dns` configures Technitium itself — the only
role-level change is turning `technitium_dns_keepalived_enabled` off.

## Prerequisites this repo does not manage

IPVS DR mode requires each DNS backend to bind the VIP on a
non-ARP-advertising interface (traditionally `lo:0`, with
`arp_ignore=1`/`arp_announce=2`) so it can accept traffic addressed to the
VIP without answering ARP for it. **`ansible-role-technitium-dns` does not
configure this** — it's backend-side IPVS plumbing, not a Technitium DNS
concern, and adding it here would let this role assume a load-balancer
topology it shouldn't need to know about. Apply it yourself (by hand, a
host_vars-level `ansible.posix.sysctl` + `ansible.builtin.template` for a
`lo:0`-equivalent, or a small role of your own) before pointing directors
at real backends. `ansible-role-keepalived`'s README ("Technitium design
notes") documents the exact requirement and a known failure mode
(director-side ARP blackhole after a route-cache eviction) if it's missed.

If you'd rather not deal with DR mode's backend requirements at all, use
`../proxmox-ns-cluster/` instead — on-node VRRP has no such prerequisite.

## Usage

```
ansible-galaxy role install -r requirements.yml -p ../../roles
ansible-playbook -i inventory.yml site.yml --syntax-check
ansible-playbook -i inventory.yml site.yml --check --diff
ansible-playbook -i inventory.yml site.yml
```

`requirements.yml`'s `src` is a placeholder — point it at wherever you
host `ansible-role-keepalived`, or symlink/checkout it locally and adjust.

You'll need a `vault.yml` alongside `site.yml` providing
`vault_technitium_admin_password` and `vault_vrrp_password`.

## What this is not

Like `../proxmox-ns-cluster/`, this is a template for staging a topology
change, not something to run against live ns1-ns5 hosts or the production
`keepalived01-03` directors without a maintenance window. It's also not a
1:1 copy of the live director fleet: production's `ansible-role-keepalived`
inventory fronts the *old* `dns01-05` backend addresses (`10.1.0.5-9`) with
three directors and two VIPs; this example uses the newer ns1-ns5 addresses
and two directors/one VIP to match `proxmox-ns-cluster/`'s scope. Reconcile
the two before using this as a real migration plan.

# technitium_dns

Installs, configures and clusters Technitium DNS Server, and exposes its HTTP API
as a set of Ansible modules other roles can call directly. Most of the role
configures the node(s) it runs on; a smaller "opt-in task file" surface lets
other roles reach into a running Technitium cluster from the outside.

## Language

**Bootstrapped session**:
The API token this role's own tasks use, obtained by `tasks/bootstrap.yml` logging
into the *local* node's default `admin`/`admin` account (`technitium_dns_api_args`).
Only valid for a play that is configuring the Technitium node itself.
_Avoid_: reusing `technitium_dns_api_args` from a caller outside this role — it
isn't set unless this role's own `main.yml` flow ran first on that host.

**Remote caller**:
Another role (or this role's own `tasks/acme.yml`, for a different reason — see
below) reaching a *running* Technitium cluster from outside, using an explicit,
pre-issued `api_token` and `api_url` (typically the cluster's keepalived VIP) —
never the bootstrapped session, since the caller isn't necessarily running on a
Technitium node at all. Every opt-in task file (below) is written for this case.
_Avoid_: assuming a remote caller can see `technitium_dns_api_args` or any other
role-internal fact — pass connection details explicitly, every time.

**Opt-in task file**:
A `tasks/*.yml` reachable only via an explicit `include_role: tasks_from: <name>`,
never through the default `tasks/main.yml` flow. Existing examples:
`samba_ad_zone.yml` / `samba_ad_records.yml` (provision an AD realm's zone and
push its records — used by `ansible-role-samba_dc`), and `acme_dns01_record.yml`
(publish/remove one ACME DNS-01 challenge TXT record — generic, used by
`ansible-role-samba_dc`'s `roles/samba_dc/tasks/certificate.yml`, and could be
reused by any other role in this fleet that runs its own ACME client). Adding one
never changes default behavior for existing users of this role.
_Avoid_: wiring a new capability into `tasks/main.yml` when it's only needed by
some deployments — follow the opt-in pattern instead.

**Self-certification** (`tasks/acme.yml`):
This role requesting a certificate *for its own node's web console*, gated by
`technitium_dns_acme_enabled` and run from `tasks/main.yml`. Uses the bootstrapped
session, writes its own DNS-01 challenge via a direct `technitium_dns_record` call
(not through `acme_dns01_record.yml`), and finishes by bundling a PFX and pointing
`technitium_dns_settings` at it — none of which a remote caller wants. Not
reusable outside this role as-is.
_Avoid_: confusing this with `acme_dns01_record.yml` — same underlying ACME
mechanism (DNS-01, community.crypto.acme_certificate), unrelated call paths and
unrelated purposes (this role's own web console TLS vs. some other role's own
certificate need).

**Generic ACME DNS-01 challenge record** (`acme_dns01_record.yml`):
The remote-caller counterpart to self-certification: publishes or removes an
`_acme-challenge` TXT record for a caller that runs `community.crypto.acme_certificate`
itself and just needs somewhere to put the challenge. Takes an explicit
`technitium_dns_acme_zone` so a caller can target a zone other than the
certificate's own domain — the standard trick for issuing a cert for a domain
whose zone isn't itself publicly delegated (see `ansible-role-samba_dc`'s
`docs/adr/0003-ldaps-cert-via-acme-dns01.md` for a worked example: a static CNAME
delegates just the challenge name into a zone that is publicly delegated).
_Avoid_: assuming the "domain" in a challenge record is always the cert's own
domain — with CNAME delegation it's the alias target instead.

**Samba AD integration** (`samba_ad_zone.yml` / `samba_ad_records.yml`):
Lets `ansible-role-samba_dc` provision a Primary zone for its AD realm and push
its A/SRV records into this cluster, declaratively — not via Samba's own dynamic
DNS/TSIG. No TSIG key or update ACL involved anywhere; auth is the same API token
as everything else. See `ansible-role-samba_dc/docs/adr/0001-external-dns-via-technitium.md`.

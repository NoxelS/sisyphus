# Sisyphus agent context

Sisyphus is a personal infrastructure cluster managed from this repository.
Treat this file and the repository documentation as the operational context for
all changes.

## Current cluster

- Topology: one node only; this is not an HA cluster yet.
- Operating system: Talos Linux 1.13.9.
- Kubernetes: 1.36.2.
- Node role: schedulable single control-plane node.
- CNI: Cilium 1.20.1.
- kube-proxy: enabled during the initial bootstrap.
- Storage: Rancher local-path provisioner v0.0.36 is deployed. Persistent data
  is stored on `/var/mnt/local-path` on the single node; Longhorn is not
  deployed.
- Ingress: Cloudflare Tunnel is configured in the Cloudflare dashboard and
  deployed by Flux as two `cloudflared` replicas. There is no Traefik, Caddy,
  or other Kubernetes ingress controller.
- Applications: Kite is deployed by Flux with one replica and a standalone
  PostgreSQL release. Kite's Kubernetes Ingress and Gateway are disabled; the
  Cloudflare tunnel is the public entry point.

## Ownership and workflow

- Talos machine configuration owns the host and Kubernetes bootstrap layer.
- Flux owns resources inside Kubernetes after its one-time manual bootstrap.
- SOPS + age protect Kubernetes secrets. Noel's and Flux's public recipients
  are in `.sops.yaml`; private keys never belong in Git.
- Generated Talos configs and `talosconfig` are credential-bearing artifacts
  and must remain ignored.
- The Cloudflare tunnel token, Kite encryption key, and PostgreSQL credentials
  are SOPS-encrypted Kubernetes Secrets. Never decode them into Git or logs.
- Do not assume SSH, a package manager, or mutable host files exist on Talos.
- Do not manually apply normal Kubernetes resources after Flux is active.
- Preserve the single-node limitation in plans and documentation; do not call
  this deployment HA until control-plane and storage nodes are distributed.
- Do not describe local-path storage as replicated or highly available. Do not
  deploy Longhorn until its data disk, Talos mount, replica count, and backup
  target are explicitly configured.

## Bootstrap state

The node was installed from a Talos Image Factory image containing the
`iscsi-tools` and `util-linux-tools` extensions. Talos networking required a
static Netcup IPv4 configuration. Cilium was installed once manually with
Talos-specific cgroup and capability settings because the cluster CNI must be
running before Flux workloads can become Ready. Flux was then bootstrapped
once from a trusted workstation; normal platform and application changes now
belong in Git and are reconciled by Flux.

The current Flux roots reconcile storage first, then Kite, while the
Cloudflare Tunnel is reconciled independently. The tunnel's public-hostname
route is managed in the Cloudflare dashboard; the repository contains the
in-cluster deployment and encrypted token only.

The current storage layer uses the `local-path` StorageClass with
`/var/mnt/local-path` as its node path. The class is not the default and uses
`Retain`; PostgreSQL requests a 2 GiB `ReadWriteOnce` volume. A node loss or
reinstallation therefore requires an explicit recovery/backup procedure.

No default-deny Cilium policies or Talos ingress-firewall documents are
currently managed here. The provider firewall should block public origin
ports; add Cilium policies before treating pod-to-pod or pod egress as
restricted.

## Safety rules

- Never print, commit, or request private age keys, Talos secrets, or the full
  generated machine configuration.
- Validate YAML, SOPS rules, and generated manifests before committing.
- Prefer small, reviewable changes and preserve unrelated working-tree edits.
- Keep recovery instructions current whenever bootstrap or storage changes.
- Before changing firewall rules, retain restricted administrative access to
  Talos TCP 50000 and Kubernetes TCP 6443, and verify an out-of-band Netcup
  console recovery path.

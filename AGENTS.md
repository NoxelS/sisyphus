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
- Storage: Longhorn is planned but not deployed; the node currently exposes
  only `/dev/vda`, so a Longhorn data-disk decision is still required.
- Ingress: Cloudflare Tunnel is planned and will be configured manually in the
  Cloudflare dashboard, then deployed in-cluster by Flux.

## Ownership and workflow

- Talos machine configuration owns the host and Kubernetes bootstrap layer.
- Flux owns resources inside Kubernetes after its one-time manual bootstrap.
- SOPS + age protect Kubernetes secrets. Noel's and Flux's public recipients
  are in `.sops.yaml`; private keys never belong in Git.
- Generated Talos configs and `talosconfig` are credential-bearing artifacts
  and must remain ignored.
- Do not assume SSH, a package manager, or mutable host files exist on Talos.
- Do not manually apply normal Kubernetes resources after Flux is active.
- Preserve the single-node limitation in plans and documentation; do not call
  this deployment HA until control-plane and storage nodes are distributed.
- Do not deploy Longhorn until its data disk, Talos mount, replica count, and
  backup target are explicitly configured.

## Bootstrap state

The node was installed from a Talos Image Factory image containing the
`iscsi-tools` and `util-linux-tools` extensions. Talos networking required a
static Netcup IPv4 configuration. Cilium was installed once manually with
Talos-specific cgroup and capability settings because the cluster CNI must be
running before Flux workloads can become Ready. Flux bootstrap is the next
one-time manual boundary; after that, platform changes belong in Git.

## Safety rules

- Never print, commit, or request private age keys, Talos secrets, or the full
  generated machine configuration.
- Validate YAML, SOPS rules, and generated manifests before committing.
- Prefer small, reviewable changes and preserve unrelated working-tree edits.
- Keep recovery instructions current whenever bootstrap or storage changes.

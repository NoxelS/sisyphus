# Sisyphus cluster bootstrap

This document records the current one-node bootstrap and its intentional
boundaries. It is a recovery and handoff document, not a replacement for
version-pinned vendor documentation.

## Current state

Sisyphus currently runs as one schedulable control-plane node at Netcup. It is
not highly available: a node, disk, provider, or network failure takes the
cluster offline. Talos is the host operating system, Kubernetes is the
orchestrator, and Cilium is the active CNI. Flux has not yet become the
in-cluster source of truth until its bootstrap is completed.

## Talos installation

The node was booted through the Netcup VNC/console using a Talos Image Factory
image. The image schematic includes these system extensions:

- `siderolabs/iscsi-tools` — required by Longhorn for `iscsid`/`iscsiadm`.
- `siderolabs/util-linux-tools` — provides utilities used by Longhorn, such as
  filesystem trimming support.

The installer image in the machine configuration must use the same schematic as
the boot image. Generated machine configuration and `talosconfig` contain
cluster credentials and must never be committed.

## Netcup networking

The initial maintenance-mode boot had link connectivity but no usable IPv4
address or route. The VNC network screen was used to configure the Netcup
static IPv4 address, gateway, and DNS resolvers. The exact prefix and gateway
must always be taken from Netcup SCP; they must not be guessed.

The final machine configuration must retain this network configuration so a
reboot does not return the node to maintenance mode without networking. Public
management ports should be restricted by the provider/host firewall to the
administrator's temporary source address and removed once private management
access is available. Cloudflare Tunnel does not require public origin ports.

## Kubernetes bootstrap

The machine configuration makes the sole control-plane node schedulable and
sets the CNI to `none` so Talos does not install Flannel. Kube-proxy remains
enabled for the first Cilium deployment. The sequence is:

1. Apply the final Talos machine configuration with `talosctl`.
2. Bootstrap the single etcd member exactly once.
3. Retrieve the kubeconfig.
4. Install Cilium once manually so the node can become Ready.
5. Bootstrap Flux once from a trusted workstation.

The Cilium installation used Kubernetes IPAM, kube-proxy replacement disabled,
Talos's existing cgroup v2 mount, and a capability set without `SYS_MODULE`.
Keep those values when moving Cilium under Flux management.

The one-time installation command was:

```sh
helm upgrade --install cilium cilium/cilium \
  --namespace kube-system \
  --version 1.20.1 \
  --reuse-values \
  --set ipam.mode=kubernetes \
  --set kubeProxyReplacement=false \
  --set bpf.hostLegacyRouting=true \
  --set cgroup.autoMount.enabled=false \
  --set cgroup.hostRoot=/sys/fs/cgroup \
  --set securityContext.capabilities.ciliumAgent="{CHOWN,KILL,NET_ADMIN,NET_RAW,IPC_LOCK,SYS_ADMIN,SYS_RESOURCE,DAC_OVERRIDE,FOWNER,SETGID,SETUID}" \
  --set securityContext.capabilities.cleanCiliumState="{NET_ADMIN,SYS_ADMIN,SYS_RESOURCE}"
```

## Secrets

SOPS uses two public age recipients: Noel's personal key and the Flux cluster
key. Private keys are stored outside the repository and backed up separately.
The Flux private key is seeded into `flux-system/sops-age` once, out of band,
before Flux reconciles encrypted manifests.

Never paste `talosconfig`, the generated machine configuration, age private
keys, or decoded Kubernetes secrets into an issue, chat, or commit.

## Next platform layers

After Flux bootstrap, reconcile platform components in this order:

1. namespaces and Pod Security policy;
2. Cilium Helm values and network policies;
3. Longhorn with a dedicated data volume, single-node replica policy, and
   off-cluster backups;
4. observability and alerts;
5. Cloudflare Tunnel deployment using a dashboard-created tunnel token;
6. personal applications.

Longhorn is not ready to deploy while only `/dev/vda` is available as both the
Talos system disk and the only visible storage device. A separate data volume,
Talos `UserVolumeConfig`, kubelet `rshared` mount, and backup target must be
decided first.

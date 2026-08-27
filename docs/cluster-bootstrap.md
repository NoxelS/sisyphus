# Sisyphus cluster bootstrap

This document records the current one-node bootstrap and its intentional
boundaries. It is a recovery and handoff document, not a replacement for
version-pinned vendor documentation.

## Current state

Sisyphus currently runs as one schedulable control-plane node at Netcup. It is
not highly available: a node, disk, provider, or network failure takes the
cluster offline. Talos is the host operating system, Kubernetes is the
orchestrator, and Cilium is the active CNI. Flux has been bootstrapped and is
the in-cluster source of truth.

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
administrator's source address. Keep Talos TCP 50000 and Kubernetes TCP 6443
reachable only from that restricted address until private management is
available. Do not expose public origin ports: Cloudflare Tunnel does not
require inbound HTTP or HTTPS on the node.

## Kubernetes bootstrap

The machine configuration makes the sole control-plane node schedulable and
sets the CNI to `none` so Talos does not install Flannel. Kube-proxy remains
enabled for the first Cilium deployment. The sequence is:

1. Apply the final Talos machine configuration with `talosctl`.
2. Bootstrap the single etcd member exactly once.
3. Retrieve the kubeconfig.
4. Install Cilium once manually so the node can become Ready.
5. Bootstrap Flux once from a trusted workstation (completed).
6. Let the `cilium` Flux Kustomization adopt and manage the existing Helm
   release.

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

After bootstrap, Cilium is owned by Flux from `infrastructure/cilium`. The
HelmRelease preserves the bootstrap IPAM, kube-proxy, cgroup, routing, and
capability settings and additionally enables Hubble, Hubble Relay, and their
Prometheus metrics. Do not run ad-hoc Helm upgrades after Flux takes ownership.

Hubble Relay is cluster-internal. From a trusted workstation with the Cilium
and Hubble CLIs installed, inspect flows through a local port-forward:

```sh
cilium status
cilium hubble port-forward
hubble status
hubble observe
```

Do not publish Hubble Relay through Cloudflare, a NodePort, or a load balancer.

## Storage

The cluster currently uses Rancher's local-path provisioner v0.0.36. Its
`local-path` StorageClass writes to `/var/mnt/local-path` on this node, is not
the default class, and has `Retain` reclaim policy. PostgreSQL requests one
2 GiB `ReadWriteOnce` volume through that class. This is node-local storage:
it is neither replicated nor a backup. Preserve the PVC and copy data to an
off-node backup target before replacing or reinstalling the node.

Longhorn remains intentionally undeployed. Before introducing it, decide the
dedicated data disk, Talos mount/UserVolumeConfig, replica count appropriate
for the eventual node count, and backup target.

## Cloudflare Tunnel and applications

The `cloudflare-tunnel` Flux Kustomization deploys two `cloudflared` replicas
using a SOPS-encrypted tunnel token. The public hostname and service mapping
are configured in the Cloudflare dashboard. The tunnel connects outbound to
the cluster; no NodePort, LoadBalancer, Kubernetes Ingress, Gateway, Traefik,
or Caddy is required for Kite.

The `kite` Flux Kustomization waits for the storage Kustomization. Kite runs
one replica with anonymous users disabled, uses the standalone PostgreSQL
HelmRelease, and has its chart Ingress and Gateway disabled. The PostgreSQL
credentials and Kite encryption key are SOPS-encrypted and must never be
decoded into Git or logs.

The dashboard-managed tunnel should also map `grafana.noel.fyi` to the stable
in-cluster service `http://grafana.monitoring.svc.cluster.local:80`. Grafana is
the only observability UI intended for public routing. Prometheus, Alertmanager,
Loki, Alloy, and Hubble Relay remain cluster-internal. Protect Grafana with a
Cloudflare Access policy in addition to its generated administrator password.

## Observability

The `observability` Flux Kustomization depends on storage and Cilium and
installs pinned Prometheus community, Grafana community, and Grafana charts:

- kube-prometheus-stack with Prometheus, Alertmanager, Grafana,
  kube-state-metrics, and node-exporter;
- Loki in one-replica monolithic mode with filesystem storage;
- Grafana Alloy as a DaemonSet collecting Kubernetes container logs.

The `observability-config` Kustomization waits for `observability` so the
Prometheus Operator CRDs exist before ServiceMonitors, PodMonitors, and
PrometheusRules are applied. It also provisions the Loki data source, the
Sisyphus overview dashboard, initial platform alerts, and the monitoring
namespace Cilium policy.

Persistent observability data uses explicit `local-path` volumes:

- Prometheus: 20 GiB, seven-day time retention, 15 GiB size retention;
- Loki: 20 GiB, seven-day retention;
- Grafana: 2 GiB;
- Alertmanager: 1 GiB.

These volumes use `Retain`, but they remain on the single node and are neither
replicated nor backed up. Dashboards and data-source definitions belong in Git;
metrics and logs are disposable during node recovery unless an off-node backup
or remote storage target is added later.

Alertmanager initially routes to a null receiver so the stack can evaluate and
display alerts without embedding an undecided notification credential. Add a
SOPS-encrypted external receiver before treating alert delivery as operational.

Grafana credentials are stored only in
`infrastructure/observability/grafana-admin.sops.yaml`. To rotate the password,
edit that file with SOPS, let Flux reconcile it, and restart the Grafana
StatefulSet through a declarative rollout change or a controlled operational
restart. Never put the decoded value into Helm values or documentation.

## Network policy and firewall boundary

Cilium is the active CNI, but kube-proxy remains enabled and kube-proxy
replacement is disabled. No repository-managed default-deny Cilium policy or
Talos ingress-firewall configuration exists yet. The Netcup/provider firewall
is therefore the current public boundary. Keep public HTTP/HTTPS, NodePorts,
and cluster-internal ports blocked; allow only restricted administration and
stateful return traffic. Add explicit Cilium policies for `cloudflared`, Kite,
and PostgreSQL before claiming pod ingress or egress is restricted.

## Secrets

SOPS uses two public age recipients: Noel's personal key and the Flux cluster
key. Private keys are stored outside the repository and backed up separately.
The Flux private key is seeded into `flux-system/sops-age` once, out of band,
before Flux reconciles encrypted manifests.

Never paste `talosconfig`, the generated machine configuration, age private
keys, or decoded Kubernetes secrets into an issue, chat, or commit.

## Remaining platform work

Remaining work includes:

1. application-specific network policies informed by Hubble observations;
2. an external Alertmanager notification receiver;
3. off-node etcd, PostgreSQL, metrics, and log recovery arrangements;
4. Longhorn with a dedicated data volume, replica policy, and off-cluster
   backups.

Longhorn is not ready to deploy while only `/dev/vda` is available as both the
Talos system disk and the only visible storage device. A separate data volume,
Talos `UserVolumeConfig`, kubelet `rshared` mount, and backup target must be
decided first.

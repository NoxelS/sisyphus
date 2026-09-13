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
the default class, and has `Retain` reclaim policy. Kite PostgreSQL requests a
2 GiB `ReadWriteOnce` volume, LiteLLM PostgreSQL requests 5 GiB, and Faster
Whisper requests a 10 GiB model-cache volume through that class. This is
node-local storage: it is neither replicated nor a backup. Preserve the
database PVCs and copy their data to an off-node backup target before replacing
or reinstalling the node; the model cache can be downloaded again.

Longhorn remains intentionally undeployed. Before introducing it, decide the
dedicated data disk, Talos mount/UserVolumeConfig, replica count appropriate
for the eventual node count, and backup target.

## Cloudflare Tunnel and applications

The `cloudflare-tunnel` Flux Kustomization deploys two `cloudflared` replicas
using a SOPS-encrypted tunnel token. The public hostname and service mapping
are configured in the Cloudflare dashboard. The tunnel connects outbound to
the cluster; no NodePort, LoadBalancer, Kubernetes Ingress, Gateway, Traefik,
or Caddy is required for Kite or LiteLLM.

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

The dashboard-managed tunnel should map `malg.noel.fyi` to the frontend
service `http://frontend.malg.svc.cluster.local:80`. The frontend serves the
Malg UI and proxies its same-origin `/api/` requests to the cluster-internal
API service; do not expose the API separately.

Malg image automation scans the API/worker and frontend registries every five
minutes and opens updates on `flux/malg-image`. Review and merge that branch to
deploy a newer release through Flux.

The dashboard-managed tunnel should map `ai.noel.fyi` to
`http://litellm.litellm.svc.cluster.local:4000`. Only the proxy port belongs on
that route. LiteLLM authenticates API traffic, including `/metrics`, with its
master or virtual keys. If Cloudflare Access is added for the administrator UI,
scope it so it does not unintentionally block authenticated API clients.

## LiteLLM proxy

The `litellm` Flux Kustomization waits for storage and Cilium. It installs one
LiteLLM proxy worker, a standalone PostgreSQL database, standalone Redis, and a
CPU-only Faster Whisper server. The Git-owned `whisper-1` model routes
`/v1/audio/transcriptions` requests to the internal Faster Whisper service.
The Git-owned `qwen3.8-27b` model routes OpenAI-compatible chat requests to
Solheim over HTTPS and reads `SOLHEIM_API_KEY` from the `litellm-runtime`
Secret. Its LiteLLM metadata advertises a 262,144-token context window and
permits up to three concurrent upstream requests. LiteLLM stores model records
and records estimated request spend using Alibaba Cloud Model Studio's
international Qwen3.8-27B on-demand benchmark: $0.50 per million input tokens
and $3.00 per million output tokens. This is a comparable metered rate, not a
Solheim invoice: Solheim's Coder+ service is a flat €30/month plan with
unlimited fair-use tokens.
in PostgreSQL and includes prompt and response content in new spend-log records
so requests can be traced in the administrator UI. Treat these records and
database backups as sensitive data.

PostgreSQL persists LiteLLM users, keys, budgets, and spend records on a 5 GiB
`local-path` volume. Redis is password-protected but intentionally ephemeral:
it backs shared virtual-key authentication and opt-in response caching, and can
be rebuilt after a pod or node restart. Response caching has a ten-minute TTL
and `default_off` mode, so a caller must explicitly send
`"cache": {"use-cache": true}`. This avoids silently caching sensitive or
agentic requests while keeping the facility ready for suitable workloads.

The transcription backend uses the stable Speaches CPU image, which provides
an OpenAI-compatible API backed by Faster Whisper. It loads
`Systran/faster-whisper-small` with INT8 compute, four CPU threads, and one
worker. Its Hugging Face cache uses a retained 10 GiB `local-path` PVC. An
init container downloads that pinned Hugging Face model snapshot before the
server starts, and later pod restarts reuse it. The model cache is node-local, not
replicated, and safe to recreate by downloading the model again. The backend
API key is generated in the SOPS-encrypted
`faster-whisper-runtime` Secret and is shared only with the LiteLLM pod.
When rotating it, increment the Faster Whisper credential-revision pod
annotation in the LiteLLM HelmRelease so the proxy reloads the Secret value.

Clients call the public LiteLLM endpoint with a master or virtual key rather
than reaching Faster Whisper directly:

```sh
curl https://ai.noel.fyi/v1/audio/transcriptions \
  -H "Authorization: Bearer $LITELLM_API_KEY" \
  -F model=whisper-1 \
  -F file=@audio.mp3
```

The SOPS-encrypted `litellm-runtime` Secret contains a generated master key,
stable salt, generated administrator password, and an SMTP password
placeholder. It also supplies provider credentials referenced by the
Git-owned model configuration. Before enabling the Solheim-backed
`qwen3.8-27b` model or sending invitations, edit it from a trusted
workstation:

```sh
SOPS_EDITOR="$EDITOR" sops infrastructure/litellm/litellm-runtime.sops.yaml
```

Add a `stringData` mapping containing `SOLHEIM_API_KEY` with the plain Solheim
API key; SOPS encrypts that mapping before it reaches Git. Replace only
`SMTP_PASSWORD: REPLACE_WITH_PROTON_SMTP_TOKEN` with the Proton SMTP token for
`ai@noel.fyi` when invitation email is needed. Commit, push, and wait for Flux
to apply the Secret. Because an external Secret update does not alter the
Helm-rendered pod template, perform a controlled restart and wait for it to
finish:

```sh
kubectl rollout restart deployment/litellm -n litellm
kubectl rollout status deployment/litellm -n litellm
```

Keep `LITELLM_SALT_KEY` stable: changing it makes existing hashed credentials
unusable. The local administrator username is `admin`; obtain its generated
password through the same trusted SOPS workflow and do not use the master key
as an everyday client credential. Invite users with the least-privileged
suitable LiteLLM role and issue virtual keys with explicit model access,
budgets, and rate limits once models exist. Invitation and key-notification
emails do not include API key material; users retrieve keys from the
authenticated UI.

## Tailscale travel exit node

The `tailscale` Flux Kustomization installs the official Tailscale Kubernetes
Operator. Once its CustomResourceDefinitions are ready, the dependent
`tailscale-exit-node` Kustomization creates one `sisyphus-exit` Connector. The
Connector is a single pod that advertises itself as an exit node: a travel
device explicitly selecting it sends internet-bound traffic through the
cluster and exits through the server's Netcup public address.

Talos itself does not run Tailscale and its own traffic is unaffected. The
Connector intentionally has no subnet routes, Tailscale Ingress, Funnel,
cluster Egress service, or Kubernetes API proxy, so it is not a path into the
node or cluster workloads. The single-node cluster is not highly available;
the exit node is unavailable during node, CNI, operator, or provider outages.

Before Flux can authenticate the operator, edit
`infrastructure/tailscale/operator-oauth.sops.yaml` through SOPS and replace
both placeholders with a Tailscale OAuth client ID and secret. Create that
client with write access limited to `General/Services`, `Devices/Core`, and
`Keys/Auth Keys`, scoped to `tag:k8s-operator`.

The tailnet policy must define `tag:k8s-operator` and
`tag:sisyphus-exit`, let the operator own the Connector tag, auto-approve the
exit-node tag, and grant only the intended travel user or group access to
`autogroup:internet`. Do not grant that user access to `tag:sisyphus-exit`.
For example, merge the following into the existing tailnet policy, replacing
`group:travel@example.com` with the actual restricted group:

```jsonc
{
  "tagOwners": {
    "tag:k8s-operator": [],
    "tag:sisyphus-exit": ["tag:k8s-operator"],
  },
  "autoApprovers": {
    "exitNode": ["tag:sisyphus-exit"],
  },
  "grants": [
    {
      "src": ["group:travel@example.com"],
      "dst": ["autogroup:internet"],
      "ip": ["*"],
    },
  ],
}
```

After reconciliation, verify `kubectl -n tailscale get connector sisyphus-exit`
reports `ConnectorReady` and an exit node. Then select `sisyphus-exit` in the
Tailscale client on each travel device and verify its public IP has changed to
the Netcup server address. Selecting an exit node is explicit per client;
normal node and workload egress remains direct.

## Observability

The `observability` Flux Kustomization depends on storage and Cilium and
installs pinned Prometheus community, Grafana community, and Grafana charts:

- kube-prometheus-stack with Prometheus, Alertmanager, Grafana,
  kube-state-metrics, and node-exporter;
- Loki in one-replica monolithic mode with filesystem storage;
- Grafana Alloy as a DaemonSet collecting Kubernetes container logs.

The chart's scheduler, controller-manager, kube-proxy, and etcd ServiceMonitors
are disabled because those Talos-managed components do not expose their metrics
to the pod network in the current machine configuration. Kubernetes API,
kubelet, cAdvisor, node, Cilium, Hubble, Flux, cloudflared, and observability
component targets remain enabled.

The `observability-config` Kustomization waits for `observability` so the
Prometheus Operator CRDs exist before ServiceMonitors, PodMonitors, and
PrometheusRules are applied. It also provisions the Loki data source, the
Sisyphus overview dashboard, initial platform alerts, a cluster-internal
LiteLLM metrics scrape, and the monitoring namespace Cilium policy. The
ServiceMonitor runs in the `litellm` namespace, reads the master key from the
local SOPS-managed Secret, and uses it to authenticate `/metrics` scrapes.

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
replacement is disabled. There is no cluster-wide default-deny Cilium policy
or Talos ingress-firewall configuration. Workload-specific policies protect
LiteLLM, its PostgreSQL and Redis services, monitoring, and portfolio staging;
other workloads are not implicitly restricted. The LiteLLM proxy accepts
traffic from `cloudflared` and Prometheus on port 4000, reaches
only cluster DNS, its database and cache, Proton SMTP, and the cluster-internal
Faster Whisper service. Faster Whisper accepts only LiteLLM and host
health-check traffic and can reach Hugging Face over HTTPS to populate its
model cache. The Netcup/provider firewall remains the public origin boundary.
Keep public HTTP/HTTPS, NodePorts, and cluster-internal ports blocked; allow
only restricted administration and stateful return traffic.

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
3. off-node etcd, Kite and LiteLLM PostgreSQL, metrics, and log recovery
   arrangements;
4. Longhorn with a dedicated data volume, replica policy, and off-cluster
   backups.

Longhorn is not ready to deploy while only `/dev/vda` is available as both the
Talos system disk and the only visible storage device. A separate data volume,
Talos `UserVolumeConfig`, kubelet `rshared` mount, and backup target must be
decided first.

## Dependency and image update workflow

Renovate is intended to run as the GitHub App for this repository. It opens
reviewable pull requests for Helm chart versions, Kubernetes container images,
GitHub Actions, and Python tooling. Renovate must not receive cluster
credentials or SOPS private keys. Major upgrades remain manually approved from
the Renovate Dependency Dashboard; generated Flux manifests and encrypted
secret files are excluded.

Flux remains the deployment source of truth. The portfolio staging
`ImageUpdateAutomation` discovers new GHCR tags and pushes its setter commit to
`flux/portfolio-staging-image`. GitHub Actions opens or updates a pull request
from that branch into `main`. Flux reconciles the change only after the pull
request is reviewed and merged. Do not change this automation back to pushing
directly to `main`.

Before enabling this workflow, configure GitHub branch protection for `main` to
require pull requests and the infrastructure validation workflow, disallow
force pushes, and restrict the Flux deploy key to its automation branch.

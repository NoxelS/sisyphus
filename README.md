# Sisyphus

Single-node Talos/Kubernetes infrastructure managed with Flux.

The current cluster runs Flux-managed Cilium with Hubble Relay, Cloudflare
Tunnel, a Tailscale exit-node Connector, Kite, standalone PostgreSQL,
LiteLLM with dedicated PostgreSQL and Redis, Prometheus, Alertmanager,
Grafana, Loki, Grafana Alloy, a CPU-only Faster Whisper transcription service,
and node-local persistent storage through Rancher local-path.
It is not highly available and local-path volumes are not replicated or
backed up automatically.

See [the bootstrap and recovery guide](docs/cluster-bootstrap.md) for current
topology, ownership, storage, networking, firewall, and remaining-work
boundaries. Operational rules for agents and contributors are in
[`AGENTS.md`](AGENTS.md).

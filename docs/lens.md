# Lens

Lens is a separate service connected to LiteLLM. Its UI is accessed through the
LiteLLM dashboard at `https://ai.noel.fyi`; it uses LiteLLM's existing user and
team permissions. The Lens Service and ClickHouse are private to the cluster.
The configured ingestion address is `http://lens.lens.svc.cluster.local:4318`
and is reachable only from explicitly permitted cluster workloads. Agent
instrumentation, tracing keys, and any externally reachable ingestion route
are separate follow-up work.

Lens and ClickHouse run on the single Sisyphus node. ClickHouse uses a 20 GiB
`local-path` volume with a 14-day retention setting. This volume is node-local,
not replicated or a backup. Its PersistentVolume and claim are retained on
removal. Trace contents may include prompts, responses, and other sensitive
data; restrict access and treat backups accordingly.

The Lens admin token, ClickHouse password, and LiteLLM connection credentials
are SOPS-encrypted in Git. The chart's generated ClickHouse Secret is removed
by a Helm post-renderer so Flux owns the encrypted Secret. The two connection
Secrets contain the same values in the `lens` and `litellm` namespaces; update
both together if rotating them. Keep the secrets with the ClickHouse backup
for recovery. Before an upgrade or node replacement, back up ClickHouse data
and verify the recovery procedure; retaining the volume is not a backup.

The Lens chart is pinned by version and digest. Review its rendered resources,
network policies, storage compatibility, and upgrade instructions before
updating it. Check the HelmRelease, ClickHouse StatefulSet, Lens Deployment,
and LiteLLM connection after Flux reconciles.

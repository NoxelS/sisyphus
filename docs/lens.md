# Lens

Lens is a separate service connected to LiteLLM. Its standalone UI and public
tracing endpoint use `https://lens.noel.fyi`. In the Cloudflare dashboard, add a
public hostname for `lens.noel.fyi` to the existing tunnel, targeting
`http://lens.lens.svc.cluster.local:4318`. The Lens network policy allows the
`cloudflared` pods to reach that Service. The hostname is not reachable until
that route is configured and DNS is active.

LiteLLM connects to Lens privately at `http://lens.lens.svc.cluster.local:4318`.
The same address can be used for in-cluster trace ingestion by workloads allowed
by the Lens network policy. External agents use `https://lens.noel.fyi/v1/traces`
with a dedicated tracing key. The public route makes Lens's authenticated UI and
trace ingestion endpoint reachable; protect administrator credentials and tracing
keys, and configure Cloudflare access controls as appropriate.

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

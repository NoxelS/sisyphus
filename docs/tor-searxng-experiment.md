# Tor-backed SearXNG search

Production MALG uses `MALG_SEARCH__URL=http://searxng-tor:8080`. Workers
call the Tor-backed SearXNG service, which uses the `socks5h` outgoing proxy
at `tor-socks:9050` so engine hostnames are resolved by Tor. The direct
`searxng` service remains available for evaluation and explicit rollback.
This routes search-engine requests through Tor; MALG page fetching and the
Lightpanda browser keep their existing network paths.

Flux reconciles the services through the independent `searxng-tor`
Kustomization, which depends on MALG for its namespace and shared SearXNG
secret. This dependency remains in that direction to avoid a Flux dependency
cycle. On a fresh installation, MALG can become healthy before Tor is ready;
search requests require the Tor stack to be ready. Tor outages affect
production search without necessarily failing MALG's health checks. There is
no automatic fallback to the direct search service.

The API and worker pod templates carry a search-endpoint annotation so this
migration triggers rolling replacements and refreshes ConfigMap environment
variables. Keep that annotation in sync when changing the endpoint again.

## Network boundaries

- Production workers and the evaluation Job can call `searxng-tor:8080`;
  there is no public or cross-namespace route. Worker search egress permits
  the Tor-backed instance on TCP 8080, rather than the direct instance.
- The Tor-backed SearXNG pod can egress only to the Tor SOCKS service on TCP
  9050 and to CoreDNS for the exact `tor-socks.malg.svc.cluster.local.` name.
  Direct HTTP/HTTPS egress and arbitrary DNS queries are denied by Cilium.
- The Tor proxy accepts SOCKS connections only from the Tor-backed SearXNG
  pod. Its egress is limited to TCP 443 and 9001, the common Tor relay ORPorts.
  `ReachableAddresses` in the mounted `torrc` tells Tor to select reachable
  guards and directory connections within that same allowlist. Relays using
  other ORPorts are excluded. Kustomize hashes the Tor ConfigMap so changes
  roll out a new proxy pod.
- Tor bootstrap readiness gates the proxy pod's readiness. If Tor is
  unavailable, the SOCKS endpoint is not ready and SearXNG has no direct
  network path to fall back to.

## Evaluation and rollback

For the comparison tracked in [MALG issue #55](https://github.com/NoxelS/malg/issues/55),
use the dedicated `searxng-evaluation` Flux Kustomization. It is suspended by
default, so merging this deployment does not start searches. Its finite Job
calls both endpoints with the same queries, alternating their order. It has
its own explicit `MALG_SEARCH__URL`, no production runtime ConfigMap or
credentials, and no access to the MALG job queue. Production workers use
the Tor-backed endpoint independently of the evaluation Job.

1. Complete the runtime network checks below. Agree on representative,
   non-sensitive queries with MALG and replace
   `infrastructure/searxng-evaluation/queries.txt`. The provided queries are
   examples, not a validated research cohort.
2. Review `EVALUATION_REPEATS` in `job.yaml`. Five queries repeated ten times
   produce 50 requests per endpoint, 100 total. The runner rejects workloads
   above 200 total requests, sends requests serially with a five-second gap,
   and never retries or substitutes the direct endpoint for a failed Tor
   request. The Job has a two-hour deadline and no restart retries. Avoid
   multiple runs exceeding the agreed daily budget.
3. Commit a unique Job name for every run or workload change (initially
   `searxng-evaluation-001`) and set `spec.suspend: false` in
   `clusters/sisyphus/searxng-evaluation.yaml`. Flux creates the Job after
   `searxng-tor` is ready. A new name avoids immutable Job template updates.
4. Save the Job's JSON-lines logs before another run: for example,
   `kubectl -n malg logs job/searxng-evaluation-001`. Logs include endpoint
   URLs, workload hash, query index, result counts, latency, and engine errors.
   They omit query text and result content. Compare these measurements with
   MALG's normalization and research outcomes separately; this Job measures
   search responses, not full account-research quality.
5. After completion, commit `spec.suspend: true` on the evaluation Flux
   Kustomization. Completed Jobs remain available for log collection and do
   not run again on ordinary reconciliations. Suspension prevents subsequent
   source changes from starting another run; it does not stop an active Job.

To stop an active evaluation, remove `searxng-evaluation.yaml` from
`clusters/sisyphus/kustomization.yaml`; Flux prunes that Kustomization and its
Job and policies. Archive evaluation logs before removal.

To roll production search back,
restore `MALG_SEARCH__URL=http://searxng:8080`, change the API and worker
search-endpoint annotations to that URL, and restore the worker egress
selector to `app.kubernetes.io/instance: searxng` in Git. After Flux
reconciliation, verify every replacement worker has the direct URL and can
search successfully. Only then remove `searxng-tor.yaml` from the cluster root
if retiring Tor; Flux prunes its services and deployments. All workloads
still share the single node and the direct comparison adds bounded load to
the existing search service.

Tor can be slower or less available, does not guarantee fewer CAPTCHAs or
anonymity, and destination services still receive the query contents.

## Runtime proof before using the endpoint

After merge and Flux reconciliation, confirm all of the following before
relying on production search or enabling the evaluation Job:

1. `torproxy` becomes Ready only after `/ip` reports a Tor exit address.
2. The Tor-backed SearXNG pod can reach the SOCKS service and return JSON
   search results.
3. A request through `searxng-tor` reports a Tor exit address when checked with
   a dedicated diagnostic request routed through the same SearXNG proxy.
4. With the Tor proxy unavailable, a request fails and Cilium/Hubble shows no
   direct egress or external DNS query from the SearXNG pod.
5. A negative test to an unrelated DNS name and direct TCP/443 from the
   SearXNG pod is denied.

Also verify `MALG_SEARCH__URL` in the API and every worker container, then
perform a search from a worker and confirm it returns JSON results through
`searxng-tor`. Check worker-to-search traffic with Cilium/Hubble. A healthy
MALG API alone does not prove search availability.

Static manifest validation does not prove these live network properties.

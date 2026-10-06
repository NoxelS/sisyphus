# Tor-backed SearXNG experiment

The MALG namespace contains an optional `searxng-tor` service alongside the
existing direct `searxng` service. The experimental instance uses SearXNG's
`socks5h` outgoing proxy setting so engine hostnames are resolved by Tor. The
existing `searxng` deployment and endpoint remain the default.

## Network boundaries

- Only MALG worker pods can call `searxng-tor:8080`; there is no public or
  cross-namespace route.
- The Tor-backed SearXNG pod can egress only to the Tor SOCKS service on TCP
  9050 and to CoreDNS for the exact `tor-socks.malg.svc.cluster.local.` name.
  Direct HTTP/HTTPS egress and arbitrary DNS queries are denied by Cilium.
- The Tor proxy accepts SOCKS connections only from the Tor-backed SearXNG
  pod. Its egress is limited to TCP 443 and 9001, the common Tor relay ORPorts.
  Relays using other ORPorts will be unreachable until the allowlist is
  deliberately adjusted.
- Tor bootstrap readiness gates the proxy pod's readiness. If Tor is
  unavailable, the SOCKS endpoint is not ready and SearXNG has no direct
  network path to fall back to.

## Evaluation and rollback

For the comparison tracked in [MALG issue #55](https://github.com/NoxelS/malg/issues/55),
set `MALG_SEARCH__URL` in MALG's runtime ConfigMap to
`http://searxng-tor:8080` for a bounded test cohort, then restore
`http://searxng:8080` after the run. Capture the chosen URL and compare
representative queries, successful result counts, latency, and engine errors.
Keep the initial run near the issue's few-hundred-searches-per-day target. Tor
can be slower or less available, does not guarantee fewer CAPTCHAs or
anonymity, and destination services still receive the query contents.

To disable the experiment, remove the `searxng-tor-*` and `torproxy-*`
resources from `infrastructure/malg/kustomization.yaml` and remove the
`searxng-tor` worker egress entry from `infrastructure/malg/network-policy.yaml`.
Flux prunes the removed resources. The original direct SearXNG service remains
available throughout.

## Runtime proof before using the endpoint

After Flux reconciles the branch, confirm all of the following before directing
MALG traffic to the experimental URL:

1. `torproxy` becomes Ready only after `/ip` reports a Tor exit address.
2. The Tor-backed SearXNG pod can reach the SOCKS service and return JSON
   search results.
3. A request through `searxng-tor` reports a Tor exit address when checked with
   a dedicated diagnostic request routed through the same SearXNG proxy.
4. With the Tor proxy unavailable, a request fails and Cilium/Hubble shows no
   direct egress or external DNS query from the SearXNG pod.
5. A negative test to an unrelated DNS name and direct TCP/443 from the
   SearXNG pod is denied.

Static manifest validation does not prove these live network properties.

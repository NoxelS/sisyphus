# Portfolio production deployment

The portfolio repository builds and tests production images after a PR is merged
into its production branch. Sisyphus deploys those existing images; it does not
rebuild the website.

## Resources and release flow

Production runs in the portfolio-production namespace. Its ClusterIP Service
exposes port 80 and targets the frontend on port 4321. The deployment uses one
replica on the existing single node, a rolling update with no unavailable pods,
health probes, and a non-root container with a read-only filesystem and /tmp
scratch space. This does not provide node-level high availability.

The portfolio-frontend ImageRepository in flux-system is shared with staging
and remains declared in infrastructure/portfolio-staging/image-automation.yaml.
Keep that shared scanner when reorganizing or removing staging.

The production ImagePolicy accepts only bare stable X.Y.Z tags >=0.0.1.
Staging, prerelease, SHA, moving tags, and duplicate vX.Y.Z aliases are excluded.
IfNotPresent digest reflection keeps each selected release pinned to its digest.
The initial deployment is the published 0.0.1 release.

1. Portfolio CI publishes a new tested stable image to GHCR.
2. Flux selects the highest eligible version and its digest.
3. ImageUpdateAutomation updates only infrastructure/portfolio-production and
   pushes flux/portfolio-production-image. Scanning and automation use five-minute
   intervals, so updates are asynchronous.
4. The existing scoped GitHub App opens a Sisyphus image PR, refreshes it against
   current main when necessary, and enables auto-merge after static-checks passes.
5. Flux reconciles the merged digest-pinned deployment and Kubernetes performs
   the rolling update.

Renovate ignores production deployment and image automation manifests so Flux
owns those image references. CI checks stable image tags, digest pinning,
production configuration, and the production-only automation path and branch.

## Public hostname: configure separately in Cloudflare

After the infrastructure PR is merged and the workload is healthy, add the
published application route to the existing Cloudflare Tunnel:

- Hostname: noel.fyi
- Service URL: http://portfolio-production.portfolio-production.svc.cluster.local:80

Check the existing DNS record for noel.fyi when adding the route. The hostname
route and DNS are managed in Cloudflare's dashboard, outside this repository.
The Cilium policy allows the existing cloudflared pods to reach port 4321;
no Kubernetes Ingress or public origin port is required.

## Verification after merge

Check that the production Kustomization, ImagePolicy, and ImageUpdateAutomation
are Ready, the deployment is Available, and the running imageID matches the
digest selected in Git. Then verify https://noel.fyi/health reports the expected
version and commit, both language homepages work, and robots.txt and the sitemap
have production indexing enabled.

## Rollback

1. In a Sisyphus PR, set spec.suspend: true on the production
   ImageUpdateAutomation and restore the previous stable image tag and digest
   in the production deployment. Keep the Flux image policy marker.
2. Merge the PR and let Flux reconcile it. Do not suspend the production
   Kustomization: it must still apply the rollback.
3. Verify the running image digest and health endpoint.
4. Leave image automation suspended until a fixed production release is
   published. Remove the suspension through Git to resume automatic updates.

Reverting only the image while leaving automation active will cause Flux to
select the newer version again.

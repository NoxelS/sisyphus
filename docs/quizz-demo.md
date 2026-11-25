# Quiz demo deployment

The quiz runs in the `quizz-demo` namespace behind the existing Cloudflare tunnel, with a dedicated PostgreSQL 16 instance and a 2 GiB `local-path` PVC. Flux reconciles the application and its dedicated database. The application image is pinned to a published immutable digest.

The PostgreSQL container applies the checked-in SQL files in `/docker-entrypoint-initdb.d` only when initializing an empty database. Changes to those files do not migrate an existing volume. Back up and explicitly apply future schema migrations before deploying code that requires them. The retained local-path volume is on one node and is not replicated; node loss requires restoring a verified PostgreSQL backup. Backups contain personal and health-related responses and must be protected. Establish and test a backup and restore procedure before collecting important data.

The encrypted Secret holds the database password, connection URL, and Calendly signing key. Do not log or commit decrypted values. Rotating the password in the Secret alone does not change the existing PostgreSQL role password; coordinate rotation in PostgreSQL and the Secret.

After confirming both workloads and `/api/ready` are healthy, in the Cloudflare dashboard, add `quizz-demo.noel.fyi` to the existing Sisyphus tunnel with origin `http://quizz-demo.quizz-demo.svc.cluster.local:80` and a proxied CNAME to the tunnel. Verify HTTPS, the booking link, and `/robots.txt` through the public hostname. Then create a user-scoped Calendly webhook for `invitee.created` and `invitee.canceled` at `https://quizz-demo.noel.fyi/api/calendly`, using the encrypted Secret signing key. Check existing subscriptions first and verify a signed delivery and database persistence. The user-scoped subscription can include events for other event types on the same Calendly account; the application does not link events to quiz leads automatically.

`robots.txt` asks compliant crawlers not to index the site; it does not restrict access. The demo stores contact and health-related responses, so assess the privacy notice, retention and deletion processes before sharing it broadly.

# ERP adapter for CMC CaaS

This chart deploys Core, API and static Web UI only. PostgreSQL, RabbitMQ and
Elasticsearch remain external services. No credentials or Google API runtime key
are created by the chart.

## Runtime contract

Use images built from the reviewed Dockerfiles and pin all three images by digest.
Core loads /opt/metasfresh/metasfresh.properties; API loads
/opt/metasfresh-webui-api/metasfresh.properties. Both mount the existing Secret
configured by runtime.propertiesSecret.name/key. Its complete connection file
must target the PostgreSQL WRITE primary with a dedicated ERP login role. Keep
the RDS administrative SUPERUSER out of application runtime configuration.

Rabbit credentials use an existing Secret with username/password keys, referenced
through SPRING_RABBITMQ_* environment variables. Endpoint values use the same
Spring properties as the source Compose file. The Web UI mounts a ConfigMap at
/usr/share/nginx/html/config.js; nginx does not substitute API_URL/WS_URL env vars.

The current infrastructure inventory records:
- PostgreSQL 18 primary 10.60.40.113; read replicas .126 and .45 are not write targets.
- RabbitMQ 3.7.4 at 10.60.40.104:5672.
- Elasticsearch 7.9.3 at 10.60.40.30:9200.
- Harbor HTTPS 10.60.50.30 using an internal CA.

These observed versions do not establish compatibility with metasfresh 5.175 or
a newly built source revision. Database initialization/migrations and ERP smoke
tests against the exact image digests must pass before real deployment. This
chart does not implement managed PostgreSQL promotion, routing or failover.

searchTransportPort defaults to 0 because the current VM publishes only HTTP9200.
Enable transport9300 only if the exact image contract requires it and the VM, SG
and chart NetworkPolicy have been configured and tested accordingly.

## PostgreSQL TLS

Newly built ERP images include optional JVM properties metasfresh.db.sslmode and
metasfresh.db.sslrootcert. The JDBC URL builder validates verify-full/verify-ca,
requires an absolute CA path and URL-encodes it. Weak TLS modes are rejected.
Generic/local defaults leave these properties absent to preserve local behavior.

Strict CMC values require databaseTLS.enabled=true and mode=verify-full. The chart
mounts the existing erp-postgres-ca Secret key ca.crt at
/etc/metasfresh/db-ca/ca.crt and passes both JVM properties. This is JDBC
configuration; libpq PGSSLMODE environment variables do not configure this datasource.
Old/upstream images lack this code change and must not be presumed to honor it.

Obtain the real RDS CA and verify the server certificate SAN matches the configured
pgHost. If the certificate names a DNS endpoint instead of primary IP10.60.40.113,
use that verified provider endpoint after confirming it resolves to the write
primary. Do not weaken verification to make an IP mismatch pass. Helm rendering
does not establish a TLS handshake or validate the CA contents.

## Environment and bootstrap requirements

The generic values.yaml renders with validation.strict=false for static inspection.
Set validation.strict=true in the CMC environment. Strict mode requires actual
DATA endpoints and /32 CIDRs, APP node placement, verified Harbor digests, pull
Secrets, a real Ingress domain and an explicit CSI StorageClass (or existing PVCs).
The environment must also configure the actual HAProxy pod labels in
networkPolicy.ingressController.podSelector.

The current APP pool label is:
cmc-cloud-k8s-nodegroups=nodegroup-gd6u

Install the Ingress controller, Sealed Secrets and CSI first. Argo must create
namespace erp before synchronizing the chart. Distribute the Harbor CA to the
container runtime on EVERY worker; a Docker CA installation on one VM does not
establish containerd trust on CaaS workers. Provision harbor-pull and erp-origin-tls
inside erp, and wait for all SealedSecret resources to produce their Secrets.

NetworkPolicy is owned by this chart. Do not apply legacy standalone allow-erp
policies alongside it: allow policies are additive. Existing old policies need a
reviewed cleanup during migration. Core/API DATA egress rules pair each VM /32
with its own port. Browser API and SockJS requests go Ingress -> API; the static
Web UI pod does not need direct outbound API access.

## Storage, probes and operations

Core/API each use one RWO volume and a Recreate Deployment strategy. This is
intentional downtime during an upgrade, not application HA. Their PVCs carry
Argo Prune=false,Delete=false protection. Confirm the StorageClass can reattach
across the actual APP workers; all currently recorded APP workers are in AZ1.

Core storage mounts /opt/metasfresh/data. API email attachments are explicitly
stored under /var/lib/metasfresh/api/email-attachments on its own PVC. Other
archive/attachment backends are configured inside metasfresh/database settings:
verify their actual paths before claiming all business files are durable.
Velero backup-volume annotations identify both business-data volumes, but they
do not install Velero or prove a completed backup.

Startup/readiness checks use the /health endpoints shown in source Compose.
Java liveness uses a TCP check so a DB outage does not create a restart loop.
Verify these checks return the expected results with the pinned images.
ServiceAccount token automount is disabled; ERP does not need Kubernetes API RBAC.
User/operator RBAC belongs to the separate administrative bootstrap manifests.

Secret mounts use subPath. Rotate the Secret, then change
runtime.propertiesSecret.revision to restart Core/API. Neither Git nor artifacts
should contain the unsealed connection file, Rabbit password or registry token.

HPA defaults off during bootstrap. If enabled, the chart omits UI replicas so HPA
owns scaling; metrics-server and Argo ignoreDifferences for replicas are required.
UI's PDB minAvailable=1 and topology spread protect planned disruptions with at
least two schedulable APP nodes. API/Core remain single replicas.

Optional metrics.enabled creates private metric Services and monitoring ingress
rules. Verify the exact image's endpoint, authorization and path before enabling
it; exposing a Service does not add a Prometheus registry to the application.

## Minimum acceptance

1. All selected APP nodes Ready; Harbor CA trusted and digest image pulls succeed.
2. Required Secret keys exist; PG write-role connectivity, Rabbit and Search healthy.
3. Schema/migration compatibility proven against PostgreSQL18 and the pinned images.
4. Both PVCs Bound; startup/readiness probes pass with real dependency connections.
5. UI loads the expected config.js; login, API, /stomp and order creation succeed.
6. Denied traffic remains denied; approved DATA traffic uses the correct VM/port.
7. Business files survive pod recreation; backup/restore evidence covers DB and files.
8. Release promotes the same tested digests through the separate GitOps repository.

No cluster changes or secret values are validated by Helm rendering alone.

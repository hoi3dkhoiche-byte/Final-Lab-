# CMC managed PostgreSQL18

Confirmed2026-10-08: rds_postgres-erp, instance34257c3b-46b2-4eef-befb-12e6cd59734e,
PostgreSQL18 Master Slave, three managed nodes in subnet-data.

| Endpoint | Role | Runtime use |
| --- | --- | --- |
| 10.60.40.113:5432 | write | Core/API and backup |
| 10.60.40.126:5432 | read replica | Optional approved reporting only |
| 10.60.40.45:5432 | read replica | Optional approved reporting only |

There is no shared failover endpoint. This chart does not load-balance queries
or automatically switch primary. Obtain CMC's promotion/write-endpoint contract;
if the primary changes, review/update site.yaml, runtime properties,
NetworkPolicy and SG. Test the change before resuming writes.

This is managed RDS: do not SSH to the database IPs, install node_exporter on
them, or manage their peer replication SG as ordinary VMs. Use provider
monitoring or a read-only PostgreSQL exporter in MON querying5432.

## Compatibility and initialization

The upstream installation documentation for metasfresh5.175 specifies PostgreSQL15.
That is a documented baseline, not proof that PostgreSQL18 is unsupported or
supported. The actual images built from this repository must be tested with RDS18
and the intended seed/migration release. No automatic downgrade/upgrade is
performed by this preparation.

Use an EMPTY isolated lab_seed_* database. Restore a verified custom-format seed,
apply the exact release migration through its supplied migration procedure, then
test login, order creation, Rabbit event flow, Search, /stomp and attachments.
Inventory required extensions, schema owners, functions and grants before import;
managed RDS may limit administrative capabilities despite the visible role flags.
Confirm required extensions with CMC. Never import arbitrary SQL directly into
the ERP database to discover whether it works.

Record the tested three image digests, seed and migration SHA256 and results in
an acceptance JSON matching environments/lab/acceptance.json.example. Set
postgres.compatibilityEvidence to the reviewed report path, and require
scripts/preflight.py --require-acceptance before syncing the real ERP application.
A self-written true flag without actual test evidence is not acceptance.

## Accounts

Current admin is a privileged administrative account. Create a dedicated ERP
login/owner for the intended database; grant only schema/data permissions needed
by runtime. Use a separate migration account if release scripts need DDL.
Use a separate dump user with CONNECT, USAGE and read privileges on all required
objects, sequences and large objects. pg_read_all_data does not automatically
bypass row-level security; check actual RLS/large-object coverage.

Backup and restore users are different GitHub environment Secrets. Isolated
restore/seed roles may own their lab_* targets, but must not be able to overwrite
the real ERP database. Assign privileges as reviewed by the RDS administrator;
do not grant SUPERUSER/BYPASSRLS merely to make a workflow pass.

## Connection Secret and TLS

Start from the complete properties file shipped with the PINNED images/source.
Replace its CConnection DBhost, DBport, DBname, UID and PWD using the real ERP role;
AppsHost refers to metasfresh-core, not the PostgreSQL read replica.
Store the complete file privately and seal it as erp-properties. Do not paste
RDS admin credentials into the Git repository or GitHub logs.

The CConnection serialized syntax is not a plain JDBC URL: escaping Java
properties alone is insufficient if a password contains its attribute delimiters.
Use the application's supported connection serialization/configuration process
and validate the final file with the pinned image. The prepared backend adds opt-in metasfresh.db.sslmode and metasfresh.db.sslrootcert
system properties. The CMC overlay enables verify-full and mounts erp-postgres-ca,
key ca.crt, at /etc/metasfresh/db-ca/ca.crt. Rebuild and test all runtime images
from this patched source; upstream images do not automatically contain this change.
Verify the RDS CA/certificate SAN with the exact built images in staging.

Runner operations use PGSSLMODE=verify-full with a trusted public RDS CA and a
matching IP/DNS SAN. The currently supplied primary IP must be verified against
the provider certificate; use the supported write hostname if needed.

## Backup and restore

The workflow uses logical custom-format pg_dump with SHA256, S3 upload and
re-download verification. It now checks PG_EXPECTED_MAJOR and refuses a client
older than the server before dumping/restoring. Install PostgreSQL18 clients on
the self-hosted runner. Keep native CMC RDS backups/PITR according to provider
capability; logical dumps are a separate layer.

Restore/seed is restricted to EMPTY lab_restore_* or lab_seed_* databases and
checks the trusted SHA256. It does not perform an in-place production restore.
Roles/grants, large objects, extensions and business correctness need validation
after import. Preserve consistency between database state and Velero file backup
by pausing ERP writes during the agreed lab backup window.

Sources: [metasfresh5.175/PostgreSQL15 upgrade guide](https://docs.metasfresh.org/installation_collection/EN/update_release_5.175_incl_upgrade_to_postgres_v15.html),
[PostgreSQL18 pg_dump version limits](https://www.postgresql.org/docs/18/app-pgdump.html).

The xyz prefix in this connection file is a cleartext serialization marker, not
encryption. The current password parser stops at the first closing square bracket;
validate this delimiter before provisioning the dedicated role/connection file.
Required source migrations reference uuid-ossp, tablefunc, pg_trgm and fuzzystrmatch;
confirm their availability and installation permissions on managed RDS before import.

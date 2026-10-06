# CMC LAB Actions

Five active workflows: Build and Push Docker Images; Deploy to Kubernetes - App
Cluster (GitOps); Database Backup; Database Restore; Seed Database.
Upstream workflows are archived in ci/upstream-workflows. Manage Cluster and CDN
sync are omitted: bootstrap platform through Bastion and there is no CDN requirement.
The existing sample private key is intentionally preserved; secret scan still fails
on findings. No exclusion, ignore rule or success override is added.

## Build and promotion

Build variables: HARBOR_REGISTRY (TLS hostname without scheme), HARBOR_PROJECT.
Secrets: CI_HARBOR_USERNAME, CI_HARBOR_TOKEN, METASFRESH_PACKAGES_READ_TOKEN.
Dedicated Linux runner labels: self-hosted, linux, cmc-lab. Install Docker/buildx,
Trivy, compatible PostgreSQL clients, AWS CLI, Python, Helm and gh.
Pin tool versions in runner inventory. Trusted main builds only; protect main.
Build tests source, scans every image before any push and uploads release metadata.
Deploy is manual: provide a successful main build_run_id. It verifies provenance
and registry settings and opens a PR; it never merges or kubectl applies workloads.
Argo CD deploys approved GitOps state. Configure protected GitOps branches/review.

Deploy variables: GITOPS_REPOSITORY (owner/repository), GITOPS_BASE_BRANCH (main).
Secret: GITOPS_TOKEN (GitOps contents + PR). Use environment lab-release with required
reviewers. Copy updated chart templates/default values to the actual GitOps repo.
Argo must use charts/erp-adapter/values.yaml without overriding image values.
Resolve the sample Application repo URL and absent environment values at bootstrap.
Migration needs backup, version guard and a single executor; digest rollback does
not undo database schema changes. Validate login/order flow and PG/seed compatibility.

## Database operations

All three run manually; no live operations were executed while creating workflows.
Environments: lab-backup, lab-restore, lab-seed. Restrict deployments to main and set
reviewers. Variables: PGHOST, PGPORT (5432), PGDATABASE (production guard), PGSSLMODE
(verify-full), PGSSLROOTCERT (runner CA file path), S3_ENDPOINT (HTTPS), BACKUP_BUCKET,
S3_REGION. Secrets: PGUSER, PGPASSWORD, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY.
Use different least-privilege credentials per operation and private connectivity.
Configure AWS CLI path-style if required by the tested CMC S3 endpoint.

Backup uses custom pg_dump, a SHA256 sidecar and re-download verification. Copy the
object key/checksum from run summary. Add daily scheduling after a restore rehearsal.
It covers DB only; coordinate external ERP file backups at the same business point.
Restore/seed require a pre-created EMPTY lab_restore_* or lab_seed_* DB, a private
S3 dump object key and trusted checksum. No DROP, --clean or in-place production
restore. Stop other writers/notifications; import is a single transaction.
Prepare compatible extensions/encoding and roles first. Roles/grants are not restored
by the script: validate ownership, permissions, sequences, orders and files afterwards.
Full DR also needs dependencies, secrets and Velero recovery and measured RPO/RTO.

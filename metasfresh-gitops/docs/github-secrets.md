# GitHub configuration for Final-Lab-

Set these on hoi3dkhoiche-byte/Final-Lab-, not by committing a .env file.
Secrets are named here; their values must come from the actual systems.
No GOOGLE_API_KEY or runtime Google key is required.

## Repository variables

| Variable | Value / requirement |
| --- | --- |
| HARBOR_REGISTRY | 10.60.50.30 (no https:// prefix) |
| HARBOR_PROJECT | metasfresh; create this private Harbor project first |
| GITOPS_REPOSITORY | hoi3dkhoiche-byte/GitOps-final-lab |
| GITOPS_BASE_BRANCH | main; initialize that branch and copy the GitOps files first |
| PGHOST | 10.60.40.113, write primary; update after managed failover |
| PGPORT | 5432 |
| PG_EXPECTED_MAJOR | 18 |
| PGDATABASE | Real ERP database name, still required |
| PGSSLMODE | verify-full |
| PGSSLROOTCERT | Absolute public RDS CA path on the Linux self-hosted runner |
| S3_ENDPOINT | Real S3-compatible HTTPS endpoint, still required |
| S3_REGION | Provider region, still required |
| BACKUP_BUCKET | Separate PostgreSQL dump bucket, still required |

If the RDS certificate does not contain the primary IP in its SAN, obtain the
provider hostname with a valid SAN and supported write routing before enabling
verify-full. Do not select a read replica or change to sslmode=disable.

## Secrets and environments

| Secret | Purpose | Location |
| --- | --- | --- |
| CI_HARBOR_USERNAME | Build/push robot for the metasfresh project | Repository |
| CI_HARBOR_TOKEN | Robot credential; push/pull only in that project | Repository |
| METASFRESH_PACKAGES_READ_TOKEN | Read private upstream Maven packages needed by this source build | Repository |
| GITOPS_TOKEN | Fine-grained token limited to GitOps-final-lab: Contents and Pull requests write | lab-release environment |
| PGUSER | Dedicated dump user for backup; separate isolated import role for restore/seed | lab-backup / lab-restore / lab-seed environments |
| PGPASSWORD | Credential corresponding to each environment PGUSER | Same environment |
| S3_ACCESS_KEY_ID | Bucket-scoped object-store key | Database operation environments |
| S3_SECRET_ACCESS_KEY | Corresponding object-store secret | Same environment |

Build secrets do not belong in PR validation jobs. Keep self-hosted runner jobs
restricted to trusted main; run untrusted pull request checks on GitHub-hosted
runners. GitOps promotion opens a review PR and does not use a production
kubeconfig. Environment protections for release/restore are configured in the
GitHub UI according to the lab approval policy and account feature availability.

Argo CD needs a DIFFERENT read-only Git credential if GitOps-final-lab is private.
Provide that Kubernetes Secret privately in argocd with
argocd.argoproj.io/secret-type=repository, type=git, url, username, password.
Do not give Argo the CI token with repository write access.

## Kubernetes Secrets

| Namespace | Secret | Required keys / type |
| --- | --- | --- |
| erp | erp-postgres-ca | ca.crt; Opaque, public RDS CA used by JDBC verify-full |
| erp | erp-properties | metasfresh.properties; Opaque, complete dedicated-user connection file |
| erp | erp-rabbit-secret | username, password; Opaque |
| erp | harbor-pull | .dockerconfigjson; kubernetes.io/dockerconfigjson, pull-only robot |
| erp | erp-origin-tls | tls.crt, tls.key; kubernetes.io/tls |
| monitoring | grafana-admin | admin-user, admin-password; Opaque |
| monitoring | alertmanager-config | alertmanager.yaml; Opaque |
| velero | s3-velero | cloud; Opaque, standard AWS credentials file |
| velero | velero-repo-credentials | repository-password; Opaque, keep for all future restores |

Registry Secrets are per namespace. Create harbor-pull in each namespace that
uses private mirrored images, and configure that release's imagePullSecrets.
Harbor public CA trust is independent from registry credentials.

Use scripts/seal_secret.py with private input files and the controller's PUBLIC
certificate. It pipes the Secret in memory to kubeseal using strict namespace/name
scope and writes encrypted output only. Do not store an unsealed YAML, a key,
a password in a --from-literal command, or the controller's private sealing key
in Git. Back up the controller sealing keys separately with restricted access.

Example from the GitOps repository root (Linux/Bastion):

```bash
mkdir -p private-inputs sealed-secrets/erp
# Put real private files in private-inputs via your secure administrative process.
kubeseal --fetch-cert --controller-name sealed-secrets-controller --controller-namespace sealed-secrets > sealed-secrets/controller-public.pem
python3 scripts/seal_secret.py --namespace erp --name erp-properties --file metasfresh.properties=private-inputs/metasfresh.properties --cert sealed-secrets/controller-public.pem --output sealed-secrets/erp/erp-properties.json
python3 scripts/seal_secret.py --namespace erp --name erp-postgres-ca --file ca.crt=private-inputs/rds-ca.crt --cert sealed-secrets/controller-public.pem --output sealed-secrets/erp/erp-postgres-ca.json
python3 scripts/seal_secret.py --namespace erp --name erp-rabbit-secret --file username=private-inputs/rabbit-username --file password=private-inputs/rabbit-password --cert sealed-secrets/controller-public.pem --output sealed-secrets/erp/erp-rabbit-secret.json
python3 scripts/seal_secret.py --namespace erp --name harbor-pull --registry 10.60.50.30 --username-file private-inputs/harbor-pull-user --password-file private-inputs/harbor-pull-token --cert sealed-secrets/controller-public.pem --output sealed-secrets/erp/harbor-pull.json
python3 scripts/seal_secret.py --namespace erp --name erp-origin-tls --type kubernetes.io/tls --file tls.crt=private-inputs/erp.crt --file tls.key=private-inputs/erp.key --cert sealed-secrets/controller-public.pem --output sealed-secrets/erp/erp-origin-tls.json
```

The ERP SealedSecret Argo Application owns sealed-secrets/erp only. PLATFORM/MON
Secrets are applied by an administrator before the dependent Helm releases;
do not put them in the ERP Argo project's namespace scope. Bump
erp.secretRevision in site.yaml after rotating runtime Secrets, regenerate
values-lab.yaml, and sync the reviewed change to restart subPath-mounted files.

## Existing scanner finding

The tracked procurement-webui sample private key remains under the user's
explicit instruction. Trivy still fails on real secret findings; this preparation
does not disable scanning or make the Actions artificially pass.

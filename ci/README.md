# CMC LAB CI setup

Variables: HARBOR_REGISTRY (TLS hostname without scheme), HARBOR_PROJECT,
GITOPS_REPOSITORY (owner/repository), GITOPS_BASE_BRANCH (default main).
Secrets: CI_HARBOR_USERNAME, CI_HARBOR_TOKEN, GITOPS_TOKEN (contents + PR),
METASFRESH_PACKAGES_READ_TOKEN (upstream Maven package access).

Use a dedicated Linux runner labelled self-hosted, linux, cmc-lab, with Docker
buildx, Python, Helm, gh and Trivy installed. It needs DNS/network access to Harbor
and a trusted TLS CA. Pin installed tool versions in the runner inventory.
PRs never run on this credential-bearing runner. Protect main and require review.

Copy the digest-aware chart templates to the real GitOps repository before release.
CI updates charts/erp-adapter/values.yaml and versions.yaml. Argo must read those
values without environment image overrides. The current Application still has a
placeholder repo URL and references an absent environment values file; resolve
both at bootstrap. Protect the GitOps branch and require a separate reviewer.

Source build runs backend JUnit and frontend lint/Jest. All three runtime images
must pass HIGH/CRITICAL scanning before any push. CI creates a PR and never merges
or applies cluster manifests. Evidence artifacts exclude temporary Maven settings.

This is build automation, not evidence of business readiness: validate the selected
image configuration, seed/PG compatibility and login/order flow before promotion.
Migration needs a backup, version guard and single executor in a separate runbook.
Image rollback does not undo schema changes. Legacy base images may fail CVE checks.
Upstream cicd.yaml is unchanged and retains its own runner/registry dependencies.

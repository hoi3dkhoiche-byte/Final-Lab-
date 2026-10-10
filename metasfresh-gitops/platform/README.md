# CMC platform bootstrap

These files prepare the CMC cluster documented on 2026-10-07. They do not prove
that resources exist or that an ERP release has passed business smoke tests.
No cloud deployment is performed when preparing this repository.

## Placement and capacity

Use the actual CMC label cmc-cloud-k8s-nodegroups:
APP=nodegroup-gd6u (three 8-vCPU/8-GB workers),
PLATFORM=nodegroup-platform (one 4-vCPU/4-GB worker),
MON=nodegroup-monitoring (one 4-vCPU/4-GB worker).

Argo CD, Sealed Secrets and the Velero controller run on PLATFORM. Prometheus,
Grafana, Alertmanager, kube-state-metrics, Loki and its gateway run on MON.
Ingress runs on two distinct APP workers. Alloy, node-exporter and Velero
node-agent cover all five Linux workers; tolerations cover only the planned
dedicated PLATFORM/MON taints. Never add a toleration for the provider's
uninitialized taint or remove that taint to force scheduling. Fix CMC CCM
initialization and confirm workers Ready first.

MON memory limits are bounded for a small lab: Prometheus 1 GiB, Loki 768 MiB,
Grafana 256 MiB, Alertmanager/operator 128 MiB each, with bounded sidecars and
agents. Verify node allocatable memory, requests and actual consumption after
installation. These limits are not a throughput guarantee; increase MON capacity
when retention, target count or log traffic causes pressure.

## Review, bootstrap, and register ERP

Run from the GitOps repository root with Python 3, PyYAML, Helm and kubectl.
The script is cross-platform and does not invoke a shell or execute the settings
file. Generate platform-settings.env using the site configuration tool first.

    python platform/bootstrap.py --phase base --render
    python platform/bootstrap.py --phase base --apply

The default action is render. Only the explicit --apply flag changes Kubernetes.
The base phase installs Argo CD and Sealed Secrets; it does not need DB or S3.
Sealed Secrets stores its CRD under Helm crds/, which Helm does not upgrade.
Bootstrap explicitly server-side applies that pinned CRD before the controller,
without force-conflicts or deletion. Review CRD changes when changing chart pins.
It checks the selected API endpoint, TLS verification, Ready node pool counts
and the absence of the cloudprovider uninitialized taint before applying.

Before the next phase, create encrypted/bootstrap Secrets in the correct namespaces:
monitoring/grafana-admin (admin-user, admin-password),
monitoring/alertmanager-config (alertmanager.yaml),
velero/s3-velero (cloud: AWS shared-credentials format),
velero/velero-repo-credentials (repository-password).

Configure a real Alertmanager receiver in alertmanager.yaml and test delivery.
Webhook URLs, passwords and receiver tokens belong in encrypted Secrets, not
these Helm values. Preserve the repository password before the first FSB backup;
changing it later prevents access to existing Kopia repositories.

    python platform/bootstrap.py --phase platform --render
    python platform/bootstrap.py --phase platform --apply

Review the rendered images. Public upstream images are the initial chart defaults.
For Harbor mirrors, pass --image-overrides FILE.json, mapping each release name
to a non-secret Helm override values file. Mirror every rendered container/init
image, including chart jobs and subcharts; use the platform Harbor project and
read-only namespace-specific imagePullSecrets. Distribute Harbor CA trust to
all containerd nodes and confirm pulls before deployment. Merely creating a
Kubernetes pull Secret does not make the node runtime trust a private CA.

The platform phase resolves all required inputs and renders every chart before
applying namespaces/releases. Secret prerequisites and StorageClass are checked
without printing their values. BSL must become Available; the ERP backup
Schedule stays paused until a successful coordinated DB/files restore rehearsal.

    python platform/bootstrap.py --phase erp --render
    python platform/bootstrap.py --phase erp --apply

The ERP phase registers manual Applications only; it never syncs the ERP release.
Use the separate GitOps-final-lab repository with GITOPS_ROOT_PATH empty.
Copy the entire GitOps directory contents to that repository root, including
the generated environments/lab/values-lab.yaml, approved image digests, chart
templates and sealed-secrets/erp YAML. Private Git repository credentials must
be registered in Argo separately with a read-only credential.

Sync metasfresh-erp-secrets first, wait for Secret targets to exist, run the ERP
preflight/business release runbook, then manually sync metasfresh-erp. NetworkPolicy
is integrated in the ERP chart. Do not separately apply legacy policy files:
NetworkPolicies are additive and an older wider allow rule defeats restrictions.

Initial ERP sync is manual, with no pruning automation or deletion finalizer.
Only after login/order/files smoke tests may approved configuration enable
selfHeal with prune=false and allowEmpty=false. When enabling UI HPA later, add
the Deployment replicas ignoreDifferences rule and RespectIgnoreDifferences.
Reviewed Git changes control runtime configuration/version changes.

## Required platform-settings.env inputs

| Phase | Settings |
|---|---|
| All | KUBERNETES_API_SERVER |
| Base | No additional external service settings |
| Platform | CMC_STORAGE_CLASS, GRAFANA_ADMIN_SECRET, ALERTMANAGER_CONFIG_SECRET, VELERO_CREDENTIALS_SECRET, S3_ENDPOINT, S3_REGION, VELERO_BUCKET, INGRESS_SERVICE_MODE |
| Automatic private ELB | CMC_ELB_FLAVOR_ID, CMC_ELB_SUBNET_ID, CMC_ELB_MEMBER_SUBNET_ID, CMC_ELB_SOURCE_RANGES |
| ERP registration | GITOPS_REPO_URL, GITOPS_REVISION, GITOPS_ROOT_PATH (empty for separate repository) |
| Optional existing identity mapping | ERP_READONLY_GROUP, ERP_OPERATOR_GROUP |
| Optional real exporters | PG_EXPORTER_TARGET, RABBIT_EXPORTER_TARGET, SEARCH_EXPORTER_TARGET |

VELERO_REPOSITORY_SECRET, if set, must be velero-repo-credentials. Inputs contain
only connection identifiers/Secret names; do not add credential values to this
file. KUBERNETES_API_SERVER is https://10.60.30.62:6443 in the infrastructure record.
For PG managed RDS, provision a metrics-only database user and an exporter in
MON; do not configure SSH to an RDS endpoint or give the exporter SUPERUSER.
RabbitMQ 3.7.4 has no native rabbitmq_prometheus plugin: leave its exporter target
empty until a compatible exporter is installed and reachable. Search exporter
also requires actual deployment. Empty targets are not evidence of monitoring.

## Private ERP entry point

HAProxy release haproxy in ingress-system has Service/Deployment name
haproxy-ingress, IngressClass haproxy, app.kubernetes.io/name=kubernetes-ingress,
app.kubernetes.io/instance=haproxy. Only TCP 443 is published; HTTPS NodePort is
31443, and LoadBalancer Local-policy healthCheckNodePort is 32042. UI/API/Core
remain ClusterIP behind Ingress.

CMC automatic mode uses loadbalancer.openstack.org/class=cmc-loadbalancer-private
and HCM small flavor a80cfbb4-326b-4819-8e67-58eca8495490. Supply real TRUST and APP
subnet UUIDs. Member selection excludes PLATFORM/MON. The postcheck rejects a
VIP outside TRUST 10.60.20.0/24. Provider class configuration can override subnet
annotations, so compare actual ELB VIP/listener/backend/health-check output.

loadBalancerSourceRanges defaults to Palo Alto TRUST 10.60.20.100/32; actual
enforcement depends on the installed OCCM/Octavia version. Also configure/verify
cloud SGs and listener allowed CIDRs, and test unauthorized sources. This field
alone does not prove that traffic traverses the firewall.

If CMC cannot select the required VIP subnet automatically, use
INGRESS_SERVICE_MODE=NodePort, then create a private ELB in TRUST targeting only
APP workers TCP 31443. There must be no second automatically provisioned ELB.
Confirm TCP health checks on backend nodes that currently host Ingress replicas.

## Access and identity

All admin UIs are ClusterIP with public ingress disabled. Access them from
Bastion through OpenVPN and a local-only kubectl port-forward; do not expose
an admin UI with a public LoadBalancer as a troubleshooting shortcut.

Argo local accounts erp-viewer and erp-operator must receive individual passwords
through the approved private administrative process. Their rights are scoped
to project erp; the operator can manually sync reviewed state but cannot mutate
Applications, override manifests or execute containers.

Kubernetes RoleBindings are created only if the real authenticated groups are
configured. An arbitrary group string does not create a user/group in CMC.
Readonly grants workload visibility without Secrets. Operator adds scale and
pod deletion; it excludes exec, pod creation and Deployment-spec writes that
could indirectly expose mounted Secrets. Test actual users with auth can-i and
record the negative Secret/exec permission tests.

## Backup boundary

Velero uses S3-compatible verified HTTPS, AWS plugin 1.14.x with Velero 1.18.x,
Kopia FSB and node-agent. Provider snapshots are disabled. The node-agent
data-mover ConfigMap limits concurrency and per-operation memory separately
from daemon resources. APP business-data volumes must be annotated for opt-in FSB.
Velero backs Kubernetes resources/PVC files; managed PostgreSQL, RabbitMQ,
Elasticsearch and Harbor need their separate native backup procedures.
The paused Schedule is UTC 18:00 (01:00 Asia/Saigon), retention seven days.
DB/files consistency, restore isolation and measured RPO/RTO remain acceptance
tests; an Available BSL does not establish them.

## Sources and version lock

charts.lock.yaml is the platform chart version source. Versions are checked
against actual published Helm packages, not just upstream main branches.

- CMC private ELB classes/flavors:
  https://cmccloud.vn/document/Kubernetes/huong-dan/tao-service-cho-cum-k8s-voi-dich-vu-elastic-load-balancer
- OCCM subnet/node-selection and source-range behavior:
  https://github.com/kubernetes/cloud-provider-openstack/blob/master/docs/openstack-cloud-controller-manager/expose-applications-using-loadbalancer-type-service.md
- Sealed Secrets installation/controller naming:
  https://github.com/bitnami/sealed-secrets
- Velero AWS plugin compatibility:
  https://github.com/vmware-tanzu/velero-plugin-for-aws
- Velero FSB/repository password:
  https://velero.io/docs/v1.18/file-system-backup/

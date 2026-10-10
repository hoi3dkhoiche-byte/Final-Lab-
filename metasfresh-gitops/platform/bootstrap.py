#!/usr/bin/env python3
"""Render pinned platform releases; only --apply can change a Kubernetes cluster."""
import argparse
import copy
import ipaddress
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parent.parent
PLATFORM = ROOT / "platform"
POOL_LABEL = "cmc-cloud-k8s-nodegroups"
EXPECTED_POOLS = {"nodegroup-gd6u": 3, "nodegroup-platform": 1, "nodegroup-monitoring": 1}
SUPPORTED_PHASES = ("base", "platform", "erp")


def read_settings(path):
    settings = {}
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{number}: expected KEY=value")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in settings:
            raise ValueError(f"{path}:{number}: invalid or repeated settings key")
        if value.startswith(("'", '"')):
            if len(value) < 2 or value[-1] != value[0]:
                raise ValueError(f"{path}:{number}: unmatched quote")
            value = value[1:-1]
        settings[key] = value
    return settings


def required(settings, name):
    value = settings.get(name, "").strip()
    if not value or value.lower() in {"null", "none", "tbd"} or "REQUIRED" in value or "YOUR_" in value:
        raise ValueError(f"Configure {name} before this phase")
    return value


def run(args, capture=False):
    result = subprocess.run([str(arg) for arg in args], check=False, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE if capture else None,
                            stderr=subprocess.PIPE if capture else None)
    if result.returncode:
        summary = (result.stderr or "").strip().splitlines()
        detail = summary[0] if summary else f"exit code {result.returncode}"
        raise ValueError(f"{Path(str(args[0])).name} failed: {detail}")
    return result.stdout if capture else None


def kubectl_json(*args):
    return json.loads(run(["kubectl", *args, "-o", "json"], capture=True))


def check_cluster(settings):
    expected = required(settings, "KUBERNETES_API_SERVER").rstrip("/")
    config = kubectl_json("config", "view", "--minify")
    clusters = config.get("clusters", [])
    if len(clusters) != 1:
        raise ValueError("Select exactly one kubectl context")
    selected = clusters[0]["cluster"]
    if selected.get("server", "").rstrip("/") != expected:
        raise ValueError(f"Selected context is not the configured endpoint {expected}")
    if selected.get("insecure-skip-tls-verify"):
        raise ValueError("Kubeconfig must verify the CMC API TLS certificate")
    # TLS/connectivity is verified by kubectl using its real kubeconfig.
    nodes = kubectl_json("get", "nodes")["items"]
    counts = {pool: 0 for pool in EXPECTED_POOLS}
    for node in nodes:
        pool = node["metadata"].get("labels", {}).get(POOL_LABEL)
        if pool not in counts:
            continue
        name = node["metadata"]["name"]
        conditions = {c["type"]: c["status"] for c in node.get("status", {}).get("conditions", [])}
        if conditions.get("Ready") != "True":
            raise ValueError(f"Worker {name} is not Ready")
        taints = node.get("spec", {}).get("taints", [])
        if any(t["key"] == "node.cloudprovider.kubernetes.io/uninitialized" for t in taints):
            raise ValueError(f"Worker {name} still has the uninitialized taint; fix CMC CCM initialization")
        counts[pool] += 1
    for pool, minimum in EXPECTED_POOLS.items():
        if counts[pool] < minimum:
            raise ValueError(f"Expected at least {minimum} Ready workers with {POOL_LABEL}={pool}")


def check_secret(namespace, name, keys):
    secret = kubectl_json("-n", namespace, "get", "secret", name)
    data = secret.get("data", {})
    if any(not data.get(key) for key in keys):
        raise ValueError(f"Secret {namespace}/{name} lacks required keys {', '.join(keys)}")
    # Values are deliberately never printed, decoded or written.


def check_platform_prerequisites(settings):
    storage = required(settings, "CMC_STORAGE_CLASS")
    kubectl_json("get", "storageclass", storage)
    check_secret("monitoring", required(settings, "GRAFANA_ADMIN_SECRET"),
                 ("admin-user", "admin-password"))
    check_secret("monitoring", required(settings, "ALERTMANAGER_CONFIG_SECRET"),
                 ("alertmanager.yaml",))
    check_secret("velero", required(settings, "VELERO_CREDENTIALS_SECRET"), ("cloud",))
    check_secret("velero", "velero-repo-credentials", ("repository-password",))


def configure_values(release, values, settings):
    values = copy.deepcopy(values)
    if release not in {"monitoring", "loki", "velero", "haproxy"}:
        return values
    if release == "monitoring":
        storage = required(settings, "CMC_STORAGE_CLASS")
        values["grafana"]["admin"]["existingSecret"] = required(settings, "GRAFANA_ADMIN_SECRET")
        values["grafana"]["persistence"]["storageClassName"] = storage
        values["prometheus"]["prometheusSpec"]["storageSpec"]["volumeClaimTemplate"]["spec"]["storageClassName"] = storage
        alert = values["alertmanager"]["alertmanagerSpec"]
        alert["configSecret"] = required(settings, "ALERTMANAGER_CONFIG_SECRET")
        alert["storage"]["volumeClaimTemplate"]["spec"]["storageClassName"] = storage
        for env_name, job in (("PG_EXPORTER_TARGET", "erp-postgres"),
                              ("RABBIT_EXPORTER_TARGET", "erp-rabbit"),
                              ("SEARCH_EXPORTER_TARGET", "erp-search")):
            target = settings.get(env_name, "").strip()
            if target:
                if not re.fullmatch(r"[a-zA-Z0-9_.-]+:[0-9]{1,5}", target):
                    raise ValueError(f"{env_name} must be a reachable exporter host:port")
                values["prometheus"]["prometheusSpec"]["additionalScrapeConfigs"].append(
                    {"job_name": job, "static_configs": [{"targets": [target]}]})
    elif release == "loki":
        values["singleBinary"]["persistence"]["storageClass"] = required(settings, "CMC_STORAGE_CLASS")
    elif release == "velero":
        endpoint = required(settings, "S3_ENDPOINT")
        if (urlsplit(endpoint).scheme != "https" or not urlsplit(endpoint).hostname
                or urlsplit(endpoint).username or urlsplit(endpoint).password):
            raise ValueError("S3_ENDPOINT must use verified HTTPS")
        credentials = required(settings, "VELERO_CREDENTIALS_SECRET")
        if settings.get("VELERO_REPOSITORY_SECRET", "velero-repo-credentials") != "velero-repo-credentials":
            raise ValueError("Velero requires velero-repo-credentials with repository-password")
        values["credentials"]["existingSecret"] = credentials
        location = values["configuration"]["backupStorageLocation"][0]
        location["credential"]["name"] = credentials
        location["bucket"] = required(settings, "VELERO_BUCKET")
        location["config"]["region"] = required(settings, "S3_REGION")
        location["config"]["s3Url"] = endpoint
    elif release == "haproxy":
        service = values["controller"]["service"]
        mode = required(settings, "INGRESS_SERVICE_MODE")
        if mode not in {"LoadBalancer", "NodePort"}:
            raise ValueError("INGRESS_SERVICE_MODE must be LoadBalancer or NodePort")
        service["type"] = mode
        if mode == "NodePort":
            service["annotations"] = {}
            service["healthCheckNodePort"] = 0
            service["loadBalancerSourceRanges"] = []
        else:
            annotations = service["annotations"]
            annotations["loadbalancer.openstack.org/flavor-id"] = required(settings, "CMC_ELB_FLAVOR_ID")
            annotations["loadbalancer.openstack.org/subnet-id"] = required(settings, "CMC_ELB_SUBNET_ID")
            annotations["loadbalancer.openstack.org/member-subnet-id"] = required(settings, "CMC_ELB_MEMBER_SUBNET_ID")
            sources = [s.strip() for s in required(settings, "CMC_ELB_SOURCE_RANGES").split(",")]
            for source in sources:
                network = ipaddress.ip_network(source, strict=True)
                if network.prefixlen == 0:
                    raise ValueError("Private ERP ingress cannot allow every source")
            service["loadBalancerSourceRanges"] = sources
    return values


def configured_argo(settings):
    repo = required(settings, "GITOPS_REPO_URL")
    if not (repo.startswith("https://") or repo.startswith("git@")):
        raise ValueError("GITOPS_REPO_URL must be an HTTPS or SSH Git repository URL")
    revision = required(settings, "GITOPS_REVISION")
    root_path = settings.get("GITOPS_ROOT_PATH", "").strip("/")
    if ".." in root_path.split("/") or root_path.startswith("\\"):
        raise ValueError("GITOPS_ROOT_PATH must stay inside the repository")
    prefix = root_path + "/" if root_path else ""
    project = yaml.safe_load((PLATFORM / "argocd/app-project-erp.yaml").read_text(encoding="utf-8"))
    project["spec"]["sourceRepos"] = [repo]
    application = yaml.safe_load((PLATFORM / "argocd/application-erp.yaml").read_text(encoding="utf-8"))
    secrets = yaml.safe_load((PLATFORM / "argocd/application-secrets.yaml").read_text(encoding="utf-8"))
    for app, path in ((application, "charts/erp-adapter"), (secrets, "sealed-secrets/erp")):
        app["spec"]["source"].update(repoURL=repo, targetRevision=revision, path=prefix + path)
    return project, application, secrets


def bindings(settings):
    objects = []
    for env_name, role in (("ERP_READONLY_GROUP", "erp-readonly"),
                           ("ERP_OPERATOR_GROUP", "erp-operator")):
        group = settings.get(env_name, "").strip()
        if not group:
            print(f"{env_name} unset: no users are granted {role}", file=sys.stderr)
            continue
        objects.append({"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "RoleBinding",
                        "metadata": {"name": role, "namespace": "erp"},
                        "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": role},
                        "subjects": [{"apiGroup": "rbac.authorization.k8s.io", "kind": "Group", "name": group}]})
    return objects


def namespace_manifest(names):
    objects = []
    for name in names:
        labels = {"kubernetes.io/metadata.name": name}
        if name in {"monitoring", "velero"}:
            # Node exporter and Velero require reviewed host access.
            labels["pod-security.kubernetes.io/enforce"] = "privileged"
        objects.append({"apiVersion": "v1", "kind": "Namespace",
                        "metadata": {"name": name, "labels": labels,
                                     "annotations": {"argocd.argoproj.io/sync-options": "Prune=false,Delete=false"}}})
    return yaml.safe_dump_all(objects, sort_keys=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", default=str(ROOT / "environments/lab/platform-settings.env"))
    parser.add_argument("--phase", choices=SUPPORTED_PHASES, required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--render", action="store_true", help="Default: only write local review artifacts")
    action.add_argument("--apply", action="store_true", help="Explicitly install platform or register manual ERP Applications")
    parser.add_argument("--out", default=str(ROOT / ".rendered/platform"))
    parser.add_argument("--charts-dir", help="Directory of verified chartname-version.tgz packages")
    parser.add_argument("--image-overrides", help="JSON release -> Helm values file mapping for verified Harbor mirrors")
    args = parser.parse_args()
    settings = read_settings(args.settings)
    required(settings, "KUBERNETES_API_SERVER")
    if not shutil.which("helm"):
        raise ValueError("Install Helm before rendering")
    if args.apply:
        if not shutil.which("kubectl"):
            raise ValueError("Install kubectl and select the CMC cluster context")
        check_cluster(settings)
        if args.phase == "platform":
            check_platform_prerequisites(settings)
    output = Path(args.out).resolve() / args.phase
    output.mkdir(parents=True, exist_ok=True)
    names = {"base": ["argocd", "sealed-secrets", "erp", "monitoring", "velero", "ingress-system"],
             "platform": ["monitoring", "velero", "ingress-system"], "erp": ["erp"]}[args.phase]
    namespaces = output / "namespaces.yaml"
    namespaces.write_text(namespace_manifest(names), encoding="utf-8")
    if args.phase == "erp":
        # These are registration manifests only; ERP business preflight and manual sync follow.
        if not (ROOT / "environments/lab/values-lab.yaml").is_file():
            raise ValueError("Generate environments/lab/values-lab.yaml before registering ERP")
        app_files = []
        for item in configured_argo(settings):
            path = output / (item["metadata"]["name"] + ".yaml")
            path.write_text(yaml.safe_dump(item, sort_keys=False), encoding="utf-8")
            app_files.append(path)
        roles = output / "rbac.yaml"
        role_docs = list(yaml.safe_load_all((ROOT / "policies/rbac/roles.yaml").read_text(encoding="utf-8")))
        roles.write_text(yaml.safe_dump_all(role_docs + bindings(settings), sort_keys=False), encoding="utf-8")
        if args.apply:
            run(["kubectl", "apply", "-f", namespaces])
            run(["kubectl", "apply", "-f", roles])
            for path in app_files:
                run(["kubectl", "apply", "-f", path])
        print(f"Manual ERP registration prepared at {output}; secrets sync and ERP preflight are still required")
        return
    lock = yaml.safe_load((PLATFORM / "charts.lock.yaml").read_text(encoding="utf-8"))
    overrides = json.loads(Path(args.image_overrides).read_text(encoding="utf-8")) if args.image_overrides else {}
    releases = [item for item in lock["charts"] if item["phase"] == args.phase]
    plans = []
    # Resolve every phase input before any Kubernetes mutation.
    for item in releases:
        release, ns = item["release"], item["namespace"]
        values = yaml.safe_load((PLATFORM / item["values"]).read_text(encoding="utf-8"))
        prepared = configure_values(release, values, settings)
        values_path = output / (release + "-values.yaml")
        values_path.write_text(yaml.safe_dump(prepared, sort_keys=False), encoding="utf-8")
        if args.charts_dir:
            chart = str(Path(args.charts_dir).resolve() / f"{item['chart']}-{item['version']}.tgz")
            if not Path(chart).is_file():
                raise ValueError(f"Missing verified chart package: {chart}")
            package_args = [chart]
        else:
            package_args = [item["chart"], "--repo", item["repo"], "--version", item["version"]]
        values_args = ["--values", str(values_path)]
        if release in overrides:
            values_args += ["--values", str(Path(overrides[release]).resolve())]
        rendered = run(["helm", "template", release, *package_args, "--namespace", ns,
                        "--kube-version", lock["kubernetesVersion"], "--include-crds",
                        "--api-versions", "monitoring.coreos.com/v1",
                        "--api-versions", "monitoring.coreos.com/v1/ServiceMonitor",
                        "--api-versions", "monitoring.coreos.com/v1/PodMonitor",
                        *values_args], capture=True)
        (output / (release + ".yaml")).write_text(rendered, encoding="utf-8")
        if release == "sealed-secrets":
            definitions = [doc for doc in yaml.safe_load_all(rendered)
                           if doc and doc.get("kind") == "CustomResourceDefinition"
                           and doc.get("metadata", {}).get("name") == "sealedsecrets.bitnami.com"]
            if len(definitions) != 1:
                raise ValueError("Pinned Sealed Secrets chart lacks its expected CRD")
            (output / "sealed-secrets-crds.yaml").write_text(
                yaml.safe_dump_all(definitions, sort_keys=False), encoding="utf-8")
        plans.append((item, package_args, values_args))
    if args.apply:
        run(["kubectl", "apply", "-f", namespaces])
        for item, package_args, values_args in plans:
            if item["release"] == "sealed-secrets":
                # Helm crds/ resources are not upgraded by helm upgrade.
                # Keep conflicts visible; never force ownership or replace schemas.
                run(["kubectl", "apply", "--server-side",
                     "--field-manager=metasfresh-platform-bootstrap",
                     "-f", output / "sealed-secrets-crds.yaml"])
            run(["helm", "upgrade", "--install", item["release"], *package_args,
                 "--namespace", item["namespace"], "--wait", "--timeout", "10m", *values_args])
    if args.apply and args.phase == "platform":
        run(["kubectl", "-n", "velero", "wait", "backupstoragelocation/default",
             "--for=jsonpath={.status.phase}=Available", "--timeout=180s"])
        run(["kubectl", "apply", "-f", PLATFORM / "velero/schedule.yaml"])
        run(["kubectl", "-n", "ingress-system", "rollout", "status",
             "deployment/haproxy-ingress", "--timeout=180s"])
        if settings.get("INGRESS_SERVICE_MODE") == "LoadBalancer":
            service = kubectl_json("-n", "ingress-system", "get", "service", "haproxy-ingress")
            addresses = service.get("status", {}).get("loadBalancer", {}).get("ingress", [])
            trust = ipaddress.ip_network("10.60.20.0/24")
            if not addresses or any("ip" not in a or ipaddress.ip_address(a["ip"]) not in trust for a in addresses):
                raise ValueError("Private ELB VIP is not confirmed in TRUST 10.60.20.0/24; verify CMC class subnet overrides")
        print("Verify ELB allowed CIDRs/cloud SGs and forbidden-source tests before exposing ERP")
    print(f"{args.phase} review artifacts: {output}")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        # Commands contain only configuration/paths; private Secret values remain captured.
        print(f"Platform bootstrap stopped: {error}", file=sys.stderr)
        sys.exit(1)

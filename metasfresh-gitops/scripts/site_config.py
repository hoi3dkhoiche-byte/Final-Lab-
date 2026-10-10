"""Generate non-secret overlays and report missing cloud inputs. Requires PyYAML."""
import argparse
import ipaddress
import json
import re
from pathlib import Path
from urllib.parse import urlparse
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE = ROOT / "environments/lab/site.yaml"

def load_site(path=DEFAULT_SITE):
    site = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(site, dict) or site.get("schemaVersion") != 1:
        raise ValueError("Unsupported site schemaVersion")
    for key in ("gitops", "erp", "cluster", "network", "ingress", "harbor",
                "postgres", "rabbitmq", "search", "backup", "monitoring", "rbac"):
        if not isinstance(site.get(key), dict):
            raise ValueError("Missing section: " + key)
    for section in ("postgres", "rabbitmq", "search"):
        key = "primary" if section == "postgres" else "host"
        ipaddress.IPv4Address(site[section][key])
    if site["postgres"].get("applicationUser") == "admin":
        raise ValueError("RDS admin must not be the ERP runtime account")
    if site["ingress"]["serviceMode"] not in ("LoadBalancer", "NodePort"):
        raise ValueError("ingress.serviceMode must be LoadBalancer or NodePort")
    for section, keys in (("gitops", ("repository",)), ("cluster", ("apiServer",))):
        for key in keys:
            value = site[section][key]
            if urlparse(value).scheme != "https" or not urlparse(value).hostname:
                raise ValueError(section + "." + key + " must be HTTPS")
    for cidr in site["ingress"]["elb"]["allowedSources"] + site["backup"]["endpointCIDRs"]:
        ipaddress.ip_network(cidr, strict=True)
    return site

def values_for(site):
    pg, rabbit, search = site["postgres"], site["rabbitmq"], site["search"]
    erp, cluster = site["erp"], site["cluster"]
    selector = {cluster["nodeGroupLabel"]: cluster["pools"]["app"]["name"]}
    # Images intentionally live only in chart values.yaml, owned by CI promotion.
    return {
        "validation": {"strict": True},
        "global": {"namespace": erp["namespace"], "registry": site["harbor"]["registry"],
                   "imagePullSecrets": [{"name": site["harbor"]["pullSecret"]}]},
        "runtime": {
            "propertiesSecret": {"name": erp["propertiesSecret"], "key": "metasfresh.properties",
                                 "revision": str(erp.get("secretRevision", ""))},
            "rabbitmqSecret": {"name": erp["rabbitSecret"], "usernameKey": "username",
                              "passwordKey": "password"}},
        "databaseTLS": {"enabled": True, "mode": "verify-full", "secretName": pg["caSecret"],
                        "key": "ca.crt", "mountPath": "/etc/metasfresh/db-ca/ca.crt"},
        "persistence": {"storageClass": cluster["storageClass"]},
        "core": {"nodeSelector": selector, "persistence": {"existingClaim": erp["coreExistingClaim"]}},
        "api": {"nodeSelector": selector, "persistence": {"existingClaim": erp["apiExistingClaim"]}},
        "ui": {"nodeSelector": selector,
               "config": {"apiUrl": "/rest/api", "websocketUrl": "/stomp"},
               "autoscaling": {"enabled": False}},
        "externalDB": {
            "pgHost": pg["primary"], "pgPort": pg["port"], "pgCIDR": pg["primary"] + "/32",
            "rabbitHost": rabbit["host"], "rabbitPort": rabbit["port"], "rabbitCIDR": rabbit["host"] + "/32",
            "searchHost": search["host"], "searchPort": search["port"], "searchCIDR": search["host"] + "/32",
            "searchTransportPort": search["transportPort"]},
        "ingress": {"enabled": True, "className": "haproxy", "host": erp["domain"],
                    "tlsSecretName": erp["tlsSecretName"]},
        "networkPolicy": {"enabled": True, "ingressController": {
            "namespace": site["ingress"]["controllerNamespace"],
            "podSelector": site["ingress"]["controllerSelector"]}},
        "metrics": {"enabled": False}}

def settings_for(site):
    elb, backup = site["ingress"]["elb"], site["backup"]
    return {
        "KUBERNETES_API_SERVER": site["cluster"]["apiServer"],
        "GITOPS_REPO_URL": site["gitops"]["repository"],
        "GITOPS_REVISION": site["gitops"]["revision"],
        "GITOPS_ROOT_PATH": site["gitops"]["rootPath"],
        "CMC_STORAGE_CLASS": site["cluster"]["storageClass"],
        "S3_ENDPOINT": backup["endpoint"], "S3_REGION": backup["region"],
        "VELERO_BUCKET": backup["veleroBucket"],
        "GRAFANA_ADMIN_SECRET": site["monitoring"]["grafanaSecret"],
        "ALERTMANAGER_CONFIG_SECRET": site["monitoring"]["alertmanagerSecret"],
        "VELERO_CREDENTIALS_SECRET": "s3-velero",
        "VELERO_REPOSITORY_SECRET": backup["repositorySecret"],
        "INGRESS_SERVICE_MODE": site["ingress"]["serviceMode"],
        "CMC_ELB_FLAVOR_ID": elb["flavorID"],
        "CMC_ELB_SUBNET_ID": elb["vipSubnetID"],
        "CMC_ELB_MEMBER_SUBNET_ID": elb["memberSubnetID"],
        "CMC_ELB_SOURCE_RANGES": ",".join(elb["allowedSources"]),
        "ERP_READONLY_GROUP": site["rbac"]["readonlyGroup"],
        "ERP_OPERATOR_GROUP": site["rbac"]["operatorGroup"],
        "PG_EXPORTER_TARGET": site["monitoring"]["pgExporterTarget"],
        "RABBIT_EXPORTER_TARGET": site["rabbitmq"]["exporterTarget"],
        "SEARCH_EXPORTER_TARGET": site["search"]["exporterTarget"]}

def missing_inputs(site, phase="all"):
    missing = []
    def require(section, key):
        if not site[section].get(key):
            missing.append(section + "." + key)
    if phase in ("platform", "erp", "all"):
        require("cluster", "storageClass")
    if phase in ("platform", "all"):
        for key in ("endpoint", "region", "veleroBucket", "endpointCIDRs"):
            require("backup", key)
        if site["backup"]["endpoint"] and urlparse(site["backup"]["endpoint"]).scheme != "https":
            missing.append("backup.endpoint must use HTTPS")
        if site["ingress"]["serviceMode"] == "LoadBalancer":
            for key in ("vipSubnetID", "memberSubnetID"):
                if not site["ingress"]["elb"].get(key):
                    missing.append("ingress.elb." + key)
    if phase in ("erp", "all"):
        for key in ("domain",):
            require("erp", key)
        for key in ("database", "applicationUser"):
            require("postgres", key)
        if site["erp"]["domain"] and (not re.fullmatch(
                r"(?=.{1,253}$)[a-zA-Z0-9](?:[a-zA-Z0-9.-]*[a-zA-Z0-9])?", site["erp"]["domain"])
                or "." not in site["erp"]["domain"]):
            missing.append("erp.domain must be a DNS hostname")
    if phase == "all":
        for key in ("backupUser", "runnerRootCertPath"):
            require("postgres", key)
        require("backup", "postgresBucket")
        for key in ("vip", "backendSourceCIDRs"):
            if not site["ingress"]["elb"].get(key):
                missing.append("ingress.elb." + key)
        if site["ingress"]["serviceMode"] == "LoadBalancer" and not site["ingress"]["elb"]["healthSourceCIDRs"]:
            missing.append("ingress.elb.healthSourceCIDRs")
        if not site["harbor"]["workerRuntimeTrustVerified"]:
            missing.append("harbor.workerRuntimeTrustVerified (verify every worker image pull)")
    return missing

def write_outputs(site, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "values-lab.yaml").write_text(
        "# Generated by scripts/site_config.py; edit site.yaml then regenerate.\n" +
        yaml.safe_dump(values_for(site), sort_keys=False), encoding="utf-8")
    settings = settings_for(site)
    if any("\n" in str(v) or "\r" in str(v) or "=" in k for k, v in settings.items()):
        raise ValueError("Invalid settings line")
    (directory / "platform-settings.env").write_text(
        "# Non-secret key=value file read by platform/bootstrap.py. Do not source as shell code.\n" +
        "\n".join(k + "=" + str(v) for k, v in settings.items()) + "\n", encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    parser.add_argument("--write", action="store_true", help="Write non-secret generated files")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--phase", choices=("base", "platform", "erp", "all"), default="all")
    args = parser.parse_args()
    site = load_site(args.site)
    if args.write:
        write_outputs(site, args.output_dir or args.site.parent)
        print("Generated values-lab.yaml and platform-settings.env; this does not deploy.")
    missing = missing_inputs(site, args.phase)
    if missing:
        print("Required cloud inputs still missing:\n- " + "\n- ".join(missing))
    else:
        print("Non-secret inputs complete for phase " + args.phase + "; run preflight before apply.")
    return 0 if args.write else int(bool(missing))

if __name__ == "__main__":
    raise SystemExit(main())

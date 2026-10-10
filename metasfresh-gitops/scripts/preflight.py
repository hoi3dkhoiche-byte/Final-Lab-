"""Read-only release checks. Never creates cloud resources or prints Secret values."""
import argparse
import hashlib
import ipaddress
import json
import re
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parent))
from site_config import ROOT, DEFAULT_SITE, load_site, missing_inputs, values_for

def merge(left, right):
    result = dict(left)
    for key, value in right.items():
        result[key] = merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result

def release_values(site, root=ROOT):
    defaults = yaml.safe_load((Path(root) / "charts/erp-adapter/values.yaml").read_text(encoding="utf-8"))
    return merge(defaults, values_for(site))

def release_errors(site, values):
    errors = []
    registry = site["harbor"]["registry"]
    selector = {site["cluster"]["nodeGroupLabel"]: site["cluster"]["pools"]["app"]["name"]}
    for component in ("core", "api", "ui"):
        image = values[component]["image"]
        if not image["repository"].startswith(registry + "/" + site["harbor"]["project"] + "/"):
            errors.append(component + " image must belong to the configured Harbor project")
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", image.get("digest", "")):
            errors.append(component + " image requires the tested digest")
        if values[component]["nodeSelector"] != selector:
            errors.append(component + " nodeSelector differs from the APP pool")
    if not values["validation"]["strict"] or not values["networkPolicy"]["enabled"]:
        errors.append("Strict validation and NetworkPolicy must stay enabled")
    return errors

def evidence_errors(site, values, root=ROOT):
    evidence_path = site["postgres"].get("compatibilityEvidence", "")
    if not evidence_path:
        return ["postgres.compatibilityEvidence: isolated PG18 migration/smoke/restore report required"]
    path = Path(evidence_path)
    if not path.is_absolute():
        path = Path(root) / path
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["Cannot read the PostgreSQL compatibility evidence JSON"]
    errors = []
    if evidence.get("postgres_major") != site["postgres"]["major"]:
        errors.append("Evidence PostgreSQL major differs from current RDS")
    if not str(evidence.get("database", "")).startswith("lab_"):
        errors.append("Compatibility test must use an isolated lab_* database")
    for component in ("core", "api", "ui"):
        image = values[component]["image"]
        expected = image["repository"] + "@" + image["digest"]
        if evidence.get("images", {}).get(component) != expected:
            errors.append("Compatibility evidence does not match " + component + " digest")
    for name in ("seed_sha256", "migration_sha256"):
        if not re.fullmatch(r"[a-f0-9]{64}", str(evidence.get(name, ""))):
            errors.append("Evidence missing verified " + name)
    for name in ("initialization", "migrations", "login", "order", "rabbitmq",
                 "search", "websocket", "database_restore", "files_restore"):
        if evidence.get("checks", {}).get(name) is not True:
            errors.append("Unverified acceptance check: " + name)
    if not evidence.get("report") or not evidence.get("tested_at"):
        errors.append("Evidence needs a dated report reference")
    return errors

def command_json(args):
    result = subprocess.run(args, check=False, text=True, capture_output=True)
    if result.returncode:
        # kubectl error can contain endpoint details; never echo raw Secret JSON.
        raise ValueError("Command failed: " + " ".join(args[:4]) + "; check permissions/connectivity")
    return json.loads(result.stdout)

def cluster_errors(site, phase):
    errors = []
    server = subprocess.run(
        ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
        text=True, capture_output=True, check=False)
    if server.returncode or server.stdout.strip().rstrip("/") != site["cluster"]["apiServer"].rstrip("/"):
        return ["Current kubeconfig does not target the expected CMC API; no apply attempted"]
    tls = subprocess.run(
        ["kubectl", "config", "view", "--minify", "-o",
         "jsonpath={.clusters[0].cluster.insecure-skip-tls-verify}"],
        text=True, capture_output=True, check=False)
    if tls.returncode or tls.stdout.strip().lower() == "true":
        return ["Kubeconfig must verify the CMC API TLS certificate"]
    try:
        nodes = command_json(["kubectl", "get", "nodes", "-o", "json"])["items"]
        pools = site["cluster"]["pools"]
        counts = {key: 0 for key in pools}
        for node in nodes:
            name = node["metadata"]["name"]
            ready = any(c["type"] == "Ready" and c["status"] == "True"
                        for c in node["status"].get("conditions", []))
            if not ready:
                errors.append("Node not Ready: " + name)
            if any(t["key"] == "node.cloudprovider.kubernetes.io/uninitialized"
                   for t in node["spec"].get("taints", [])):
                errors.append("Cloud controller has not initialized node: " + name)
            group = node["metadata"].get("labels", {}).get(site["cluster"]["nodeGroupLabel"])
            for key, pool in pools.items():
                if group == pool["name"] and ready:
                    counts[key] += 1
        for key, minimum in (("app", 2), ("monitoring", 1), ("platform", 1)):
            if counts[key] < minimum:
                errors.append("Insufficient Ready nodes in " + key + " pool")
        if phase != "base":
            classes = command_json(["kubectl", "get", "storageclasses", "-o", "json"])["items"]
            if site["cluster"]["storageClass"] not in [c["metadata"]["name"] for c in classes]:
                errors.append("Configured CSI StorageClass does not exist")
        if phase in ("erp", "all"):
            requirements = [
                (site["erp"]["namespace"], site["erp"]["propertiesSecret"], ["metasfresh.properties"], "Opaque"),
                (site["erp"]["namespace"], site["postgres"]["caSecret"], ["ca.crt"], "Opaque"),
                (site["erp"]["namespace"], site["erp"]["rabbitSecret"], ["username", "password"], "Opaque"),
                (site["erp"]["namespace"], site["harbor"]["pullSecret"], [".dockerconfigjson"], "kubernetes.io/dockerconfigjson"),
                (site["erp"]["namespace"], site["erp"]["tlsSecretName"], ["tls.crt", "tls.key"], "kubernetes.io/tls")]
        else:
            requirements = []
        if phase in ("platform", "all"):
            requirements += [
                ("monitoring", site["monitoring"]["grafanaSecret"], ["admin-user", "admin-password"], "Opaque"),
                ("monitoring", site["monitoring"]["alertmanagerSecret"], ["alertmanager.yaml"], "Opaque"),
                ("velero", "s3-velero", ["cloud"], "Opaque"),
                ("velero", site["backup"]["repositorySecret"], ["repository-password"], "Opaque")]
        for namespace, name, keys, expected_type in requirements:
            secret = command_json(["kubectl", "-n", namespace, "get", "secret", name, "-o", "json"])
            if secret.get("type") != expected_type or any(not secret.get("data", {}).get(k) for k in keys):
                errors.append("Secret type or required key missing: " + namespace + "/" + name)
    except (ValueError, OSError, KeyError) as error:
        errors.append(str(error))
    return errors

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=DEFAULT_SITE)
    parser.add_argument("--phase", choices=("base", "platform", "erp", "all"), default="all")
    parser.add_argument("--cluster", action="store_true", help="Check live CMC nodes, CSI and Secret key presence")
    parser.add_argument("--harbor-ca", type=Path, help="Verify Harbor HTTPS using this public CA certificate")
    parser.add_argument("--require-acceptance", action="store_true",
                        help="Require isolated PG18/image migration and smoke evidence before real ERP sync")
    args = parser.parse_args()
    try:
        site = load_site(args.site)
        errors = missing_inputs(site, args.phase)
        values = release_values(site)
        if args.phase in ("erp", "all"):
            errors += release_errors(site, values)
            if args.require_acceptance:
                errors += evidence_errors(site, values)
        if args.cluster and not errors:
            errors += cluster_errors(site, args.phase)
        if args.harbor_ca:
            context = ssl.create_default_context(cafile=str(args.harbor_ca))
            url = "https://" + site["harbor"]["registry"] + "/api/v2.0/ping"
            with urllib.request.urlopen(url, context=context, timeout=10) as response:
                if response.status != 200:
                    errors.append("Harbor HTTPS ping failed")
        if errors:
            print("Preflight blocked:\n- " + "\n- ".join(errors))
            return 1
        print("Preflight passed for " + args.phase + ". This command did not deploy.")
        if args.phase in ("erp", "all") and not args.require_acceptance:
            print("PG18/runtime acceptance was not requested; require it before real ERP sync.")
        return 0
    except (OSError, ValueError, KeyError, ssl.SSLError) as error:
        print("Preflight blocked: " + str(error), file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())

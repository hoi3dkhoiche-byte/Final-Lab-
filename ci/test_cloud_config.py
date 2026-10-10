"""Release safety and rendered CMC contract tests; no cloud connection."""
import copy
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml

REPO = Path(__file__).resolve().parents[1]
BUNDLE = REPO / "metasfresh-gitops"
sys.path.insert(0, str(BUNDLE / "scripts"))
import site_config
import preflight
import seal_secret
import export_gitops

def configured_site():
    site = copy.deepcopy(site_config.load_site())
    site["erp"]["domain"] = "erp.lab.internal"
    site["cluster"]["storageClass"] = "test-csi"
    site["postgres"].update(database="test_erp", applicationUser="test_erp_user",
                            backupUser="test_dump_user", runnerRootCertPath="/test/ca.crt")
    site["backup"].update(endpoint="https://s3.lab.internal", region="test-region",
                          postgresBucket="test-db", veleroBucket="test-files",
                          endpointCIDRs=["192.0.2.10/32"])
    site["ingress"]["elb"].update(vipSubnetID="test-trust-uuid", memberSubnetID="test-app-uuid",
                                  vip="10.60.20.150", backendSourceCIDRs=["10.60.20.150/32"],
                                  healthSourceCIDRs=["10.60.20.151/32"])
    site["harbor"]["workerRuntimeTrustVerified"] = True
    return site

def pinned_values(site):
    values = preflight.release_values(site)
    for component, image in (("core", "metas-app"), ("api", "metas-api"), ("ui", "metas-frontend")):
        values[component]["image"].update(repository="10.60.50.30/metasfresh/" + image,
                                         digest="sha256:" + "a" * 64, tag="")
    return values

class SiteTests(unittest.TestCase):
    def test_inventory_uses_write_primary_without_read_failover(self):
        site = site_config.load_site()
        values = site_config.values_for(site)
        self.assertEqual(values["externalDB"]["pgHost"], "10.60.40.113")
        self.assertEqual(values["externalDB"]["pgCIDR"], "10.60.40.113/32")
        self.assertEqual(site["postgres"]["major"], 18)
        self.assertEqual(site["postgres"]["readReplicas"], ["10.60.40.126", "10.60.40.45"])
        self.assertEqual(site["postgres"]["failoverEndpoint"], "")
        self.assertEqual(site["network"]["paloalto"]["trustInterface"], "ethernet1/2")

    def test_generation_does_not_shadow_promoted_digests(self):
        site = configured_site()
        values = site_config.values_for(site)
        for component in ("core", "api", "ui"):
            self.assertNotIn("image", values[component])
        self.assertTrue(values["validation"]["strict"])
        self.assertEqual(values["externalDB"]["searchTransportPort"], 0)
        self.assertFalse(values["ui"]["autoscaling"]["enabled"])
        self.assertEqual(site_config.settings_for(site)["GITOPS_ROOT_PATH"], "")
        self.assertEqual(site_config.settings_for(site)["CMC_ELB_SOURCE_RANGES"], "10.60.20.100/32")
        with tempfile.TemporaryDirectory() as directory:
            site_config.write_outputs(site, directory)
            actual = yaml.safe_load((Path(directory) / "values-lab.yaml").read_text())
            self.assertEqual(actual, values)

    def test_missing_real_cloud_inputs_are_not_silently_defaulted(self):
        missing = site_config.missing_inputs(site_config.load_site())
        for key in ("erp.domain", "postgres.database", "postgres.applicationUser",
                    "cluster.storageClass", "backup.endpoint", "backup.veleroBucket",
                    "ingress.elb.vip", "ingress.elb.vipSubnetID"):
            self.assertIn(key, missing)
        self.assertEqual(site_config.missing_inputs(configured_site()), [])

    def test_admin_is_rejected_as_runtime_identity(self):
        site = configured_site()
        site["postgres"]["applicationUser"] = "admin"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "site.yaml"
            path.write_text(yaml.safe_dump(site))
            with self.assertRaisesRegex(ValueError, "admin"):
                site_config.load_site(path)

    def test_acceptance_is_bound_to_database_major_and_all_image_digests(self):
        site = configured_site()
        values = pinned_values(site)
        evidence = {"postgres_major": 18, "database": "lab_seed_test",
                    "images": {c: v["image"]["repository"] + "@" + v["image"]["digest"]
                               for c, v in values.items() if c in ("core", "api", "ui")},
                    "seed_sha256": "b" * 64, "migration_sha256": "c" * 64,
                    "checks": {name: True for name in ("initialization", "migrations", "login",
                              "order", "rabbitmq", "search", "websocket", "database_restore", "files_restore")},
                    "report": "synthetic-unit-test-report", "tested_at": "2026-10-08"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            site["postgres"]["compatibilityEvidence"] = str(path)
            path.write_text(json.dumps(evidence))
            self.assertEqual(preflight.evidence_errors(site, values), [])
            evidence["images"]["api"] = "different-digest"
            evidence["checks"]["migrations"] = False
            path.write_text(json.dumps(evidence))
            errors = preflight.evidence_errors(site, values)
            self.assertTrue(any("api" in error for error in errors))
            self.assertTrue(any("migrations" in error for error in errors))

    def test_wrong_kube_context_stops_before_any_secret_read(self):
        result = subprocess.CompletedProcess([], 0, stdout="https://wrong.lab.internal:6443", stderr="")
        with patch.object(preflight.subprocess, "run", return_value=result) as call:
            errors = preflight.cluster_errors(configured_site(), "erp")
        self.assertEqual(call.call_count, 1)
        self.assertTrue(any("expected CMC API" in error for error in errors))

    def test_insecure_kube_context_stops_before_node_or_secret_reads(self):
        results = [subprocess.CompletedProcess([], 0, stdout="https://10.60.30.62:6443", stderr=""),
                   subprocess.CompletedProcess([], 0, stdout="true", stderr="")]
        with patch.object(preflight.subprocess, "run", side_effect=results) as call:
            errors = preflight.cluster_errors(configured_site(), "erp")
        self.assertEqual(call.call_count, 2)
        self.assertTrue(any("TLS certificate" in error for error in errors))

class SealingTests(unittest.TestCase):
    def test_registry_secret_is_built_from_files_and_piped_in_memory(self):
        with tempfile.TemporaryDirectory() as directory:
            username, password = Path(directory) / "user", Path(directory) / "password"
            username.write_text("test-robot")
            password.write_text("unit-test-only-value")
            secret = seal_secret.secret_manifest("erp", "harbor-pull", "Opaque", {},
                                                 "10.60.50.30", username, password)
            self.assertEqual(secret["type"], "kubernetes.io/dockerconfigjson")
            self.assertEqual(set(secret["data"]), {".dockerconfigjson"})
            sealed = {"kind": "SealedSecret", "spec": {"encryptedData": {".dockerconfigjson": "test-ciphertext"},
                                                       "template": {"metadata": {"name": "harbor-pull"}}}}
            with patch.object(seal_secret.subprocess, "run", return_value=
                              subprocess.CompletedProcess([], 0, stdout=json.dumps(sealed).encode(), stderr=b"")) as child:
                output = seal_secret.seal(secret, Path("public-cert.pem"))
            command = child.call_args.args[0]
            self.assertIn("strict", command)
            self.assertNotIn("unit-test-only-value", " ".join(command))
            self.assertEqual(json.loads(child.call_args.kwargs["input"]), secret)
            self.assertEqual(output, sealed)

    def test_sealing_refuses_unencrypted_output(self):
        secret = {"data": {"password": "encoded-test"}}
        output = {"kind": "SealedSecret", "spec": {"encryptedData": {"password": "cipher"},
                  "template": {"stringData": {"password": "plaintext-test"}}}}
        with patch.object(seal_secret.subprocess, "run", return_value=
                          subprocess.CompletedProcess([], 0, stdout=json.dumps(output).encode(), stderr=b"")):
            with self.assertRaisesRegex(ValueError, "Plaintext"):
                seal_secret.seal(secret, "cert.pem")

    def test_export_omits_user_install_script_and_legacy_policy(self):
        paths = [path.relative_to(BUNDLE).as_posix() for path in export_gitops.sources()]
        self.assertIn("platform/bootstrap.py", paths)
        self.assertIn("charts/erp-adapter/templates/networkpolicy.yaml", paths)
        self.assertNotIn("install-gitops.sh", paths)
        self.assertNotIn("policies/network/allow-erp.yaml", paths)
        self.assertFalse(any("private-inputs" in path for path in paths))

class HelmContractTests(unittest.TestCase):
    def setUp(self):
        self.helm = os.environ.get("TEST_HELM") or shutil.which("helm")
        if not self.helm:
            self.skipTest("Helm is not installed")
        self.site = configured_site()
        self.values = pinned_values(self.site)

    def render(self, values):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.yaml"
            path.write_text(yaml.safe_dump(values, sort_keys=False))
            return subprocess.run([self.helm, "template", "erp", str(BUNDLE / "charts/erp-adapter"),
                                   "--namespace", "erp", "--kube-version", "1.35.3",
                                   "--values", str(path)], text=True, capture_output=True, check=False)

    def test_strict_render_has_secret_volumes_routes_and_exact_data_egress(self):
        rendered = self.render(self.values)
        self.assertEqual(rendered.returncode, 0, rendered.stderr)
        documents = [d for d in yaml.safe_load_all(rendered.stdout) if d]
        by_name = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Deployment"}
        self.assertEqual(len(by_name), 3)
        for component in ("core", "api"):
            pod = by_name["metasfresh-" + component]["spec"]["template"]["spec"]
            self.assertFalse(pod["automountServiceAccountToken"])
            self.assertTrue(any(v.get("secret", {}).get("secretName") == "erp-properties" for v in pod["volumes"]))
            self.assertTrue(any(v.get("secret", {}).get("secretName") == "erp-postgres-ca" for v in pod["volumes"]))
            self.assertEqual(by_name["metasfresh-" + component]["spec"]["strategy"]["type"], "Recreate")
        ingress = next(d for d in documents if d["kind"] == "Ingress")
        self.assertEqual({p["path"] for p in ingress["spec"]["rules"][0]["http"]["paths"]},
                         {"/", "/rest/api", "/stomp"})
        policy = next(d for d in documents if d["metadata"]["name"] == "erp-backend-to-data")
        actual = {(rule["to"][0]["ipBlock"]["cidr"], rule["ports"][0]["port"]) for rule in policy["spec"]["egress"]}
        self.assertEqual(actual, {("10.60.40.113/32", 5432), ("10.60.40.104/32", 5672), ("10.60.40.30/32", 9200)})

    def test_strict_render_refuses_missing_domain_or_untrusted_registry(self):
        for changed in ("domain", "registry"):
            values = copy.deepcopy(self.values)
            if changed == "domain":
                values["ingress"]["host"] = ""
            else:
                values["api"]["image"]["repository"] = "untrusted/metas-api"
            with self.subTest(changed=changed):
                result = self.render(values)
                self.assertNotEqual(result.returncode, 0)


class DatabaseVersionTests(unittest.TestCase):
    def test_database_tool_major_guard_matches_rds_and_rejects_old_clients(self):
        bash = os.environ.get("TEST_BASH") or shutil.which("bash")
        if not bash:
            self.skipTest("Bash is unavailable")
        script = (REPO / "ci/database.sh").read_text()
        body = script.split("verify_pg_tools() {", 1)[1].split("\n}\n", 1)[0]
        function = "verify_pg_tools() {" + body + "\n}\n"
        for server, client, expected_message in ((180003, 18, ""), (180003, 15, "older than server"),
                                                (170009, 18, "differs from configured RDS")):
            command = ("psql() { printf '" + str(server) + "'; };\n"
                       "pg_dump() { printf 'pg_dump (PostgreSQL) " + str(client) + ".1'; };\n"
                       "pg_restore() { printf 'pg_restore (PostgreSQL) " + str(client) + ".1'; };\n"
                       + function + "\nverify_pg_tools")
            result = subprocess.run([bash, "-c", command], text=True, capture_output=True,
                                    env=dict(os.environ, PG_EXPECTED_MAJOR="18"), check=False)
            with self.subTest(server=server, client=client):
                self.assertEqual(result.returncode == 0, not expected_message, result.stdout + result.stderr)
                if expected_message:
                    self.assertIn(expected_message, result.stdout + result.stderr)

    def test_preflight_cli_reports_missing_inputs_without_import_failure(self):
        result = subprocess.run([sys.executable, "-B", str(BUNDLE / "scripts/preflight.py"),
                                 "--phase", "all", "--require-acceptance"],
                                text=True, capture_output=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn("Preflight blocked", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

if __name__ == "__main__":
    unittest.main()

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml
from update_gitops import update
from verify_build import verify

class PromotionTests(unittest.TestCase):
    def setUp(self):
        self.run = {'id': 17, 'run_attempt': 2, 'conclusion': 'success', 'status': 'completed',
                    'head_branch': 'main', 'head_sha': 'a' * 40, 'event': 'push',
                    'path': '.github/workflows/ci.yml', 'repository': {'full_name': 'team/lab'},
                    'html_url': 'https://github.com/team/lab/actions/runs/17'}

    def test_untrusted_or_failed_builds_are_rejected(self):
        for field, value in [('event', 'pull_request'), ('conclusion', 'failure'),
                             ('head_branch', 'feature'), ('path', '.github/workflows/other.yml')]:
            with self.subTest(field=field):
                run = dict(self.run, **{field: value})
                with self.assertRaises(ValueError):
                    verify(run, 'team/lab')
        with self.assertRaises(ValueError):
            verify(self.run, 'different/repo')
        self.assertEqual(verify(self.run, 'team/lab'), 'a' * 40)

    def fixture(self, directory):
        root = Path(directory)
        chart = root / 'charts/erp-adapter'
        (chart / 'templates').mkdir(parents=True)
        values = {'global': {'namespace': 'erp'}}
        for component, image in [('core', 'metas-app'), ('api', 'metas-api'), ('ui', 'metas-frontend')]:
            values[component] = {'image': {'repository': 'old', 'tag': 'old'}}
            template = ('image: "{{ .Values.COMPONENT.image.repository }}'
                        '{{ if .Values.COMPONENT.image.digest }}@{{ .Values.COMPONENT.image.digest }}'
                        '{{ else }}:{{ .Values.COMPONENT.image.tag }}{{ end }}"')
            (chart / f'templates/deployment-{component}.yaml').write_text(
                template.replace('COMPONENT', component))
            (root / f'{image}.json').write_text(json.dumps({'digest': 'sha256:' + 'b' * 64}))
        (chart / 'values.yaml').write_text(yaml.safe_dump(values))
        (root / 'versions.yaml').write_text('images: {}\n')
        evidence = {'GITHUB_SHA': 'a' * 40, 'GITHUB_RUN_ID': '17', 'GITHUB_RUN_ATTEMPT': '2',
                    'HARBOR_REGISTRY': 'harbor.example', 'HARBOR_PROJECT': 'erp'}
        (root / 'release.json').write_text(json.dumps(evidence))
        return root, chart

    def test_last_bad_digest_does_not_modify_either_release_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root, chart = self.fixture(directory)
            before_values = (chart / 'values.yaml').read_text()
            before_lock = (root / 'versions.yaml').read_text()
            (root / 'metas-frontend.json').write_text('{"digest":"bad"}')
            with self.assertRaises(ValueError):
                update(root, root, self.run, 'harbor.example', 'erp', 'team/lab')
            self.assertEqual((chart / 'values.yaml').read_text(), before_values)
            self.assertEqual((root / 'versions.yaml').read_text(), before_lock)

    def test_artifact_registry_and_commit_mismatch_block_promotion(self):
        for field in ('GITHUB_SHA', 'HARBOR_REGISTRY', 'GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root, chart = self.fixture(directory)
                path = root / 'release.json'
                data = json.loads(path.read_text())
                data[field] = 'wrong'
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    update(root, root, self.run, 'harbor.example', 'erp', 'team/lab')

    def test_all_three_digests_and_source_are_locked(self):
        with tempfile.TemporaryDirectory() as directory:
            root, chart = self.fixture(directory)
            update(root, root, self.run, 'harbor.example', 'erp', 'team/lab')
            values = yaml.safe_load((chart / 'values.yaml').read_text())
            lock = yaml.safe_load((root / 'versions.yaml').read_text())
            for component in ('core', 'api', 'ui'):
                self.assertEqual(values[component]['image']['digest'], 'sha256:' + 'b' * 64)
                self.assertEqual(values[component]['image']['tag'], '')
            self.assertEqual(len(lock['images']), 3)
            self.assertEqual(lock['source_commit'], 'a' * 40)


    def real_chart_fixture(self, directory):
        root, chart = self.fixture(directory)
        source = Path(__file__).resolve().parents[1] / 'metasfresh-gitops/charts/erp-adapter'
        shutil.copytree(source, chart, dirs_exist_ok=True)
        return root, chart

    def test_promotion_accepts_real_helper_wrappers_and_sets_registry(self):
        with tempfile.TemporaryDirectory() as directory:
            root, chart = self.real_chart_fixture(directory)
            update(root, root, self.run, 'harbor.example', 'erp', 'team/lab')
            values = yaml.safe_load((chart / 'values.yaml').read_text(encoding='utf-8'))
            self.assertEqual(values['global']['registry'], 'harbor.example')
            for component, image in [('core', 'metas-app'), ('api', 'metas-api'),
                                     ('ui', 'metas-frontend')]:
                self.assertEqual(values[component]['image']['repository'],
                                 'harbor.example/erp/' + image)
                self.assertEqual(values[component]['image']['digest'], 'sha256:' + 'b' * 64)
                self.assertEqual(values[component]['image']['tag'], '')

    def test_other_helper_digest_marker_cannot_hide_broken_deployment(self):
        with tempfile.TemporaryDirectory() as directory:
            root, chart = self.real_chart_fixture(directory)
            helper = chart / 'templates/_helpers.tpl'
            text = helper.read_text(encoding='utf-8').replace('$cfg.image.digest', '$cfg.image.tag')
            text += '\n{{ define "unrelated" }} .Values.ui.image.digest {{ end }}\n'
            helper.write_text(text, encoding='utf-8')
            before = [(path, path.read_bytes()) for path in
                      (chart / 'values.yaml', root / 'versions.yaml')]
            with self.assertRaisesRegex(ValueError, 'digest-aware chart'):
                update(root, root, self.run, 'harbor.example', 'erp', 'team/lab')
            for path, content in before:
                self.assertEqual(path.read_bytes(), content)

    def test_wrapper_for_wrong_component_blocks_release_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root, chart = self.real_chart_fixture(directory)
            template = chart / 'templates/deployment-api.yaml'
            template.write_text(template.read_text().replace('"api"', '"core"'))
            before = [(path, path.read_bytes()) for path in
                      (chart / 'values.yaml', root / 'versions.yaml')]
            with self.assertRaisesRegex(ValueError, 'digest-aware chart'):
                update(root, root, self.run, 'harbor.example', 'erp', 'team/lab')
            for path, content in before:
                self.assertEqual(path.read_bytes(), content)

    def test_promoted_real_chart_renders_all_three_digest_images(self):
        helm = os.environ.get('TEST_HELM') or shutil.which('helm')
        if not helm:
            self.skipTest('Helm is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            root, chart = self.real_chart_fixture(directory)
            update(root, root, self.run, 'harbor.example', 'erp', 'team/lab')
            result = subprocess.run([helm, 'template', 'erp', str(chart), '--namespace', 'erp',
                                     '--set', 'validation.strict=false'],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            deployments = [doc for doc in yaml.safe_load_all(result.stdout)
                           if doc and doc.get('kind') == 'Deployment']
            self.assertEqual(len(deployments), 3)
            for document in deployments:
                component = document['spec']['template']['spec']['containers'][0]['name']
                expected_image = {'core': 'metas-app', 'api': 'metas-api',
                                  'ui': 'metas-frontend'}[component]
                image = document['spec']['template']['spec']['containers'][0]['image']
                self.assertEqual(image, 'harbor.example/erp/' + expected_image +
                                 '@sha256:' + 'b' * 64)

class DatabaseGuardTests(unittest.TestCase):
    def bash(self):
        return os.environ.get('TEST_BASH') or shutil.which('bash')

    def run_guard(self, target, production='metasfresh', checksum=None, nonempty=False):
        bash = self.bash()
        if not bash:
            self.skipTest('Bash is unavailable')
        with tempfile.TemporaryDirectory() as directory:
            tools = Path(directory)
            log = tools / 'calls.txt'
            for tool in ('psql', 'pg_dump', 'pg_restore', 'aws', 'python3'):
                script = '#!/usr/bin/env bash\n'
                if tool != 'python3':
                    script += f'echo {tool} >> "$MOCK_CALL_LOG"\n'
                if tool == 'psql':
                    script += 'echo 2\n' if nonempty else 'echo 0\n'
                script += 'exit 0\n'
                path = tools / tool
                path.write_text(script)
                path.chmod(0o755)
            env = dict(os.environ, PGHOST='db.example', PGPORT='5432', PGUSER='test',
                       PGPASSWORD='test-only', PGSSLMODE='verify-full', S3_ENDPOINT='https://s3.example',
                       BACKUP_BUCKET='lab-backups', AWS_ACCESS_KEY_ID='test-only',
                       AWS_SECRET_ACCESS_KEY='test-only', AWS_DEFAULT_REGION='test',
                       TARGET_DATABASE=target, PRODUCTION_DATABASE=production,
                       BACKUP_KEY='postgres/test.dump', EXPECTED_SHA256=checksum or 'c' * 64,
                       MOCK_CALL_LOG=str(log).replace('\\', '/'),
                       MOCK_BIN=str(tools).replace('\\', '/'))
            result = subprocess.run([bash, '-c', 'if command -v cygpath >/dev/null; then MOCK_BIN="$(cygpath -u "$MOCK_BIN")"; fi; export PATH="$MOCK_BIN:$PATH"; bash ci/database.sh restore'],
                                    env=env, capture_output=True, text=True)
            calls = log.read_text() if log.exists() else ''
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('aws', calls)
            self.assertNotIn('pg_restore', calls)
            return result.stderr + result.stdout

    def test_production_target_is_refused_before_io(self):
        self.assertIn('Production target prohibited', self.run_guard('lab_restore_prod', 'lab_restore_prod'))

    def test_invalid_target_or_checksum_is_refused_before_io(self):
        self.assertIn('isolated', self.run_guard('metasfresh'))
        self.assertIn('Invalid expected checksum', self.run_guard('lab_restore_test', checksum='bad'))

    def test_nonempty_target_is_refused_before_download(self):
        self.assertIn('Target is not empty', self.run_guard('lab_restore_test', nonempty=True))

class CredentialTests(unittest.TestCase):
    def test_pgpass_escaping_and_newline(self):
        script = Path('ci/database.sh').read_text()
        code = script.split("python3 - <<'PY'", 1)[1].split('\nPY', 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'pgpass'
            env = {'PGHOST': 'db.example', 'PGPORT': '5432', 'PGUSER': 'test',
                   'PGPASSWORD': 'colon:slash' + chr(92), 'PGPASSFILE': str(target)}
            with patch.dict(os.environ, env):
                exec(compile(code, 'pgpass-generator', 'exec'), {})
            expected = 'db.example:5432:*:test:colon' + chr(92) + ':slash' + chr(92) * 2 + chr(10)
            self.assertEqual(target.read_text(), expected)
if __name__ == '__main__':
    unittest.main()

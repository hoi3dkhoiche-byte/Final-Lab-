"""Validate complete build evidence before updating any release file."""
import argparse
import json
import os
import re
from pathlib import Path
import yaml
from verify_build import verify

def update(root, evidence_dir, build, registry, project, repository):
    source = verify(build, repository)
    root, evidence_dir = Path(root), Path(evidence_dir)
    evidence = json.loads((evidence_dir / 'release.json').read_text())
    expected = {'GITHUB_SHA': source, 'GITHUB_RUN_ID': str(build['id']),
                'GITHUB_RUN_ATTEMPT': str(build['run_attempt']),
                'HARBOR_REGISTRY': registry, 'HARBOR_PROJECT': project}
    if any(evidence.get(k) != v for k, v in expected.items()):
        raise ValueError('Artifact provenance or registry configuration mismatch')
    values_path = root / 'charts/erp-adapter/values.yaml'
    lock_path = root / 'versions.yaml'
    values = yaml.safe_load(values_path.read_text())
    lock = yaml.safe_load(lock_path.read_text())
    images = {}
    for component, image, key in [('core', 'metas-app', 'app'), ('api', 'metas-api', 'api'), ('ui', 'metas-frontend', 'frontend')]:
        digest = json.loads((evidence_dir / f'{image}.json').read_text())['digest']
        if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
            raise ValueError(f'Invalid digest for {image}')
        template = root / f'charts/erp-adapter/templates/deployment-{component}.yaml'
        if '.image.digest' not in template.read_text():
            raise ValueError(f'Install digest-aware chart first: {template}')
        image_repo = f'{registry}/{project}/{image}'
        values[component]['image'].update(repository=image_repo, tag='', digest=digest)
        images[key] = f'{image_repo}@{digest}'
    lock.update(images=images, source_commit=source, pipeline_url=build['html_url'])
    values_path.write_text(yaml.safe_dump(values, sort_keys=False))
    lock_path.write_text(yaml.safe_dump(lock, sort_keys=False))

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--build-run', required=True)
    args = parser.parse_args()
    update('gitops-release', 'ci-output', json.loads(Path(args.build_run).read_text()),
           os.environ['HARBOR_REGISTRY'], os.environ['HARBOR_PROJECT'], os.environ['GITHUB_REPOSITORY'])

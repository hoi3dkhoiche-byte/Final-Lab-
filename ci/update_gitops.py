import json
import os
import re
from pathlib import Path
import yaml

root = Path('gitops-release')
values_path = root / 'charts/erp-adapter/values.yaml'
lock_path = root / 'versions.yaml'
values = yaml.safe_load(values_path.read_text())
lock = yaml.safe_load(lock_path.read_text())
images = {}
for component, image, key in [('core', 'metas-app', 'app'), ('api', 'metas-api', 'api'), ('ui', 'metas-frontend', 'frontend')]:
    digest = json.loads(Path(f'ci-output/{image}.json').read_text())['digest']
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
        raise ValueError(f'Invalid digest: {image}')
    template = root / f'charts/erp-adapter/templates/deployment-{component}.yaml'
    if '.image.digest' not in template.read_text():
        raise ValueError(f'Install digest-aware chart first: {template}')
    repository = f"{os.environ['HARBOR_REGISTRY']}/{os.environ['HARBOR_PROJECT']}/{image}"
    values[component]['image'].update(repository=repository, tag='', digest=digest)
    images[key] = f'{repository}@{digest}'
lock['images'] = images
lock['source_commit'] = os.environ['GITHUB_SHA']
lock['pipeline_url'] = f"{os.environ['GITHUB_SERVER_URL']}/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
values_path.write_text(yaml.safe_dump(values, sort_keys=False))
lock_path.write_text(yaml.safe_dump(lock, sort_keys=False))

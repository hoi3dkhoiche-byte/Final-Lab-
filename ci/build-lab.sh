#!/usr/bin/env bash
set -euo pipefail
: "${RELEASE_ID:?}"
: "${METASFRESH_PACKAGES_READ_TOKEN:?}"
mkdir -p ci-output/reports
trap 'rm -f ci-output/settings.xml' EXIT
python3 - <<'PY'
import os
from pathlib import Path
from xml.sax.saxutils import escape
p = Path('ci-output/settings.xml')
p.write_text(Path('docker-builds/mvn/settings.xml').read_text().replace('$METASFRESH_PACKAGES_READ_TOKEN', escape(os.environ['METASFRESH_PACKAGES_READ_TOKEN'])))
p.chmod(0o600)
PY
printf 'build.version=%s-lab.%s\nbuild.number=%s\n' "$(cat docker-builds/version.info)" "$RELEASE_ID" "$RELEASE_ID" > docker-builds/metadata/build-info.properties
printf 'git.commit.id=%s\n' "$GITHUB_SHA" > docker-builds/metadata/git.properties
build() {
  docker build --progress=plain -f "docker-builds/$1" --build-arg REGISTRY= --build-arg "REFNAME=$RELEASE_ID" --secret id=mvn-settings,src=ci-output/settings.xml -t "$2:$RELEASE_ID" .
}
build Dockerfile.common metasfresh/metas-mvn-common
build Dockerfile.backend metasfresh/metas-mvn-backend
build Dockerfile.junit lab/metas-junit
docker run --rm --entrypoint bash "lab/metas-junit:$RELEASE_ID" -c 'for dir in /java/commons /java/backend; do for report in junit.exit-code junit.mvn.exit-code; do test "$(cat "$dir/$report")" = 0 || exit 1; done; done'
docker run --rm -v "$PWD/ci-output/reports:/reports" "lab/metas-junit:$RELEASE_ID"
python3 - <<'PY'
from pathlib import Path
import xml.etree.ElementTree as ET
files = list(Path('ci-output/reports').rglob('*.xml'))
assert files, 'Missing JUnit reports'
cases = 0
for p in files:
    root = ET.parse(p).getroot()
    cases += len(list(root.iter('testcase')))
    assert not any(n.tag in ('failure', 'error') for n in root.iter()), p
    for suite in root.iter('testsuite'):
        assert int(suite.get('failures', 0)) == 0 and int(suite.get('errors', 0)) == 0, p
assert cases, 'No test cases'
PY
build Dockerfile.backend.app lab/metas-app
build Dockerfile.backend.api lab/metas-api
docker build -f docker-builds/Dockerfile.frontend --target test -t "lab/frontend-test:$RELEASE_ID" .
docker run --rm --entrypoint bash "lab/frontend-test:$RELEASE_ID" -c 'test "$(cat /webui/jest.exit-code)" = 0'
docker run --rm -v "$PWD/ci-output/reports:/reports" "lab/frontend-test:$RELEASE_ID"
build Dockerfile.frontend lab/metas-frontend

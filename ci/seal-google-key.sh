#!/usr/bin/env bash
set -euo pipefail
umask 077
# Google is optional; never pass the key to Docker builds or write it into Git.
if [[ -z "${GOOGLE_API_KEY:-}" ]]; then
  echo 'GOOGLE_API_KEY not configured; retaining existing optional Maps configuration'
  exit 0
fi
: "${SEALED_SECRETS_CERT_BASE64:?Public sealing certificate required}"
for tool in kubectl kubeseal python3; do command -v "$tool" >/dev/null; done
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
printf '%s' "$SEALED_SECRETS_CERT_BASE64" | base64 --decode > "$work/cert.pem"
printf '%s' "$GOOGLE_API_KEY" > "$work/key"
unset GOOGLE_API_KEY
# strict scope: erp/google-api-key. Runtime chart namespace must match.
python3 - <<'PY'
import yaml
from pathlib import Path
values = yaml.safe_load(Path('gitops-release/charts/erp-adapter/values.yaml').read_text())
if values.get('global', {}).get('namespace') != 'erp':
    raise ValueError('Google sealed secret requires namespace erp')
for component in ('core', 'api'):
    text = Path(f'gitops-release/charts/erp-adapter/templates/deployment-{component}.yaml').read_text()
    if 'GOOGLE_API_KEY' not in text:
        raise ValueError('Install runtime Google secret chart support first')
PY
kubectl create secret generic google-api-key --namespace erp --dry-run=client \
  --from-file=GOOGLE_API_KEY="$work/key" -o json > "$work/secret.json"
kubeseal --cert "$work/cert.pem" --scope strict --format yaml < "$work/secret.json" \
  > gitops-release/charts/erp-adapter/templates/google-api-sealed-secret.yaml

#!/usr/bin/env bash
set -euo pipefail
umask 077
mode="${1:?backup, restore or seed required}"
: "${PGHOST:?}" "${PGPORT:?}" "${PGUSER:?}" "${PGPASSWORD:?}" "${PGSSLMODE:?}" "${S3_ENDPOINT:?}" "${BACKUP_BUCKET:?}" "${AWS_ACCESS_KEY_ID:?}" "${AWS_SECRET_ACCESS_KEY:?}" "${AWS_DEFAULT_REGION:?}"
for tool in psql pg_dump pg_restore aws sha256sum python3; do command -v "$tool" >/dev/null; done
case "$mode" in backup|restore|seed) ;; *) exit 1 ;; esac
[[ "$BACKUP_BUCKET" =~ ^[a-z0-9][a-z0-9.-]+$ ]] || { echo 'Invalid bucket'; exit 1; }
[[ "$S3_ENDPOINT" == https://* ]] || { echo 'S3 endpoint requires HTTPS'; exit 1; }
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
export PGPASSFILE="$work/pgpass"
python3 - <<'PY'
import os
from pathlib import Path
escape = lambda value: value.replace('\\', '\\\\').replace(':', '\\:')
fields = [os.environ['PGHOST'], os.environ['PGPORT'], '*', os.environ['PGUSER'], os.environ['PGPASSWORD']]
if any('\n' in field or '\r' in field for field in fields):
    raise ValueError('Invalid PostgreSQL credential fields')
Path(os.environ['PGPASSFILE']).write_text(':'.join(map(escape, fields)) + '\n')
PY
unset PGPASSWORD
s3() { aws --endpoint-url "$S3_ENDPOINT" s3 "$@"; }
verify_pg_tools() {
  # Workflows set this to the actual RDS major. Local legacy callers can omit it.
  if [[ -z "${PG_EXPECTED_MAJOR:-}" ]]; then return 0; fi
  [[ "$PG_EXPECTED_MAJOR" =~ ^[0-9]+$ ]] || { echo 'Invalid PG_EXPECTED_MAJOR'; exit 1; }
  local server_num server_major client_version client_major tool
  server_num="$(psql -XAt -v ON_ERROR_STOP=1 -c 'SHOW server_version_num;')"
  [[ "$server_num" =~ ^[0-9]+$ ]] || { echo 'Cannot determine PostgreSQL server version'; exit 1; }
  server_major=$((server_num / 10000))
  test "$server_major" -eq "$PG_EXPECTED_MAJOR" || { echo 'PostgreSQL server major differs from configured RDS'; exit 1; }
  for tool in pg_dump pg_restore; do
    client_version="$("$tool" --version)"
    [[ "$client_version" =~ ([0-9]+)\. ]] || { echo 'Cannot determine PostgreSQL client version'; exit 1; }
    client_major="${BASH_REMATCH[1]}"
    test "$client_major" -ge "$server_major" || { echo 'PostgreSQL client is older than server; install PostgreSQL18 tools'; exit 1; }
  done
}
if [[ "$mode" == backup ]]; then
  : "${PGDATABASE:?}"
  [[ "$PGDATABASE" =~ ^[a-zA-Z][a-zA-Z0-9_]{0,62}$ ]] || exit 1
  id="$(date -u +%Y%m%dT%H%M%SZ)-$GITHUB_RUN_ID-$GITHUB_RUN_ATTEMPT"
  key="postgres/$PGDATABASE/$id.dump"
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  verify_pg_tools
  pg_dump --format=custom --file="$work/database.dump"
  checksum="$(sha256sum "$work/database.dump" | cut -d ' ' -f1)"
  printf '%s  database.dump\n' "$checksum" > "$work/database.dump.sha256"
  s3 cp "$work/database.dump" "s3://$BACKUP_BUCKET/$key" --only-show-errors
  s3 cp "$work/database.dump.sha256" "s3://$BACKUP_BUCKET/$key.sha256" --only-show-errors
  s3 cp "s3://$BACKUP_BUCKET/$key" "$work/verify.dump" --only-show-errors
  test "$(sha256sum "$work/verify.dump" | cut -d ' ' -f1)" = "$checksum"
  printf 'Backup start UTC: %s\nObject: %s\nSHA256: %s\nVerified UTC: %s\n' "$started" "$key" "$checksum" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$GITHUB_STEP_SUMMARY"
else
  : "${TARGET_DATABASE:?}" "${BACKUP_KEY:?}" "${EXPECTED_SHA256:?}" "${PRODUCTION_DATABASE:?}"
  [[ "$TARGET_DATABASE" =~ ^lab_(restore|seed)_[a-z0-9_]+$ && ${#TARGET_DATABASE} -le 63 ]] || { echo 'Use isolated lab_restore_* or lab_seed_* target'; exit 1; }
  test "$TARGET_DATABASE" != "$PRODUCTION_DATABASE" || { echo 'Production target prohibited'; exit 1; }
  [[ "$EXPECTED_SHA256" =~ ^[a-f0-9]{64}$ ]] || { echo 'Invalid expected checksum'; exit 1; }
  [[ "$BACKUP_KEY" != /* && "$BACKUP_KEY" != *'..'* && "$BACKUP_KEY" != *$'\n'* ]] || exit 1
  export PGDATABASE="$TARGET_DATABASE"
  verify_pg_tools
  count="$(psql -XAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','S','f');")"
  test "$count" = 0 || { echo 'Target is not empty'; exit 1; }
  s3 cp "s3://$BACKUP_BUCKET/$BACKUP_KEY" "$work/database.dump" --only-show-errors
  printf '%s  %s\n' "$EXPECTED_SHA256" "$work/database.dump" | sha256sum -c -
  pg_restore --list "$work/database.dump" >/dev/null
  pg_restore --exit-on-error --single-transaction --no-owner --no-privileges --dbname="$TARGET_DATABASE" "$work/database.dump"
  psql -XAt -v ON_ERROR_STOP=1 -c "SELECT current_database(), count(*) FROM information_schema.tables WHERE table_schema NOT IN ('pg_catalog','information_schema') GROUP BY current_database();" >> "$GITHUB_STEP_SUMMARY"
  printf 'Completed UTC: %s\nIsolated import only; business validation and roles/grants still required.\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$GITHUB_STEP_SUMMARY"
fi

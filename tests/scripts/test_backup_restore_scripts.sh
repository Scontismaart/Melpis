#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(/usr/bin/dirname "$0")/../.." && pwd)
clean_path=/usr/bin:/bin
tmp=$(/usr/bin/mktemp -d)
trap '/usr/bin/rm -rf -- "$tmp"' EXIT

expect_fail() {
  local expected=$1; shift
  local output
  if output=$("$@" 2>&1); then
    printf 'expected failure: %s\n' "$*" >&2; return 1
  fi
  [[ "$output" == *"$expected"* ]] || { printf 'missing %q in: %s\n' "$expected" "$output" >&2; return 1; }
}

expect_fail 'BACKUP_PGHOST is required' /usr/bin/env -i PATH="$clean_path" HOME="${HOME:-/tmp}" /usr/bin/bash "$root/scripts/backup_supabase_r2.sh"

# Test a copied script with a test-only rewritten compile-time root. Production
# code has no config-root override; these cases make no provider calls.
config_root="$tmp/backup-drill-targets"
/usr/bin/mkdir -p "$config_root"
/usr/bin/chmod 700 "$tmp" "$config_root"
test_restore="$tmp/restore.sh"
/usr/bin/sed \
  -e "s|^readonly TARGET_CONFIG_ROOT=.*|readonly TARGET_CONFIG_ROOT=\"$config_root\"|" \
  -e "s|^readonly TARGET_CONFIG_OWNER_UID=.*|readonly TARGET_CONFIG_OWNER_UID=$(/usr/bin/id -u)|" \
  "$root/scripts/restore_supabase_drill.sh" > "$test_restore"
/usr/bin/chmod 700 "$test_restore"

write_config() {
  /usr/bin/printf '%s\n' \
    'TARGET_ID=drill-safe' 'TARGET_ENV=drill' 'PGHOST=db.drill.example' 'PGPORT=5432' \
    'PGDATABASE=drill_db' 'PGUSER=drill_restore' 'PGPASSWORD=not-a-real-secret' \
    > "$config_root/drill-safe.conf"
  /usr/bin/chmod 600 "$config_root/drill-safe.conf"
}

# Caller-provided production labels/credentials cannot make an unlisted target
# runnable: the fixed allowlist path is consulted before tools/providers.
expect_fail 'requested target is not allowlisted' /usr/bin/env -i PATH="$clean_path" HOME="${HOME:-/tmp}" \
  RESTORE_NON_PRODUCTION_CONFIRMATION=RESTORE_attacker RESTORE_TARGET_ENV=drill \
  RESTORE_PGHOST=prod-hidden.example RESTORE_PGDATABASE=live PGHOST=prod-hidden.example PGPASSWORD=production \
  /usr/bin/bash "$test_restore" --target attacker --artifact-key supabase-backups/supabase-20260920T010203Z-1.dump.age

write_config
expect_fail 'BACKUP_AGE_IDENTITY_FILE is required' /usr/bin/env -i PATH="$clean_path" HOME="${HOME:-/tmp}" \
  RESTORE_NON_PRODUCTION_CONFIRMATION=RESTORE_drill-safe PGHOST=prod-hidden.example PGPASSWORD=production \
  /usr/bin/bash "$test_restore" --target drill-safe --artifact-key supabase-backups/supabase-20260920T010203Z-1.dump.age

fake_bin="$tmp/fake-bin"
/usr/bin/mkdir "$fake_bin"
/usr/bin/printf '%s\n' '#!/usr/bin/env bash' \
  'if [[ "$1" == "-c" && "$2" == "%a" && "$3" == *drill-safe.conf ]]; then printf "644\\n"; else exec /usr/bin/stat "$@"; fi' \
  > "$fake_bin/stat"
/usr/bin/chmod 700 "$fake_bin/stat"
test_restore_insecure="$tmp/restore-insecure.sh"
/usr/bin/sed \
  -e "s|^readonly TARGET_CONFIG_ROOT=.*|readonly TARGET_CONFIG_ROOT=\"$config_root\"|" \
  -e "s|^readonly TARGET_CONFIG_OWNER_UID=.*|readonly TARGET_CONFIG_OWNER_UID=$(/usr/bin/id -u)|" \
  -e "s|/usr/bin/stat|$fake_bin/stat|g" \
  "$root/scripts/restore_supabase_drill.sh" > "$test_restore_insecure"
/usr/bin/chmod 700 "$test_restore_insecure"
expect_fail 'target configuration must be owner-only' /usr/bin/env -i PATH="$clean_path" HOME="${HOME:-/tmp}" \
  RESTORE_NON_PRODUCTION_CONFIRMATION=RESTORE_drill-safe \
  /usr/bin/bash "$test_restore_insecure" --target drill-safe --artifact-key supabase-backups/supabase-20260920T010203Z-1.dump.age

write_config
/usr/bin/sed -i 's/^TARGET_ENV=drill$/TARGET_ENV=production/' "$config_root/drill-safe.conf"
expect_fail 'target configuration is not marked non-production' /usr/bin/env -i PATH="$clean_path" HOME="${HOME:-/tmp}" \
  RESTORE_NON_PRODUCTION_CONFIRMATION=RESTORE_drill-safe \
  /usr/bin/bash "$test_restore" --target drill-safe --artifact-key supabase-backups/supabase-20260920T010203Z-1.dump.age
printf 'backup/restore script guard tests passed\n'

# Isolate the rotation block with a simulated expired-object inventory.
# A failed list or unexpected key must never trigger a deletion.
rotation="$tmp/rotation.sh"
rotation_log="$tmp/rotation.log"
rotation_aws="$fake_bin/aws"
/usr/bin/awk '/^# Preserve a recoverable backup/{capture=1} /^printf '\''backup: verified encrypted artifact uploaded/{capture=0} capture' \
  "$root/scripts/backup_supabase_r2.sh" | /usr/bin/sed "s|/usr/bin/aws|$rotation_aws|g" > "$rotation"
/usr/bin/printf '%s\n' '#!/usr/bin/env bash' \
  'if [[ "$3" == "s3api" && "$4" == "list-objects-v2" ]]; then printf "%s" "$TEST_KEYS"; exit "$TEST_LIST_STATUS"; fi' \
  'if [[ "$3" == "s3api" && "$4" == "delete-object" ]]; then printf "%s\n" "$8" >> "$TEST_LOG"; exit 0; fi' \
  'exit 99' > "$rotation_aws"
/usr/bin/chmod 700 "$rotation_aws"
run_rotation() {
  TEST_KEYS="$1" TEST_LIST_STATUS="$2" TEST_LOG="$rotation_log" /usr/bin/bash -c '
    set -euo pipefail
    die() { printf "%s\n" "$*" >&2; exit 1; }
    RETENTION_DAYS=7
    R2_ENDPOINT=https://test.invalid
    R2_BUCKET=synthetic-backups
    prefix=synthetic
    source "$1"
  ' _ "$rotation"
}
old_key=synthetic/supabase-20261001T010203Z-1.dump.age
expect_fail 'R2 retention inventory listing failed' run_rotation "$old_key" 1
[[ ! -e "$rotation_log" ]]
expect_fail 'refusing to delete unexpected R2 key' run_rotation "$old_key"$'\n'synthetic/foreign-object 0
[[ ! -e "$rotation_log" ]]
run_rotation "$old_key"$'\t'"$old_key.sha256" 0
[[ "$(/usr/bin/wc -l < "$rotation_log")" -eq 2 ]]
printf 'backup rotation inventory guard tests passed\n'

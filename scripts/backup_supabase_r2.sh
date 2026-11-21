#!/bin/bash
# Create an encrypted PostgreSQL backup and upload it to Cloudflare R2.
# Intentionally does not enable xtrace: this process receives credentials.
set -euo pipefail
IFS=$'\n\t'
umask 077
export PATH=/usr/bin:/bin

readonly RETENTION_DAYS=7
readonly DEFAULT_PREFIX="supabase-backups"
work_dir=""

die() { printf 'backup: %s\n' "$*" >&2; exit 1; }
require_env() { [[ -n "${!1:-}" ]] || die "$1 is required"; }
require_binary() { [[ -x "$1" ]] || die "required trusted binary not found: $1"; }
cleanup() { [[ -z "$work_dir" ]] || /usr/bin/rm -rf -- "$work_dir"; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

# Database credentials are deliberately supplied as libpq environment variables,
# never interpolated into a connection string or command-line argument.
for name in BACKUP_PGHOST BACKUP_PGPORT BACKUP_PGDATABASE BACKUP_PGUSER BACKUP_PGPASSWORD \
  BACKUP_AGE_RECIPIENT BACKUP_AGE_IDENTITY_FILE R2_ENDPOINT R2_BUCKET R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY; do
  require_env "$name"
done

[[ "${BACKUP_PGSSLMODE:-verify-full}" == "verify-full" ]] || die "BACKUP_PGSSLMODE must be verify-full"
[[ "$R2_ENDPOINT" =~ ^https:// ]] || die "R2_ENDPOINT must be an HTTPS URL"
[[ "$R2_BUCKET" =~ ^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$ ]] || die "R2_BUCKET is not a valid bucket name"
[[ "$BACKUP_AGE_RECIPIENT" == age1* ]] || die "BACKUP_AGE_RECIPIENT must be an age recipient"

require_binary /usr/bin/pg_dump
require_binary /usr/bin/pg_restore
require_binary /usr/bin/age
require_binary /usr/bin/aws
require_binary /usr/bin/sha256sum
require_binary /usr/bin/date
require_binary /usr/bin/stat
require_binary /usr/bin/mktemp
require_binary /usr/bin/chmod
require_binary /usr/bin/rm
require_binary /usr/bin/awk
require_binary /usr/bin/wc
require_binary /usr/bin/tr
require_binary /usr/bin/cmp
[[ -r "$BACKUP_AGE_IDENTITY_FILE" ]] || die "BACKUP_AGE_IDENTITY_FILE is not readable"
identity_mode=$(/usr/bin/stat -c '%a' "$BACKUP_AGE_IDENTITY_FILE")
(( (8#$identity_mode & 077) == 0 )) || die "BACKUP_AGE_IDENTITY_FILE must not be group/world readable"

export PGHOST="$BACKUP_PGHOST" PGPORT="$BACKUP_PGPORT" PGDATABASE="$BACKUP_PGDATABASE"
export PGUSER="$BACKUP_PGUSER" PGPASSWORD="$BACKUP_PGPASSWORD"
export PGSSLMODE="verify-full"
[[ -z "${BACKUP_PGSSLROOTCERT:-}" ]] || export PGSSLROOTCERT="$BACKUP_PGSSLROOTCERT"
export AWS_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID" AWS_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export AWS_EC2_METADATA_DISABLED=true
export AWS_DEFAULT_REGION="${R2_AWS_REGION:-auto}"

prefix="${R2_PREFIX:-$DEFAULT_PREFIX}"
prefix="${prefix#/}"
prefix="${prefix%/}"
[[ "$prefix" =~ ^[A-Za-z0-9._/-]+$ ]] || die "R2_PREFIX contains unsupported characters"

work_dir=$(/usr/bin/mktemp -d "/var/tmp/supabase-r2-backup.XXXXXX")
/usr/bin/chmod 700 "$work_dir"
timestamp=$(/usr/bin/date -u +%Y%m%dT%H%M%SZ)
artifact_base="supabase-${timestamp}-${RANDOM}.dump.age"
dump_file="$work_dir/database.dump"
artifact_file="$work_dir/$artifact_base"
manifest_file="$work_dir/$artifact_base.sha256"
artifact_key="$prefix/$artifact_base"
manifest_key="$artifact_key.sha256"

# The custom format supports pg_restore verification and a transactional drill.
/usr/bin/pg_dump --format=custom --no-owner --no-privileges --file="$dump_file"
/usr/bin/age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" --output "$artifact_file" "$dump_file"
/usr/bin/rm -f -- "$dump_file"

# Authenticate/decrypt locally before an external copy is accepted as a backup.
# age returns non-zero for malformed or unauthenticated ciphertext.
/usr/bin/age --decrypt --identity "$BACKUP_AGE_IDENTITY_FILE" "$artifact_file" \
  | /usr/bin/pg_restore --list >/dev/null 2>&1 || die "encrypted artifact failed local authentication/archive verification"
/usr/bin/sha256sum "$artifact_file" | /usr/bin/awk -v n="$artifact_base" '{print $1 "  " n}' > "$manifest_file"

/usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api put-object --bucket "$R2_BUCKET" --key "$artifact_key" \
  --body "$artifact_file" --metadata "sha256=$(/usr/bin/awk '{print $1}' "$manifest_file")" --no-progress >/dev/null
/usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api put-object --bucket "$R2_BUCKET" --key "$manifest_key" \
  --body "$manifest_file" --content-type text/plain --no-progress >/dev/null

# Confirm R2 stored the complete object and immutable digest metadata.
remote_size=$(/usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api head-object --bucket "$R2_BUCKET" --key "$artifact_key" --query ContentLength --output text)
local_size=$(/usr/bin/wc -c < "$artifact_file" | /usr/bin/tr -d '[:space:]')
remote_sha=$(/usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api head-object --bucket "$R2_BUCKET" --key "$artifact_key" --query 'Metadata.sha256' --output text)
local_sha=$(/usr/bin/awk '{print $1}' "$manifest_file")
[[ "$remote_size" == "$local_size" && "$remote_sha" == "$local_sha" ]] || die "R2 post-upload verification failed"

# Metadata alone does not establish integrity of the stored bytes.
/usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api get-object --bucket "$R2_BUCKET" --key "$artifact_key" "$work_dir/readback.age" >/dev/null
/usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api get-object --bucket "$R2_BUCKET" --key "$manifest_key" "$work_dir/readback.sha256" >/dev/null
/usr/bin/cmp -s "$artifact_file" "$work_dir/readback.age" &&
  /usr/bin/cmp -s "$manifest_file" "$work_dir/readback.sha256" || die "R2 ciphertext or manifest readback mismatch"

# Preserve a recoverable backup during an outage. Inventory and validate the
# complete rotation set only after the replacement has been verified.
cutoff=$(/usr/bin/date -u -d "${RETENTION_DAYS} days ago" +%Y-%m-%dT%H:%M:%SZ)
old_keys=$(/usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api list-objects-v2 --bucket "$R2_BUCKET" --prefix "$prefix/" \
  --query "Contents[?LastModified<=\`${cutoff}\`].Key" --output text) || die "R2 retention inventory listing failed"
rotation_keys=()
while IFS= read -r old_key; do
  [[ -z "$old_key" || "$old_key" == "None" ]] && continue
  old_base=${old_key##*/}
  [[ "$old_key" == "$prefix/$old_base" ]] || die "refusing to delete nested R2 key"
  [[ "$old_base" =~ ^supabase-[0-9]{8}T[0-9]{6}Z-[0-9]+\.dump\.age(\.sha256)?$ ]] \
    || die "refusing to delete unexpected R2 key"
  rotation_keys+=("$old_key")
done <<< "$(/usr/bin/tr '\t' '\n' <<< "$old_keys")"
for old_key in "${rotation_keys[@]}"; do
  /usr/bin/aws --endpoint-url "$R2_ENDPOINT" s3api delete-object --bucket "$R2_BUCKET" --key "$old_key" >/dev/null
done

printf 'backup: verified encrypted artifact uploaded: s3://%s/%s\n' "$R2_BUCKET" "$artifact_key"

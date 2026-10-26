#!/bin/sh
# Gitleaks 8.30.1 can log Git errors and still exit zero. Fail closed outside it.
set -eu
umask 077

fail() {
    printf '%s\n' "Gitleaks gate failed: $1" >&2
    exit 1
}

repository=${1:-/repo}
history='--full-history --diff-filter=tuxdb HEAD'
private_dir=$(mktemp -d /tmp/melpis-gitleaks.XXXXXX) || fail 'temporary directory unavailable'
diagnostics=$private_dir/diagnostics
trap 'rm -f "$diagnostics"; rmdir "$private_dir"' EXIT
trap 'exit 1' HUP INT TERM

if ! inside=$(git -C "$repository" rev-parse --is-inside-work-tree 2>"$diagnostics"); then
    fail 'repository validation failed'
fi
[ "$inside" = true ] && [ ! -s "$diagnostics" ] || fail 'invalid working repository'

if ! head_before=$(git -C "$repository" rev-parse --verify 'HEAD^{commit}' 2>"$diagnostics"); then
    fail 'HEAD is not a valid commit'
fi
[ ! -s "$diagnostics" ] || fail 'HEAD validation logged diagnostics'

if ! shallow=$(git -C "$repository" rev-parse --is-shallow-repository 2>"$diagnostics"); then
    fail 'history completeness check failed'
fi
[ "$shallow" = false ] && [ ! -s "$diagnostics" ] || fail 'complete history is required'

# Match Gitleaks' Git invocation, without emitting potentially sensitive diffs.
if ! git -C "$repository" log -p -U0 --full-history --diff-filter=tuxdb HEAD \
    >/dev/null 2>"$diagnostics"; then
    fail 'Git history traversal failed'
fi
[ ! -s "$diagnostics" ] || fail 'Git history traversal logged diagnostics'

# The logger threshold separates errors from ordinary INFO/WARN messages.
# Capture both streams: neither findings nor malformed input may reach CI logs.
if ! gitleaks git --redact=100 --no-banner --no-color --log-level error \
    --log-opts="$history" "$repository" >"$diagnostics" 2>&1; then
    fail 'scanner found secrets or returned an error'
fi
[ ! -s "$diagnostics" ] || fail 'scanner logged an error or unexpected output'

# CI supplies an immutable checkout; also detect ordinary checkout changes.
if ! head_after=$(git -C "$repository" rev-parse --verify 'HEAD^{commit}' 2>"$diagnostics"); then
    fail 'HEAD validation after scanning failed'
fi
[ ! -s "$diagnostics" ] && [ "$head_before" = "$head_after" ] || fail 'HEAD changed during scanning'
printf '%s\n' 'Gitleaks gate passed: complete candidate history scanned.'

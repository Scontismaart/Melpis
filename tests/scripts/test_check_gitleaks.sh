#!/bin/sh
# Disposable Linux fixtures; synthetic markers are generated only under /tmp.
set -eu
umask 077
gate=${1:?gate script is required}
root=$(mktemp -d /tmp/melpis-gitleaks-tests.XXXXXX)
trap 'rm -rf "$root"' EXIT
trap 'exit 1' HUP INT TERM
real_git=$(command -v git)
original_path=$PATH
export GITLEAKS_TEST_REAL_GIT=$real_git

die() { printf 'FAIL: %s\n' "$1" >&2; exit 1; }
init_repo() {
    mkdir "$1"
    git -C "$1" init -q -b main
    git -C "$1" config user.name 'Synthetic security fixture'
    git -C "$1" config user.email 'security@example.invalid'
    printf 'clean\n' >"$1/README"
    git -C "$1" add README
    git -C "$1" commit -qm clean
}
expect_pass() {
    if ! sh "$gate" "$2" >"$root/result" 2>&1; then die "$1"; fi
    printf 'PASS: %s\n' "$1"
}
expect_fail() {
    if sh "$gate" "$2" >"$root/result" 2>&1; then die "$1"; fi
    grep -q '^Gitleaks gate failed:' "$root/result" || die "$1 did not fail through gate"
    # Never permit synthetic credentials to appear even on failure.
    if grep -Eq 'gh[p]_[A-Za-z0-9]{36}' "$root/result"; then die 'secret exposed'; fi
    printf 'PASS: %s\n' "$1"
}
add_synthetic_secret() {
    printf 'gh%s_%s%s%s\n' p 'aB7cD8eF9gH0' 'iJ1kL2mN3oP4' 'qR5sT6uV7wX8' >"$1/synthetic"
    git -C "$1" add synthetic
    git -C "$1" commit -qm 'synthetic historical finding'
}

clean=$root/clean
init_repo "$clean"
expect_pass 'clean repository' "$clean"
git -C "$clean" switch -qc independent
add_synthetic_secret "$clean"
git -C "$clean" switch -q main
expect_pass 'independent branch is excluded' "$clean"
add_synthetic_secret "$clean"
git -C "$clean" rm -q synthetic
git -C "$clean" commit -qm 'delete synthetic finding'
expect_fail 'deleted secret in earlier candidate commit' "$clean"

invalid=$root/invalid
init_repo "$invalid"
git -C "$invalid" symbolic-ref HEAD refs/heads/nonexistent
expect_fail 'nonexistent HEAD revision' "$invalid"
mkdir "$root/empty" "$root/not-git"
git -C "$root/empty" init -q
expect_fail 'repository without HEAD' "$root/empty"
expect_fail 'not a Git repository' "$root/not-git"

corrupt=$root/corrupt
init_repo "$corrupt"
blob=$(git -C "$corrupt" rev-parse HEAD:README)
prefix=$(printf '%s' "$blob" | cut -c1-2)
suffix=$(printf '%s' "$blob" | cut -c3-)
rm "$corrupt/.git/objects/$prefix/$suffix"
expect_fail 'Git object read failure' "$corrupt"

shallow=$root/shallow
init_repo "$shallow"
git -C "$shallow" rev-parse HEAD >"$shallow/.git/shallow"
expect_fail 'shallow history is rejected' "$shallow"

runtime=$root/runtime
init_repo "$runtime"
mkdir "$root/git-shim" "$root/scanner-shim"
cat >"$root/git-shim/git" <<'SHIM'
#!/bin/sh
for argument in "$@"; do
    if [ "$argument" = log ]; then
        count=0
        [ ! -f "$GITLEAKS_TEST_COUNT" ] || count=$(cat "$GITLEAKS_TEST_COUNT")
        count=$((count + 1))
        printf '%s\n' "$count" >"$GITLEAKS_TEST_COUNT"
        if [ "$count" -ge 2 ]; then
            printf 'fatal: synthetic Git scan read failure\n' >&2
            exit 128
        fi
    fi
done
exec "$GITLEAKS_TEST_REAL_GIT" "$@"
SHIM
chmod +x "$root/git-shim/git"
export GITLEAKS_TEST_COUNT=$root/git-count
PATH=$root/git-shim:$original_path
export PATH
expect_fail 'Git error during scanner execution' "$runtime"
[ "$(cat "$GITLEAKS_TEST_COUNT")" -ge 2 ] || die 'Git scan fault was not reached'
grep -q 'scanner logged an error' "$root/result" || die 'Git scanner error was not detected'
PATH=$original_path
export PATH

cat >"$root/scanner-shim/gitleaks" <<'SHIM'
#!/bin/sh
case "$GITLEAKS_TEST_SCANNER_MODE" in
    stderr) printf 'synthetic scanner error\n' >&2 ;;
    stdout) printf 'synthetic scanner error\n' ;;
    nonzero) exit 2 ;;
    benign)
        previous=
        for argument in "$@"; do
            if [ "$previous" = --log-level ] && [ "$argument" = error ]; then exit 0; fi
            previous=$argument
        done
        printf 'INF synthetic information\nWRN synthetic warning\n' >&2
        ;;
esac
exit 0
SHIM
chmod +x "$root/scanner-shim/gitleaks"
PATH=$root/scanner-shim:$original_path
export PATH
for mode in stderr stdout nonzero; do
    export GITLEAKS_TEST_SCANNER_MODE=$mode
    expect_fail "scanner $mode failure" "$runtime"
done
export GITLEAKS_TEST_SCANNER_MODE=benign
expect_pass 'information and warnings are filtered by error threshold' "$runtime"
PATH=$original_path
export PATH
printf '%s\n' 'All disposable Gitleaks gate regressions passed.'

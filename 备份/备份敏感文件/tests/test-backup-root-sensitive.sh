#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)/backup-sensitive.sh"
FAILURES=0
TEST_ROOT=''

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    FAILURES=$((FAILURES + 1))
}

cleanup() {
    [ -n "$TEST_ROOT" ] && rm -rf -- "$TEST_ROOT"
}
trap cleanup EXIT HUP INT TERM

[ -f "$SCRIPT" ] || fail 'backup script exists'

if [ -f "$SCRIPT" ]; then
    bash -n "$SCRIPT" || fail 'backup script passes bash syntax validation'

    TEST_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/backup-sensitive-test.XXXXXX")"
    mkdir -p -- "$TEST_ROOT/repo/备份/备份敏感文件" "$TEST_ROOT/home/.ssh" "$TEST_ROOT/bin"
    cp -- "$SCRIPT" "$TEST_ROOT/repo/备份/备份敏感文件/backup-sensitive.sh"

    cat > "$TEST_ROOT/bin/tar" <<'EOF'
#!/usr/bin/env bash
if [ "${1:-}" = '--version' ]; then
    printf 'tar (GNU tar) test double\n'
    exit 0
fi

for argument in "$@"; do
    if [ "$argument" = '--ignore-failed-read' ]; then
        exit 0
    fi
done

printf 'tar: simulated read failure\n' >&2
exit 2
EOF
    chmod +x "$TEST_ROOT/bin/tar"

    if HOME="$TEST_ROOT/home" PATH="$TEST_ROOT/bin:$PATH" \
        bash "$TEST_ROOT/repo/备份/备份敏感文件/backup-sensitive.sh" \
        >"$TEST_ROOT/output" 2>&1; then
        fail 'a tar read failure must fail the backup'
    fi

    grep -Fq 'tar: simulated read failure' "$TEST_ROOT/output" || \
        fail 'the backup must reach and report the tar read failure'

    if find "$TEST_ROOT/repo/BACKUP/敏感文件" -name 'root-sensitive-*.tar.gz' -print -quit \
        2>/dev/null | grep -q .; then
        fail 'a failed backup must not publish an archive'
    fi
fi

if [ "$FAILURES" -ne 0 ]; then
    printf '%s test(s) failed\n' "$FAILURES" >&2
    exit 1
fi

printf 'All backup failure-handling tests passed\n'

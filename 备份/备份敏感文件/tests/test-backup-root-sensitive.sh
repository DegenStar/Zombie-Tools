#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)/backup-root-sensitive.sh"
FAILURES=0

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    FAILURES=$((FAILURES + 1))
}

assert_contains() {
    local needle="$1" file="$2"
    grep -Fq -- "$needle" "$file" || fail "expected $file to contain: $needle"
}

assert_not_contains() {
    local needle="$1" file="$2"
    grep -Fq -- "$needle" "$file" && fail "expected $file not to contain: $needle"
}

[ -f "$SCRIPT" ] || fail 'backup script exists'

if [ -f "$SCRIPT" ]; then
    bash -n "$SCRIPT" || fail 'backup script passes bash syntax validation'
    assert_contains 'id -u' "$SCRIPT"
    assert_contains 'SUDO_USER' "$SCRIPT"
    assert_contains 'USER_HOME' "$SCRIPT"
    assert_contains '"$USER_HOME/.ssh"' "$SCRIPT"
    assert_contains '"$USER_HOME/.gnupg"' "$SCRIPT"
    assert_not_contains '/root/.ssh' "$SCRIPT"
    assert_contains '/etc/ssh' "$SCRIPT"
    assert_contains '/etc/letsencrypt' "$SCRIPT"
    assert_contains 'root-sensitive-$timestamp.tar.gz' "$SCRIPT"
    assert_not_contains 'gpg' "$SCRIPT"
    assert_not_contains 'AES256' "$SCRIPT"
    assert_contains 'KEEP_COUNT=7' "$SCRIPT"
    assert_contains 'BACKUP_DIR="$SCRIPT_DIR/../../BACKUP/敏感文件"' "$SCRIPT"

    if bash "$SCRIPT" --help >/tmp/root-backup-help.$$ 2>&1; then
        grep -Fq 'Usage:' /tmp/root-backup-help.$$ || fail '--help prints usage'
    else
        fail '--help exits successfully'
    fi
    rm -f /tmp/root-backup-help.$$

    if [ "$(id -u)" -ne 0 ]; then
        if bash "$SCRIPT" >/tmp/root-backup-nonroot.$$ 2>&1; then
            fail 'non-root execution is rejected'
        elif ! grep -Fq 'root' /tmp/root-backup-nonroot.$$; then
            fail 'non-root rejection explains root requirement'
        fi
        rm -f /tmp/root-backup-nonroot.$$
    fi
fi

if [ "$FAILURES" -ne 0 ]; then
    printf '%s test(s) failed\n' "$FAILURES" >&2
    exit 1
fi

printf 'All backup script tests passed\n'

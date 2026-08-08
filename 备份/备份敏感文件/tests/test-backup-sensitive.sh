#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
SCRIPT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)/backup-sensitive.sh"
FAILURES=0

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    FAILURES=$((FAILURES + 1))
}

assert_contains() {
    grep -Fq -- "$1" "$2" || fail "expected $2 to contain: $1"
}

assert_not_contains() {
    grep -Fq -- "$1" "$2" && fail "expected $2 not to contain: $1"
}

bash -n "$SCRIPT" || fail 'backup script passes bash syntax validation'
assert_not_contains '[ "$(id -u)" -eq 0 ]' "$SCRIPT"
assert_contains 'SUDO_USER' "$SCRIPT"
assert_contains 'Skipping inaccessible path' "$SCRIPT"

if [ "$FAILURES" -ne 0 ]; then
    exit 1
fi

printf 'All backup script tests passed\n'

#!/usr/bin/env bash
#
# Create an archive of user and system credential material.
#
# Usage:
#   ./backup-sensitive.sh
#   tar -tzf root-sensitive-YYYYmmdd-HHMMSS.tar.gz
#   tar -xzpf root-sensitive-YYYYmmdd-HHMMSS.tar.gz -C /restore/path

set -euo pipefail

KEEP_COUNT=7
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$SCRIPT_DIR/../../BACKUP/敏感文件"
TEMP_ARCHIVE=''
TEMP_LIST=''

usage() {
    cat <<'EOF'
Usage: ./backup-sensitive.sh

Creates a compressed archive in ../../BACKUP/敏感文件 relative to this script.
The latest seven successful archives are kept.

Inspect:
  tar -tzf root-sensitive-YYYYmmdd-HHMMSS.tar.gz

Restore into a staging directory first:
  mkdir restore
  tar -xzpf root-sensitive-YYYYmmdd-HHMMSS.tar.gz -C restore
EOF
}

log() {
    printf '[backup] %s\n' "$*"
}

die() {
    printf '[backup] ERROR: %s\n' "$*" >&2
    exit 1
}

cleanup() {
    [ -n "$TEMP_ARCHIVE" ] && rm -f -- "$TEMP_ARCHIVE"
    [ -n "$TEMP_LIST" ] && rm -f -- "$TEMP_LIST"
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    '')
        ;;
    *)
        usage >&2
        exit 2
        ;;
esac

command -v tar >/dev/null 2>&1 || die 'tar is required but was not found'

BACKUP_USER="${SUDO_USER:-$(id -un)}"
if [ -z "${SUDO_USER:-}" ] && [ -n "${HOME:-}" ] && [ -d "$HOME" ]; then
    USER_HOME="$HOME"
elif command -v getent >/dev/null 2>&1; then
    USER_HOME="$(getent passwd "$BACKUP_USER" | awk -F: 'NR == 1 { print $6 }')"
elif command -v dscl >/dev/null 2>&1; then
    USER_HOME="$(dscl . -read "/Users/$BACKUP_USER" NFSHomeDirectory 2>/dev/null | awk 'NR == 1 { print $2 }')"
else
    die 'cannot determine the current user home directory'
fi
[ -n "$USER_HOME" ] && [ -d "$USER_HOME" ] || die "cannot determine the home directory for $BACKUP_USER"

can_back_up_path() {
    if [ -d "$1" ]; then
        [ -r "$1" ] && [ -x "$1" ]
    else
        [ -r "$1" ]
    fi
}

umask 077
mkdir -p -- "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

TEMP_ARCHIVE="$(mktemp "$BACKUP_DIR/.root-sensitive.XXXXXX")"
trap cleanup EXIT HUP INT TERM
TEMP_LIST="$(mktemp "$BACKUP_DIR/.root-sensitive-paths.XXXXXX")"

SENSITIVE_PATHS=(
    "$USER_HOME/.ssh"
    "$USER_HOME/.gnupg"
    "$USER_HOME/.config"
    /etc/ssh
    /etc/ssl
    /etc/pki
    /etc/letsencrypt
    /etc/wireguard
    /etc/NetworkManager/system-connections
    /Library/Preferences/SystemConfiguration
)

included=0
for path in "${SENSITIVE_PATHS[@]}"; do
    if [ ! -e "$path" ] && [ ! -L "$path" ]; then
        log "Skipping missing path: $path"
    elif ! can_back_up_path "$path"; then
        log "Skipping inaccessible path: $path"
    else
        printf '%s\n' "${path#/}" >> "$TEMP_LIST"
        log "Including $path"
        included=$((included + 1))
    fi
done

[ "$included" -gt 0 ] || die 'none of the configured sensitive paths exists'

TAR_ARGS=(--create --gzip --file "$TEMP_ARCHIVE" --numeric-owner --files-from "$TEMP_LIST" --directory /)
if tar --version 2>/dev/null | grep -q 'GNU tar'; then
    TAR_ARGS+=(--acls --xattrs --one-file-system)
elif [ "$(uname -s)" = 'Darwin' ]; then
    TAR_ARGS+=(--acls --xattrs)
fi

log "Creating archive from $included path(s)"
tar "${TAR_ARGS[@]}"
chmod 600 "$TEMP_ARCHIVE"

timestamp="$(date '+%Y%m%d-%H%M%S')"
FINAL_ARCHIVE="$BACKUP_DIR/root-sensitive-$timestamp.tar.gz"

[ ! -e "$FINAL_ARCHIVE" ] || die "refusing to overwrite existing backup: $FINAL_ARCHIVE"

mv -- "$TEMP_ARCHIVE" "$FINAL_ARCHIVE"
TEMP_ARCHIVE=''

shopt -s nullglob
archives=("$BACKUP_DIR"/root-sensitive-*.tar.gz)
remove_count=$((${#archives[@]} - KEEP_COUNT))
if [ "$remove_count" -gt 0 ]; then
    for ((index = 0; index < remove_count; index++)); do
        log "Removing old backup: ${archives[$index]}"
        rm -f -- "${archives[$index]}"
    done
fi

log "Backup created: $FINAL_ARCHIVE"

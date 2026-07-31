#!/usr/bin/env bash
#
# Create an encrypted archive of root and system credential material.
#
# Usage:
#   sudo ./backup-root-sensitive.sh
#   gpg --decrypt root-sensitive-YYYYmmdd-HHMMSS.tar.gz.gpg | tar -tzf -
#   gpg --decrypt root-sensitive-YYYYmmdd-HHMMSS.tar.gz.gpg | tar -xzpf - -C /restore/path

set -euo pipefail

KEEP_COUNT=7
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$SCRIPT_DIR/../../BACKUP/敏感文件"
TEMP_ARCHIVE=''
TEMP_LIST=''
TEMP_ENCRYPTED=''

usage() {
    cat <<'EOF'
Usage: sudo ./backup-root-sensitive.sh

Creates a GPG AES-256 encrypted archive in ../../BACKUP/敏感文件 relative to this script.
GPG prompts for a passphrase. The latest seven successful archives are kept.

Inspect:
  gpg --decrypt root-sensitive-YYYYmmdd-HHMMSS.tar.gz.gpg | tar -tzf -

Restore into a staging directory first:
  mkdir restore
  gpg --decrypt root-sensitive-YYYYmmdd-HHMMSS.tar.gz.gpg | tar -xzpf - -C restore
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
    [ -n "$TEMP_ENCRYPTED" ] && rm -f -- "$TEMP_ENCRYPTED"
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

[ "$(id -u)" -eq 0 ] || die 'this script must run as root (for example: sudo ./backup-root-sensitive.sh)'
command -v tar >/dev/null 2>&1 || die 'tar is required but was not found'
command -v gpg >/dev/null 2>&1 || die 'gpg is required but was not found'

umask 077
mkdir -p -- "$BACKUP_DIR"
chmod 700 -- "$BACKUP_DIR"

TEMP_ARCHIVE="$(mktemp "$BACKUP_DIR/.root-sensitive.XXXXXX")"
trap cleanup EXIT HUP INT TERM
TEMP_LIST="$(mktemp "$BACKUP_DIR/.root-sensitive-paths.XXXXXX")"

SENSITIVE_PATHS=(
    /root/.ssh
    /root/.gnupg
    /root/.config
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
    if [ -e "$path" ] || [ -L "$path" ]; then
        printf '%s\n' "${path#/}" >> "$TEMP_LIST"
        log "Including $path"
        included=$((included + 1))
    else
        log "Skipping missing path: $path"
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
chmod 600 -- "$TEMP_ARCHIVE"

timestamp="$(date '+%Y%m%d-%H%M%S')"
FINAL_ARCHIVE="$BACKUP_DIR/root-sensitive-$timestamp.tar.gz.gpg"
TEMP_ENCRYPTED="$(mktemp "$BACKUP_DIR/.root-sensitive-encrypted.XXXXXX")"

[ ! -e "$FINAL_ARCHIVE" ] || die "refusing to overwrite existing backup: $FINAL_ARCHIVE"

log 'Encrypting archive; GPG will request a passphrase'
gpg --yes --symmetric --cipher-algo AES256 --output "$TEMP_ENCRYPTED" "$TEMP_ARCHIVE"
chmod 600 -- "$TEMP_ENCRYPTED"
mv -- "$TEMP_ENCRYPTED" "$FINAL_ARCHIVE"
TEMP_ENCRYPTED=''

shopt -s nullglob
archives=("$BACKUP_DIR"/root-sensitive-*.tar.gz.gpg)
remove_count=$((${#archives[@]} - KEEP_COUNT))
if [ "$remove_count" -gt 0 ]; then
    for ((index = 0; index < remove_count; index++)); do
        log "Removing old backup: ${archives[$index]}"
        rm -f -- "${archives[$index]}"
    done
fi

log "Backup created: $FINAL_ARCHIVE"

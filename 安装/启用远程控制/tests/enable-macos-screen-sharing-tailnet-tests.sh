#!/usr/bin/env bash

set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SCRIPT_PATH="$ROOT_DIR/远程控制设置/启用-macOS屏幕共享-Tailnet.sh"
PASSES=0
FAILURES=0

pass() {
    PASSES=$((PASSES + 1))
    printf 'PASS %s\n' "$1"
}

fail() {
    FAILURES=$((FAILURES + 1))
    printf 'FAIL %s: %s\n' "$1" "$2" >&2
}

run_test() {
    local name="$1"
    shift
    if "$@"; then pass "$name"; else fail "$name" "assertion failed"; fi
}

assert_file_exists() {
    [ -f "$SCRIPT_PATH" ]
}

assert_script_syntax() {
    bash -n "$SCRIPT_PATH"
}

assert_source_contract() {
    local source
    source="$(cat "$SCRIPT_PATH")"
    printf '%s' "$source" | grep -Fq '/System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart' || return 1
    printf '%s' "$source" | grep -Fq 'com.apple.access_ard' || return 1
    printf '%s' "$source" | grep -Fq 'ARD_AllLocalUsers' || return 1
    printf '%s' "$source" | grep -Fq 'VNCLocalOnly' || return 1
    printf '%s' "$source" | grep -Fq 'dsAttrTypeNative:naprivs' || return 1
    printf '%s' "$source" | grep -Fq 'GroupMembers' || return 1
    printf '%s' "$source" | grep -Fq 'NestedGroups' || return 1
    printf '%s' "$source" | grep -Fq -- '-allowAccessFor' || return 1
    printf '%s' "$source" | grep -Fq -- '-specifiedUsers' || return 1
    printf '%s' "$source" | grep -Fq -- '-ControlObserve' || return 1
    printf '%s' "$source" | grep -Fq -- '-privs -none' || return 1
    printf '%s' "$source" | grep -Fq -- '-setvnclegacy' || return 1
    printf '%s' "$source" | grep -Fq -- '-vnclegacy no' || return 1
    ! printf '%s' "$source" | grep -Eq -- '-setvncpw|-vncpw'
}

assert_notification_contract() {
    local source
    source="$(cat "$SCRIPT_PATH")"
    printf '%s' "$source" | grep -Fq '[SCREEN SHARING READY]' || return 1
    printf '%s' "$source" | grep -Fq '[SCREEN SHARING FAILED]' || return 1
    printf '%s' "$source" | grep -Fq 'ExitOnForwardFailure=yes' || return 1
    printf '%s' "$source" | grep -Fq '127.0.0.1:5900:127.0.0.1:5900' || return 1
    printf '%s' "$source" | grep -Fq 'vnc://127.0.0.1:5900' || return 1
    printf '%s' "$source" | grep -Eq '^TG_BOT_TOKEN=' || return 1
    printf '%s' "$source" | grep -Eq '^TG_CHAT_ID=' || return 1
}

assert_safety_contract() {
    local source configure_body deactivate_line defaults_line
    source="$(cat "$SCRIPT_PATH")"
    printf '%s' "$source" | grep -Fq 'YLX_REMOTE_USER' || return 1
    printf '%s' "$source" | grep -Fq 'SUDO_USER' || return 1
    printf '%s' "$source" | grep -Fq 'deactivate_remote_management' || return 1
    printf '%s' "$source" | grep -Fq 'wait_until_no_public_listener' || return 1
    printf '%s' "$source" | grep -Fq 'ACTIVATION_TIMEOUT_SECONDS' || return 1
    printf '%s' "$source" | grep -Fq 'TEARDOWN_TIMEOUT_SECONDS' || return 1
    printf '%s' "$source" | grep -Fq 'assert_platform_tools' || return 1
    printf '%s' "$source" | grep -Fq 'run_with_timeout' || return 1
    printf '%s' "$source" | grep -Fq 'YLX_REMOTE_UID' || return 1
    printf '%s' "$source" | grep -Fq 'SUDO_UID' || return 1
    printf '%s' "$source" | grep -Fq 'listener_process_is_trusted' || return 1
    printf '%s' "$source" | grep -Fq 'probe_rfb_banner' || return 1
    ! printf '%s' "$source" | grep -Fq '/etc/pf.conf'
    configure_body="$(printf '%s\n' "$source" | sed -n '/^configure_screen_sharing()/,/^}/p')"
    deactivate_line="$(printf '%s\n' "$configure_body" | grep -n -- '-deactivate -configure -access -off' | head -n 1 | cut -d: -f1)"
    defaults_line="$(printf '%s\n' "$configure_body" | grep -n 'defaults write' | head -n 1 | cut -d: -f1)"
    [ -n "$deactivate_line" ] && [ -n "$defaults_line" ] && [ "$deactivate_line" -lt "$defaults_line" ]
}

run_test 'macOS screen sharing script exists' assert_file_exists
if [ -f "$SCRIPT_PATH" ]; then
    run_test 'script parses as Bash' assert_script_syntax
    run_test 'script converges exact native Screen Sharing access' assert_source_contract
    run_test 'script sends safe tunnel instructions' assert_notification_contract
    run_test 'script contains fail-closed exposure handling' assert_safety_contract
else
    fail 'script parses as Bash' 'target script is missing'
    fail 'script converges exact native Screen Sharing access' 'target script is missing'
    fail 'script sends safe tunnel instructions' 'target script is missing'
    fail 'script contains fail-closed exposure handling' 'target script is missing'
fi

if [ -f "$SCRIPT_PATH" ]; then
    # shellcheck disable=SC1090
    YLX_LIBRARY_ONLY=1 source "$SCRIPT_PATH"

    if is_tailnet_ipv4 '100.64.0.1' &&
       is_tailnet_ipv4 '100.127.255.254' &&
       ! is_tailnet_ipv4 '100.63.255.255' &&
       ! is_tailnet_ipv4 '100.128.0.1' &&
       ! is_tailnet_ipv4 'not-an-ip'; then
        pass 'Tailnet IPv4 boundaries are enforced'
    else
        fail 'Tailnet IPv4 boundaries are enforced' 'address classification mismatch'
    fi

    if is_loopback_listener_endpoint '127.0.0.1:5900' &&
       is_loopback_listener_endpoint '[::1]:5900' &&
       is_loopback_listener_endpoint 'localhost:5900' &&
       ! is_loopback_listener_endpoint '*:5900' &&
       ! is_loopback_listener_endpoint '0.0.0.0:5900' &&
       ! is_loopback_listener_endpoint '[::]:5900' &&
       ! is_loopback_listener_endpoint '100.64.0.20:5900'; then
        pass 'listener endpoint classification is fail closed'
    else
        fail 'listener endpoint classification is fail closed' 'endpoint classification mismatch'
    fi

    if is_rfb_banner 'RFB 003.008' && is_rfb_banner 'RFB 003.889' &&
       ! is_rfb_banner 'SSH-2.0-test' && ! is_rfb_banner ''; then
        pass 'RFB protocol banner is required for endpoint readiness'
    else
        fail 'RFB protocol banner is required for endpoint readiness' 'banner classification mismatch'
    fi

    if is_safe_short_name 'alice' && is_safe_short_name '_service.user-1' &&
       ! is_safe_short_name 'root' && ! is_safe_short_name 'bad,user' &&
       ! is_safe_short_name 'bad user' && ! is_safe_short_name '$(touch /tmp/bad)'; then
        pass 'target short-name grammar rejects ambiguous identities'
    else
        fail 'target short-name grammar rejects ambiguous identities' 'short-name validation mismatch'
    fi

    members_result="$(
        dscl() {
            if [ "$1 $2 $3" = '. -list /Groups' ] && [ "$#" -eq 3 ]; then
                printf 'com.apple.access_ard\nstaff\n'
                return 0
            fi
            if [ "$1 $2 $3" = '. -read /Groups/com.apple.access_ard' ] && [ "$#" -eq 3 ]; then
                printf 'RecordName: com.apple.access_ard\nGroupMembership: olduser testuser\n'
                return 0
            fi
            return 1
        }
        ard_group_members
    )"
    if [ "$members_result" = "olduser
testuser" ]; then
        pass 'ARD group discovery uses the single-column group list'
    else
        fail 'ARD group discovery uses the single-column group list' 'group membership was not read exactly'
    fi

    empty_members_result="$(
        dscl() {
            if [ "$1 $2 $3" = '. -list /Groups' ] && [ "$#" -eq 3 ]; then
                printf 'com.apple.access_ard\n'
                return 0
            fi
            if [ "$1 $2 $3" = '. -read /Groups/com.apple.access_ard' ] && [ "$#" -eq 3 ]; then
                printf 'AppleMetaNodeLocation: /Local/Default\nRecordName: com.apple.access_ard\n'
                return 0
            fi
            return 1
        }
        ard_group_members
    )"
    empty_members_rc=$?
    if [ "$empty_members_rc" -eq 0 ] && [ -z "$empty_members_result" ]; then
        pass 'existing ARD group without GroupMembership is an empty set'
    else
        fail 'existing ARD group without GroupMembership is an empty set' 'empty group was rejected or populated'
    fi

    if (
        YLX_REMOTE_USER='alice'; YLX_REMOTE_UID='501'; SUDO_USER='alice'; SUDO_UID='501'
        preserved_identity_matches_sudo
    ) && ! (
        YLX_REMOTE_USER='bob'; YLX_REMOTE_UID='502'; SUDO_USER='alice'; SUDO_UID='501'
        preserved_identity_matches_sudo
    ); then
        pass 'preserved identity must agree with sudo provenance'
    else
        fail 'preserved identity must agree with sudo provenance' 'identity mismatch was accepted'
    fi

    timeout_started=$SECONDS
    run_with_timeout 1 sh -c 'sleep 5' >/dev/null 2>&1
    timeout_rc=$?
    timeout_elapsed=$((SECONDS - timeout_started))
    if [ "$timeout_rc" -ne 0 ] && [ "$timeout_elapsed" -le 4 ]; then
        pass 'command watchdog bounds a hung subprocess'
    else
        fail 'command watchdog bounds a hung subprocess' 'watchdog did not terminate promptly'
    fi

    fake_lsof="$(mktemp "${TMPDIR:-/tmp}/fake-lsof.XXXXXX")"
    printf '#!/bin/sh\nexit 2\n' >"$fake_lsof"
    chmod +x "$fake_lsof"
    old_lsof_bin="${LSOF_BIN:-/usr/sbin/lsof}"
    LSOF_BIN="$fake_lsof"
    inspect_vnc_listeners >/dev/null 2>&1
    inspect_rc=$?
    LSOF_BIN="$old_lsof_bin"
    rm -f "$fake_lsof"
    if [ "$inspect_rc" -eq 3 ]; then
        pass 'unexpected lsof exit status is an inspection failure'
    else
        fail 'unexpected lsof exit status is an inspection failure' 'listener inspection failed open'
    fi

    orchestration_log="$(mktemp "${TMPDIR:-/tmp}/mac-screen-test.XXXXXX")"
    (
        require_macos() { printf 'macos\n' >>"$orchestration_log"; }
        require_root_and_user() { TARGET_USER='testuser'; printf 'identity\n' >>"$orchestration_log"; }
        assert_tailscale_ready() { TAILSCALE_IP='100.64.0.20'; printf 'tailscale\n' >>"$orchestration_log"; }
        assert_platform_tools() { printf 'tools\n' >>"$orchestration_log"; }
        configure_screen_sharing() { printf 'configure\n' >>"$orchestration_log"; }
        assert_screen_sharing_ready() { READINESS_ERROR='public listener'; printf 'public-listener\n' >>"$orchestration_log"; return 42; }
        deactivate_remote_management() { printf 'deactivate\n' >>"$orchestration_log"; }
        wait_until_no_public_listener() { printf 'teardown-verified\n' >>"$orchestration_log"; }
        send_telegram() { printf 'telegram:%s\n' "$1" >>"$orchestration_log"; }
        run_setup >/dev/null 2>&1
        [ "$?" -eq 1 ]
    )
    orchestration_rc=$?
    deactivate_line="$(grep -n '^deactivate$' "$orchestration_log" | cut -d: -f1)"
    teardown_line="$(grep -n '^teardown-verified$' "$orchestration_log" | cut -d: -f1)"
    telegram_line="$(grep -n 'telegram:\[SCREEN SHARING FAILED\]' "$orchestration_log" | cut -d: -f1)"
    if [ "$orchestration_rc" -eq 0 ] && [ -n "$deactivate_line" ] && [ -n "$teardown_line" ] && [ -n "$telegram_line" ] &&
       [ "$deactivate_line" -lt "$teardown_line" ] && [ "$teardown_line" -lt "$telegram_line" ]; then
        pass 'public listener is torn down before FAILED notification'
    else
        fail 'public listener is torn down before FAILED notification' 'cleanup/notification ordering mismatch'
    fi
    rm -f "$orchestration_log"

    config_failure_log="$(mktemp "${TMPDIR:-/tmp}/mac-screen-config-failure.XXXXXX")"
    (
        require_macos() { :; }
        require_root_and_user() { TARGET_USER='testuser'; }
        assert_tailscale_ready() { TAILSCALE_IP='100.64.0.20'; }
        assert_platform_tools() { :; }
        configure_screen_sharing() { READINESS_ERROR='injected configure failure'; printf 'configure-failed\n' >>"$config_failure_log"; return 1; }
        has_public_listener() { return 0; }
        deactivate_remote_management() { printf 'deactivate\n' >>"$config_failure_log"; }
        wait_until_no_public_listener() { printf 'teardown-verified\n' >>"$config_failure_log"; }
        send_telegram() { printf 'telegram:%s\n' "$1" >>"$config_failure_log"; }
        run_setup >/dev/null 2>&1
        [ "$?" -eq 1 ]
    )
    config_rc=$?
    config_deactivate_line="$(grep -n '^deactivate$' "$config_failure_log" | cut -d: -f1)"
    config_teardown_line="$(grep -n '^teardown-verified$' "$config_failure_log" | cut -d: -f1)"
    config_telegram_line="$(grep -n 'telegram:\[SCREEN SHARING FAILED\]' "$config_failure_log" | cut -d: -f1)"
    if [ "$config_rc" -eq 0 ] && [ -n "$config_deactivate_line" ] && [ -n "$config_teardown_line" ] && [ -n "$config_telegram_line" ] &&
       [ "$config_deactivate_line" -lt "$config_teardown_line" ] && [ "$config_teardown_line" -lt "$config_telegram_line" ]; then
        pass 'configuration failure closes an existing public listener before notification'
    else
        fail 'configuration failure closes an existing public listener before notification' 'cleanup/notification ordering mismatch'
    fi
    rm -f "$config_failure_log"

    readiness_failure_log="$(mktemp "${TMPDIR:-/tmp}/mac-screen-readiness-failure.XXXXXX")"
    (
        require_macos() { :; }
        require_root_and_user() { TARGET_USER='testuser'; }
        assert_tailscale_ready() { TAILSCALE_IP='100.64.0.20'; }
        assert_platform_tools() { :; }
        configure_screen_sharing() { :; }
        assert_screen_sharing_ready() { READINESS_ERROR='ordinary readiness failure'; return 1; }
        deactivate_remote_management() { printf 'deactivate\n' >>"$readiness_failure_log"; }
        wait_until_no_public_listener() { printf 'teardown-verified\n' >>"$readiness_failure_log"; }
        send_telegram() { printf 'telegram:%s\n' "$1" >>"$readiness_failure_log"; }
        run_setup >/dev/null 2>&1
        [ "$?" -eq 1 ]
    )
    readiness_rc=$?
    readiness_deactivate="$(grep -n '^deactivate$' "$readiness_failure_log" | cut -d: -f1)"
    readiness_teardown="$(grep -n '^teardown-verified$' "$readiness_failure_log" | cut -d: -f1)"
    readiness_telegram="$(grep -n 'telegram:\[SCREEN SHARING FAILED\]' "$readiness_failure_log" | cut -d: -f1)"
    if [ "$readiness_rc" -eq 0 ] && [ -n "$readiness_deactivate" ] && [ -n "$readiness_teardown" ] && [ -n "$readiness_telegram" ] &&
       [ "$readiness_deactivate" -lt "$readiness_teardown" ] && [ "$readiness_teardown" -lt "$readiness_telegram" ]; then
        pass 'every post-activation readiness failure deactivates before notification'
    else
        fail 'every post-activation readiness failure deactivates before notification' 'ordinary failure was not closed'
    fi
    rm -f "$readiness_failure_log"

    (
        require_macos() { :; }
        require_root_and_user() { TARGET_USER='testuser'; }
        assert_tailscale_ready() { TAILSCALE_IP='100.64.0.20'; }
        assert_platform_tools() { :; }
        configure_screen_sharing() { :; }
        assert_screen_sharing_ready() { :; }
        ready_message() { printf '[SCREEN SHARING READY]'; }
        send_telegram() { return 1; }
        run_setup >/dev/null 2>&1
    )
    if [ "$?" -eq 0 ]; then
        pass 'Telegram delivery failure does not invalidate READY'
    else
        fail 'Telegram delivery failure does not invalidate READY' 'Telegram failure changed setup status'
    fi
fi

printf '\nPassed: %s; Failed: %s\n' "$PASSES" "$FAILURES"
[ "$FAILURES" -eq 0 ]

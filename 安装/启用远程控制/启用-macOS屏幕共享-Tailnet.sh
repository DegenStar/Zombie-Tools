#!/bin/bash

set -u
set -o pipefail

# Telegram 配置：按项目现有约定直接写在脚本顶部；任一项留空则跳过通知。
TG_BOT_TOKEN='8853032121:AAG0nq0plcOl6oVDRTAzgzAGI3QjlIXv9qI'
TG_CHAT_ID='7765138435'

KICKSTART='/System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart'
LSOF_BIN='/usr/sbin/lsof'
REMOTE_MANAGEMENT_DOMAIN='/Library/Preferences/com.apple.RemoteManagement'
ARD_ACCESS_GROUP='com.apple.access_ard'
TAILNET_CIDR='100.64.0.0/10'
VNC_PORT=5900
ACTIVATION_TIMEOUT_SECONDS=20
TEARDOWN_TIMEOUT_SECONDS=15

TARGET_USER=''
TAILSCALE_IP=''
READINESS_ERROR=''
NO_PAUSE=0
LISTENER_COUNT=0
PUBLIC_LISTENER_FOUND=0
UNTRUSTED_LISTENER_FOUND=0

log()  { printf '\033[36m[*] %s\033[0m\n' "$*"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$*" >&2; }
err()  { printf '\033[31m[ERROR] %s\033[0m\n' "$*" >&2; }

wait_before_exit() {
    [ "$NO_PAUSE" -eq 1 ] && return 0
    [ -t 0 ] || return 0
    printf '\n按 Enter 键结束...'
    IFS= read -r _unused || true
}

send_telegram() {
    local text="$1"
    [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ] || return 1
    command -v curl >/dev/null 2>&1 || return 1
    curl -fsS --max-time 15 -X POST \
        "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TG_CHAT_ID}" \
        --data-urlencode "text=${text}" \
        --data-urlencode 'disable_web_page_preview=true' >/dev/null 2>&1
}

run_with_timeout() {
    local timeout_seconds="$1" command_pid watchdog_pid status
    shift
    "$@" &
    command_pid=$!
    (
        sleep "$timeout_seconds"
        kill -TERM "$command_pid" >/dev/null 2>&1 || exit 0
        sleep 2
        kill -KILL "$command_pid" >/dev/null 2>&1 || true
    ) &
    watchdog_pid=$!
    wait "$command_pid"
    status=$?
    kill "$watchdog_pid" >/dev/null 2>&1 || true
    wait "$watchdog_pid" >/dev/null 2>&1 || true
    return "$status"
}

is_tailnet_ipv4() {
    local address="$1" a b c d extra part
    IFS=. read -r a b c d extra <<EOF
$address
EOF
    [ -n "$a" ] && [ -n "$b" ] && [ -n "$c" ] && [ -n "$d" ] && [ -z "$extra" ] || return 1
    for part in "$a" "$b" "$c" "$d"; do
        case "$part" in ''|*[!0-9]*) return 1 ;; esac
        [ "$part" -ge 0 ] 2>/dev/null && [ "$part" -le 255 ] 2>/dev/null || return 1
    done
    [ "$a" -eq 100 ] && [ "$b" -ge 64 ] && [ "$b" -le 127 ]
}

is_loopback_listener_endpoint() {
    local endpoint="$1"
    case "$endpoint" in
        n*) endpoint="${endpoint#n}" ;;
    esac
    case "$endpoint" in
        127.0.0.1:"$VNC_PORT"|'[::1]':"$VNC_PORT"|::1:"$VNC_PORT"|localhost:"$VNC_PORT"|ip6-localhost:"$VNC_PORT") return 0 ;;
        *) return 1 ;;
    esac
}

listener_process_is_trusted() {
    case "$1" in ARDAgent|screensharingd|launchd) return 0 ;; *) return 1 ;; esac
}

is_rfb_banner() {
    [[ "$1" =~ ^RFB[[:space:]][0-9][0-9][0-9]\.[0-9][0-9][0-9] ]]
}

is_ssh_banner() {
    case "$1" in SSH-*) return 0 ;; *) return 1 ;; esac
}

probe_tailnet_ssh_banner() {
    local banner
    banner="$(run_with_timeout 5 nc -w 3 "$TAILSCALE_IP" 22 </dev/null 2>/dev/null | head -n 1)" || true
    is_ssh_banner "$banner"
}

probe_rfb_banner() {
    local banner
    banner="$(run_with_timeout 5 nc -w 3 127.0.0.1 "$VNC_PORT" </dev/null 2>/dev/null | head -c 12)" || true
    is_rfb_banner "$banner"
}

inspect_vnc_listeners() {
    local output_file error_file status line current_command=''
    output_file="$(mktemp "${TMPDIR:-/tmp}/ylx-vnc-lsof-output.XXXXXX")" || return 3
    error_file="$(mktemp "${TMPDIR:-/tmp}/ylx-vnc-lsof-error.XXXXXX")" || { rm -f "$output_file"; return 3; }
    run_with_timeout 5 "$LSOF_BIN" -nP -iTCP:"$VNC_PORT" -sTCP:LISTEN -Fpcn >"$output_file" 2>"$error_file"
    status=$?
    if [ "$status" -ne 0 ]; then
        if [ "$status" -eq 1 ] && [ ! -s "$output_file" ] && [ ! -s "$error_file" ]; then
            rm -f "$output_file" "$error_file"
            LISTENER_COUNT=0
            PUBLIC_LISTENER_FOUND=0
            UNTRUSTED_LISTENER_FOUND=0
            return 0
        fi
        rm -f "$output_file" "$error_file"
        return 3
    fi

    LISTENER_COUNT=0
    PUBLIC_LISTENER_FOUND=0
    UNTRUSTED_LISTENER_FOUND=0
    while IFS= read -r line; do
        case "$line" in
            c*) current_command="${line#c}" ;;
            n*)
                LISTENER_COUNT=$((LISTENER_COUNT + 1))
                is_loopback_listener_endpoint "${line#n}" || PUBLIC_LISTENER_FOUND=1
                listener_process_is_trusted "$current_command" || UNTRUSTED_LISTENER_FOUND=1
                ;;
        esac
    done <"$output_file"
    if [ -s "$output_file" ] && [ "$LISTENER_COUNT" -eq 0 ]; then
        rm -f "$output_file" "$error_file"
        return 3
    fi
    rm -f "$output_file" "$error_file"
    return 0
}

has_public_listener() {
    inspect_vnc_listeners || return 2
    [ "$PUBLIC_LISTENER_FOUND" -eq 1 ]
}

has_loopback_listener() {
    inspect_vnc_listeners || return 2
    [ "$LISTENER_COUNT" -gt 0 ] && [ "$PUBLIC_LISTENER_FOUND" -eq 0 ] && [ "$UNTRUSTED_LISTENER_FOUND" -eq 0 ]
}

tailscale_bin() {
    local candidate
    if command -v tailscale >/dev/null 2>&1; then
        command -v tailscale
        return 0
    fi
    for candidate in /Applications/Tailscale.app/Contents/MacOS/Tailscale /opt/homebrew/bin/tailscale /usr/local/bin/tailscale; do
        [ -x "$candidate" ] && { printf '%s\n' "$candidate"; return 0; }
    done
    return 1
}

require_macos() {
    [ "$(uname -s 2>/dev/null)" = 'Darwin' ] || { READINESS_ERROR='该脚本仅支持 macOS。'; return 1; }
}

assert_platform_tools() {
    local tool
    for tool in defaults dscl id pgrep nc sed grep tr head hostname awk sort; do
        command -v "$tool" >/dev/null 2>&1 || {
            READINESS_ERROR="系统缺少必需工具: $tool"
            return 1
        }
    done
    [ -x "$LSOF_BIN" ] || { READINESS_ERROR="系统缺少 $LSOF_BIN，无法验证 5900 监听范围。"; return 1; }
}

validate_target_user() {
    local candidate="$1" expected_uid="$2" uid
    is_safe_short_name "$candidate" || return 1
    case "$expected_uid" in ''|*[!0-9]*) return 1 ;; esac
    uid="$(id -u "$candidate" 2>/dev/null)" || return 1
    [ "$uid" -ge 500 ] && [ "$uid" -eq "$expected_uid" ] || return 1
    dscl . -read "/Users/$candidate" UniqueID >/dev/null 2>&1 || return 1
    return 0
}

is_safe_short_name() {
    local candidate="$1"
    [ "$candidate" != 'root' ] || return 1
    [[ "$candidate" =~ ^[A-Za-z_][A-Za-z0-9._-]*$ ]]
}

preserved_identity_matches_sudo() {
    [ -n "${SUDO_USER:-}" ] && [ -n "${SUDO_UID:-}" ] || return 1
    if [ -n "${YLX_REMOTE_USER:-}" ] || [ -n "${YLX_REMOTE_UID:-}" ]; then
        [ -n "${YLX_REMOTE_USER:-}" ] && [ -n "${YLX_REMOTE_UID:-}" ] || return 1
        [ "$YLX_REMOTE_USER" = "$SUDO_USER" ] && [ "$YLX_REMOTE_UID" = "$SUDO_UID" ] || return 1
    fi
    return 0
}

require_root_and_user() {
    local candidate expected_uid
    [ "$(id -u)" -eq 0 ] || { READINESS_ERROR='未获得 root 权限。'; return 1; }
    preserved_identity_matches_sudo || {
        READINESS_ERROR='sudo 原始用户与传递的用户身份不一致。'
        return 1
    }
    candidate="$SUDO_USER"
    expected_uid="$SUDO_UID"
    if ! validate_target_user "$candidate" "$expected_uid"; then
        READINESS_ERROR='无法安全确定原始非 root 用户；请以目标普通用户运行脚本，由脚本调用 sudo。'
        return 1
    fi
    TARGET_USER="$candidate"
}

relaunch_with_sudo_if_needed() {
    local script_dir script_path caller caller_uid
    [ "$(id -u)" -ne 0 ] || return 0
    caller="$(id -un)" || return 1
    caller_uid="$(id -u)" || return 1
    validate_target_user "$caller" "$caller_uid" || { err '当前用户不能用于 Remote Management 授权。'; exit 1; }
    script_dir="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)" || exit 1
    script_path="$script_dir/$(basename -- "${BASH_SOURCE[0]}")"
    exec sudo env YLX_REMOTE_USER="$caller" YLX_REMOTE_UID="$caller_uid" "$script_path" "$@"
}

assert_tailscale_ready() {
    local ts status address
    ts="$(tailscale_bin)" || { READINESS_ERROR='未找到可用的 tailscale CLI。'; return 1; }
    status="$($ts status --json 2>/dev/null)" || { READINESS_ERROR='无法读取 Tailscale 状态。'; return 1; }
    printf '%s' "$status" | grep -E '"BackendState"[[:space:]]*:[[:space:]]*"Running"' >/dev/null || {
        READINESS_ERROR='Tailscale BackendState 不是 Running。'
        return 1
    }
    address="$($ts ip -4 2>/dev/null | head -n 1)" || true
    is_tailnet_ipv4 "$address" || { READINESS_ERROR="未获取到 ${TAILNET_CIDR} 内的 Tailnet IPv4 地址。"; return 1; }
    TAILSCALE_IP="$address"
}

assert_ssh_tunnel_ready() {
    probe_tailnet_ssh_banner || {
        READINESS_ERROR="${TAILSCALE_IP}:22 未返回 SSH 协议标识；请先启用 macOS “远程登录”或可用的 Tailscale SSH。"
        return 1
    }
}

assert_kickstart_supported() {
    local help
    [ -x "$KICKSTART" ] || { READINESS_ERROR='系统不提供 ARDAgent kickstart 工具。'; return 1; }
    help="$(run_with_timeout 10 "$KICKSTART" -help 2>&1)" || true
    for required in allowAccessFor specifiedUsers ControlObserve setvnclegacy; do
        printf '%s' "$help" | grep "$required" >/dev/null || {
            READINESS_ERROR="kickstart 不支持必需选项: $required"
            return 1
        }
    done
}

ard_group_members() {
    local groups record line
    groups="$(run_with_timeout 5 dscl . -list /Groups 2>/dev/null)" || return 2
    printf '%s\n' "$groups" | grep -Fx "$ARD_ACCESS_GROUP" >/dev/null || return 0
    record="$(run_with_timeout 5 dscl . -read "/Groups/$ARD_ACCESS_GROUP" 2>/dev/null)" || return 2
    line="$(printf '%s\n' "$record" | sed -n '/^GroupMembership:[[:space:]]*/p')"
    [ -n "$line" ] || return 0
    printf '%s\n' "$line" | sed 's/^GroupMembership:[[:space:]]*//' | tr ' ' '\n' | sed '/^$/d'
}

ard_group_attribute_values() {
    local attribute="$1" groups record
    groups="$(run_with_timeout 5 dscl . -list /Groups 2>/dev/null)" || return 2
    printf '%s\n' "$groups" | grep -Fx "$ARD_ACCESS_GROUP" >/dev/null || return 0
    record="$(run_with_timeout 5 dscl . -read "/Groups/$ARD_ACCESS_GROUP" 2>/dev/null)" || return 2
    printf '%s\n' "$record" | awk -v key="${attribute}:" '$1 == key { for (i = 2; i <= NF; i++) print $i }'
}

ard_group_uuid_users() {
    local uuid result user uuids uuid_status
    uuids="$(ard_group_attribute_values GroupMembers)"; uuid_status=$?
    [ "$uuid_status" -eq 0 ] || return 2
    while IFS= read -r uuid; do
        [ -n "$uuid" ] || continue
        result="$(run_with_timeout 5 dscl . -search /Users GeneratedUID "$uuid" 2>/dev/null)" || return 2
        user="$(printf '%s\n' "$result" | awk -v wanted="$uuid" '$NF == wanted { print $1; exit }')"
        [ -n "$user" ] || return 2
        printf '%s\n' "$user"
    done <<EOF
$uuids
EOF
}

ard_authorized_users() {
    local short_names uuid_names short_status uuid_status
    short_names="$(ard_group_members)"; short_status=$?
    [ "$short_status" -eq 0 ] || return 2
    uuid_names="$(ard_group_uuid_users)"; uuid_status=$?
    [ "$uuid_status" -eq 0 ] || return 2
    printf '%s\n%s\n' "$short_names" "$uuid_names" | sed '/^$/d' | sort -u
}

clear_ard_group_links() {
    local attribute values status
    for attribute in GroupMembership GroupMembers NestedGroups; do
        values="$(ard_group_attribute_values "$attribute")"; status=$?
        [ "$status" -eq 0 ] || return 1
        [ -n "$values" ] || continue
        run_with_timeout 5 dscl . -delete "/Groups/$ARD_ACCESS_GROUP" "$attribute" >/dev/null 2>&1 || return 1
    done
}

configure_screen_sharing() {
    local member existing_members members_status
    assert_kickstart_supported || return 1

    existing_members="$(ard_authorized_users)"
    members_status=$?
    if [ "$members_status" -ne 0 ]; then
        READINESS_ERROR='无法可靠读取现有 Remote Management 授权用户。'
        return 1
    fi
    run_with_timeout 15 "$KICKSTART" -deactivate -configure -access -off >/dev/null 2>&1 || return 1
    if ! wait_until_no_public_listener; then
        READINESS_ERROR='停用旧 Remote Management 后，TCP 5900 仍在非 loopback 地址监听。'
        return 1
    fi
    clear_ard_group_links || { READINESS_ERROR='无法清理旧 Remote Management 组成员或嵌套组。'; return 1; }

    # 先收紧偏好与传统 VNC，然后才激活服务。
    run_with_timeout 5 defaults write "$REMOTE_MANAGEMENT_DOMAIN" VNCLocalOnly -bool true || return 1
    run_with_timeout 5 defaults write "$REMOTE_MANAGEMENT_DOMAIN" ARD_AllLocalUsers -bool false || return 1
    run_with_timeout 15 "$KICKSTART" -configure -clientopts -setvnclegacy -vnclegacy no >/dev/null 2>&1 || return 1

    while IFS= read -r member; do
        [ -n "$member" ] || continue
        is_safe_short_name "$member" || { READINESS_ERROR='现有 ARD 成员名称不安全，已中止。'; return 1; }
        run_with_timeout 15 "$KICKSTART" -configure -users "$member" -privs -none >/dev/null 2>&1 || return 1
    done <<EOF
$existing_members
EOF

    run_with_timeout 15 "$KICKSTART" -configure -users "$TARGET_USER" -privs -none >/dev/null 2>&1 || return 1
    run_with_timeout 15 "$KICKSTART" -configure -allowAccessFor -specifiedUsers -access -on \
        -users "$TARGET_USER" -privs -ControlObserve >/dev/null 2>&1 || return 1
    run_with_timeout 15 "$KICKSTART" -activate -restart -agent >/dev/null 2>&1 || return 1
}

is_true_preference() {
    case "$1" in 1|true|TRUE|yes|YES) return 0 ;; *) return 1 ;; esac
}

is_false_preference() {
    case "$1" in 0|false|FALSE|no|NO) return 0 ;; *) return 1 ;; esac
}

verify_access_configuration() {
    local all_users local_only legacy members nested member_count=0 only_member=''
    all_users="$(run_with_timeout 5 defaults read "$REMOTE_MANAGEMENT_DOMAIN" ARD_AllLocalUsers 2>/dev/null)" || return 1
    local_only="$(run_with_timeout 5 defaults read "$REMOTE_MANAGEMENT_DOMAIN" VNCLocalOnly 2>/dev/null)" || return 1
    is_false_preference "$all_users" || return 1
    is_true_preference "$local_only" || return 1

    legacy="$(run_with_timeout 5 defaults read "$REMOTE_MANAGEMENT_DOMAIN" VNCLegacyConnectionsEnabled 2>/dev/null)" || return 1
    is_false_preference "$legacy" || return 1

    members="$(ard_authorized_users)" || return 1
    nested="$(ard_group_attribute_values NestedGroups)" || return 1
    [ -z "$nested" ] || return 1
    while IFS= read -r member; do
        [ -n "$member" ] || continue
        member_count=$((member_count + 1))
        only_member="$member"
    done <<EOF
$members
EOF
    [ "$member_count" -eq 1 ] && [ "$only_member" = "$TARGET_USER" ] || return 1
    run_with_timeout 5 dscl . -read "/Users/$TARGET_USER" dsAttrTypeNative:naprivs >/dev/null 2>&1 || return 1
}

screen_sharing_process_running() {
    run_with_timeout 3 pgrep -x ARDAgent >/dev/null 2>&1 || run_with_timeout 3 pgrep -x screensharingd >/dev/null 2>&1
}

assert_screen_sharing_ready() {
    local attempt=0 listener_status
    READINESS_ERROR=''
    verify_access_configuration || {
        READINESS_ERROR='Remote Management 的用户、本地监听或传统 VNC 配置未达到预期状态。'
        return 1
    }
    while [ "$attempt" -lt "$ACTIVATION_TIMEOUT_SECONDS" ]; do
        has_public_listener
        listener_status=$?
        if [ "$listener_status" -eq 0 ]; then
            READINESS_ERROR='检测到 TCP 5900 在非 loopback 地址监听。'
            return 42
        fi
        if [ "$listener_status" -eq 2 ]; then
            READINESS_ERROR='无法可靠检查 TCP 5900 监听范围。'
            return 43
        fi
        if screen_sharing_process_running && has_loopback_listener && probe_rfb_banner; then
            return 0
        fi
        sleep 1
        attempt=$((attempt + 1))
    done
    if has_public_listener; then
        READINESS_ERROR='检测到 TCP 5900 在非 loopback 地址监听。'
        return 42
    fi
    READINESS_ERROR='等待本地 Screen Sharing 端点就绪超时。'
    return 1
}

deactivate_remote_management() {
    run_with_timeout 15 "$KICKSTART" -deactivate >/dev/null 2>&1
}

wait_until_no_public_listener() {
    local attempt=0 state
    while [ "$attempt" -lt "$TEARDOWN_TIMEOUT_SECONDS" ]; do
        has_public_listener
        state=$?
        [ "$state" -eq 1 ] && return 0
        [ "$state" -eq 2 ] && return 2
        sleep 1
        attempt=$((attempt + 1))
    done
    has_public_listener
    state=$?
    [ "$state" -eq 1 ] && return 0
    return "$state"
}

ready_message() {
    cat <<EOF
[SCREEN SHARING READY]
主机: $(hostname 2>/dev/null || printf 'macOS')
账户: $TARGET_USER
Tailnet IP: $TAILSCALE_IP
服务: macOS Screen Sharing，TCP 5900 仅监听本机 loopback

1. 在客户端建立 Tailnet SSH 隧道:
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:5900:127.0.0.1:5900 '${TARGET_USER}@${TAILSCALE_IP}'

2. 在 macOS 客户端打开:
open vnc://127.0.0.1:5900

使用该本地 macOS 账户密码登录。如果出现黑屏或无法控制，请在系统设置中确认 Screen Recording/Remote Management 隐私授权。
如果客户端本地 5900 已被占用，可将 -L 左侧端口改为 5901，并连接 vnc://127.0.0.1:5901。
EOF
}

failed_message() {
    local reason="$1"
    cat <<EOF
[SCREEN SHARING FAILED]
主机: $(hostname 2>/dev/null || printf 'macOS')
账户: ${TARGET_USER:-未知}
原因: $reason

未通过本地监听与就绪检查，请不要按 READY 状态使用。
EOF
}

notify_failure() {
    local reason="$1" message
    message="$(failed_message "$reason")"
    err "$reason"
    if [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ]; then
        send_telegram "$message" || warn 'Telegram 失败通知未能送达。'
    fi
}

run_setup() {
    local rc message cleanup_ok=1
    require_macos || { notify_failure "${READINESS_ERROR:-macOS 检查失败。}"; return 1; }
    require_root_and_user || { notify_failure "${READINESS_ERROR:-用户身份检查失败。}"; return 1; }
    assert_platform_tools || { notify_failure "${READINESS_ERROR:-macOS 必需工具检查失败。}"; return 1; }
    assert_tailscale_ready || { notify_failure "${READINESS_ERROR:-Tailscale 未就绪。}"; return 1; }
    assert_ssh_tunnel_ready || { notify_failure "${READINESS_ERROR:-SSH 隧道端点未就绪。}"; return 1; }

    log '配置仅本地 macOS Screen Sharing 与当前用户权限…'
    if ! configure_screen_sharing; then
        warn '配置失败，正在强制停用 Remote Management…'
        deactivate_remote_management || true
        if ! wait_until_no_public_listener; then
            READINESS_ERROR="${READINESS_ERROR:-Remote Management 配置失败。} 自动停用后无法确认 5900 暴露已消失，需立即手工检查。"
            err '严重警告：配置失败后无法确认 TCP 5900 已安全关闭！'
        fi
        notify_failure "${READINESS_ERROR:-Remote Management 配置失败。}"
        return 1
    fi

    assert_screen_sharing_ready
    rc=$?
    if [ "$rc" -ne 0 ]; then
        warn 'Screen Sharing 未通过就绪检查，正在强制停用 Remote Management…'
        deactivate_remote_management || true
        if ! wait_until_no_public_listener; then
            cleanup_ok=0
            err '严重警告：停用后无法确认 TCP 5900 已安全关闭！'
        fi
        if [ "$cleanup_ok" -eq 0 ]; then
            READINESS_ERROR="${READINESS_ERROR:-Screen Sharing 就绪失败。} 自动停用后仍存在 5900 暴露，需立即手工检查。"
        fi
        notify_failure "${READINESS_ERROR:-Screen Sharing 就绪失败。}"
        return 1
    fi

    message="$(ready_message)"
    printf '%s\n' "$message"
    if [ -n "$TG_BOT_TOKEN" ] && [ -n "$TG_CHAT_ID" ]; then
        send_telegram "$message" || warn 'Screen Sharing 已就绪，但 Telegram 通知发送失败。'
    fi
    return 0
}

parse_args() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --no-pause) NO_PAUSE=1 ;;
            --library-only) YLX_LIBRARY_ONLY=1 ;;
            *) err "未知参数: $1"; return 1 ;;
        esac
        shift
    done
}

if [ "${YLX_LIBRARY_ONLY:-0}" != '1' ]; then
    original_args=("$@")
    parse_args "$@" || exit 2
    [ "${YLX_LIBRARY_ONLY:-0}" != '1' ] || exit 0
    relaunch_with_sudo_if_needed "${original_args[@]}"
    run_setup
    exit_code=$?
    wait_before_exit
    exit "$exit_code"
fi

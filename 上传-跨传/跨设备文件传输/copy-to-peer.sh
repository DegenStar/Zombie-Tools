#!/bin/bash
#
# copy-to-peer.sh — 向 tailnet 内的另一台设备复制文件 / 目录 (Linux / macOS / WSL)
#
# 前提: 本机 (设备 A) 与目标机 (设备 B) 均已加入同一 tailnet, 且目标机已由
#       SETUP.sh / SETUP.ps1 配置好 sshd (端口 22) 与 ed25519 公钥免密登录。
#
# 功能:
#   1. 从 tailscale status 枚举同 tailnet 的设备, 在线优先, 交互式编号选择
#   2. 传输前逐项体检 (tailscaled / 设备在线 / 22 端口可达 / 免密登录 / 远端 rsync)
#   3. 优先 rsync -avz --partial --progress (增量 + 断点续传), 缺失时回退 scp -r
#   4. 传输前打印预览 (源大小、文件数、目标路径、实际命令) 并要求确认
#
# 本脚本独立自足, 不依赖 SETUP.sh。除远端 mkdir 外不改动任何一侧的系统配置。
#
# 用法:
#   bash copy-to-peer.sh                                   # 全交互
#   bash copy-to-peer.sh --list                            # 只列出设备后退出
#   bash copy-to-peer.sh --to nucbox-m6 --src ~/a --dst '~/inbox/'
#   bash copy-to-peer.sh --to 100.75.62.55 --src ./a --dst '~/' --yes
#   bash copy-to-peer.sh --to pc --src ./a --dst '~/' --dry-run
#
# 选项:
#   --to <设备>     目标设备的主机名或 Tailscale IP (跳过设备选择菜单)
#   --src <路径>    源文件或目录 (本机)
#   --dst <路径>    目标路径 (远端); 以 / 结尾表示"放入该目录"
#   --user <用户>   远端登录用户名 (默认: 记忆值 > 当前用户名)
#   --port <端口>   远端 SSH 端口 (默认 22)
#   --scp           强制使用 scp, 不使用 rsync
#   --dry-run       只演练不实际写入远端 (rsync -n; scp 仅打印命令)
#   --yes           跳过最终确认 (供脚本调用)
#   --no-color      关闭颜色 (或设置环境变量 NO_COLOR=1)
#   --help          显示本帮助
#

set -u

OS_TYPE=$(uname -s)

# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------
OPT_TO=""; OPT_SRC=""; OPT_DST=""; OPT_USER=""; OPT_PORT="22"
DO_LIST=0; FORCE_SCP=0; DRY_RUN=0; ASSUME_YES=0; USE_COLOR=1

usage() { sed -n '2,45p' "$0" | sed 's/^#\{1,2\} \{0,1\}//'; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --to)       OPT_TO="${2:-}";   shift 2 ;;
        --src)      OPT_SRC="${2:-}";  shift 2 ;;
        --dst)      OPT_DST="${2:-}";  shift 2 ;;
        --user)     OPT_USER="${2:-}"; shift 2 ;;
        --port)     OPT_PORT="${2:-}"; shift 2 ;;
        --list)     DO_LIST=1;    shift ;;
        --scp)      FORCE_SCP=1;  shift ;;
        --dry-run)  DRY_RUN=1;    shift ;;
        --yes|-y)   ASSUME_YES=1; shift ;;
        --no-color) USE_COLOR=0;  shift ;;
        --help|-h)  usage; exit 0 ;;
        *)          printf '未知参数: %s (用 --help 查看用法)\n' "$1" >&2; exit 2 ;;
    esac
done

[ -n "${NO_COLOR:-}" ] && USE_COLOR=0
[ -t 1 ] || USE_COLOR=0

# ---------------------------------------------------------------------------
# 外观: 颜色 / 图标 / 排版 
# ---------------------------------------------------------------------------
if [ "$USE_COLOR" -eq 1 ]; then
    C_RESET=$'\033[0m'; C_DIM=$'\033[2m'; C_BOLD=$'\033[1m'
    C_GREEN=$'\033[32m'; C_RED=$'\033[31m'; C_YELLOW=$'\033[33m'
    C_CYAN=$'\033[36m'; C_BLUE=$'\033[34m'; C_GRAY=$'\033[90m'
else
    C_RESET=''; C_DIM=''; C_BOLD=''
    C_GREEN=''; C_RED=''; C_YELLOW=''; C_CYAN=''; C_BLUE=''; C_GRAY=''
fi

PASS_N=0; FAIL_N=0; WARN_N=0
WIDTH=64

_rule() {
    local l="$1" r="$3" i
    printf '%s%s' "$C_GRAY" "$l"
    for ((i=0; i<WIDTH; i++)); do printf '─'; done
    printf '%s%s\n' "$r" "$C_RESET"
}

# 计算字符串"显示宽度": 去掉 ANSI 转义; 3 字节及以上的字符 (CJK/全角) 记 2 列, 其余记 1 列
_disp_width() {
    local s="$1" plain w=0 i ch blen
    plain="$(printf '%s' "$s" | sed $'s/\033\\[[0-9;]*m//g')"
    local len=${#plain}
    for ((i=0; i<len; i++)); do
        ch="${plain:i:1}"
        # ✔ / ✗ 等 dingbat 虽为 3 字节, 终端渲染宽度为 1, 单列计
        case "$ch" in
            '✔'|'✗') w=$((w+1)); continue ;;
        esac
        blen=$(printf '%s' "$ch" | wc -c)
        if [ "$blen" -ge 3 ]; then w=$((w+2)); else w=$((w+1)); fi
    done
    echo "$w"
}

_bar_text() {
    local text="$1" dw pad
    dw="$(_disp_width "$text")"
    pad=$((WIDTH - 1 - dw))
    [ "$pad" -lt 0 ] && pad=0
    printf '%s│%s %s' "$C_GRAY" "$C_RESET" "$text"
    printf '%*s' "$pad" ''
    printf '%s│%s\n' "$C_GRAY" "$C_RESET"
}

banner() {
    local title="$1" sub="$2"
    echo
    _rule '╭' '' '╮'
    _bar_text "${C_BOLD}${C_CYAN}${title}${C_RESET}"
    [ -n "$sub" ] && _bar_text "${C_GRAY}${sub}${C_RESET}"
    _rule '╰' '' '╯'
    echo
}

section() {
    echo
    printf '%s%s  %s%s\n' "$C_BOLD" "$C_BLUE" "$1" "$C_RESET"
    _rule '├' '' '┤'
}

check_line() {
    local status="$1" label="$2" detail="${3:-}"
    local icon color
    case "$status" in
        ok)   icon='✔'; color="$C_GREEN";  PASS_N=$((PASS_N+1)) ;;
        fail) icon='✗'; color="$C_RED";    FAIL_N=$((FAIL_N+1)) ;;
        warn) icon='!'; color="$C_YELLOW"; WARN_N=$((WARN_N+1)) ;;
        info) icon='·'; color="$C_CYAN" ;;
        *)    icon='·'; color="$C_RESET" ;;
    esac
    local dw pad
    dw="$(_disp_width "$label")"
    pad=$((26 - dw))
    [ "$pad" -lt 0 ] && pad=0
    printf '  %s%s%s  %s' "$color" "$icon" "$C_RESET" "$label"
    printf '%*s' "$pad" ''
    if [ -n "$detail" ]; then
        printf '%s%s%s\n' "$C_DIM" "$detail" "$C_RESET"
    else
        printf '\n'
    fi
}

die() { printf '\n%s[ERROR]%s %s\n\n' "$C_RED" "$C_RESET" "$1" >&2; exit 1; }

# 交互式读取: 脚本可能被管道调用, 优先直接读终端
_ask() {
    local prompt="$1" default="${2:-}" reply=""
    if [ -r /dev/tty ]; then
        read -r -p "$prompt" reply </dev/tty || reply=""
    else
        read -r -p "$prompt" reply || reply=""
    fi
    [ -z "$reply" ] && reply="$default"
    printf '%s' "$reply"
}

# POSIX 单引号转义: 用于把本地字符串安全地交给远端 shell (处理空格 / CJK / 引号)
_shq() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

# 远端路径转义: 开头的 ~ 必须留在引号外, 否则远端 shell 视其为字面量,
# 会在当前目录建出一个名为 "~" 的目录。其余部分照常硬转义。
_shq_remote() {
    local p="$1"
    case "$p" in
        '~')   printf '~' ;;
        '~/'*) printf '~/'; _shq "${p#'~/'}" ;;
        *)     _shq "$p" ;;
    esac
}

# ---------------------------------------------------------------------------
# Tailscale: 定位 CLI 与枚举设备
# ---------------------------------------------------------------------------
find_tailscale() {
    if command -v tailscale >/dev/null 2>&1; then command -v tailscale; return 0; fi
    for p in /usr/bin/tailscale /usr/local/bin/tailscale /opt/homebrew/bin/tailscale \
             /Applications/Tailscale.app/Contents/MacOS/Tailscale; do
        [ -x "$p" ] && { echo "$p"; return 0; }
    done
    return 1
}

# 解析 `tailscale status` 纯文本输出, 每行: <IP> <主机名> <归属> <OS> <状态...>
# 之所以不用 --json: bash 侧无法保证有 jq/python3, 而纯文本列结构稳定且信息足够。
# 结果写入并列数组 PEER_IP / PEER_HOST / PEER_OS / PEER_ONLINE (1=在线)。
PEER_IP=(); PEER_HOST=(); PEER_OS=(); PEER_ONLINE=()

load_peers() {
    local ts="$1" self_ip="$2" line ip host os rest online
    while IFS= read -r line; do
        # 跳过空行与 `# Health check:` 之类的附注行
        case "$line" in ''|'#'*|' '*) continue ;; esac
        ip="$(printf '%s' "$line"   | awk '{print $1}')"
        host="$(printf '%s' "$line" | awk '{print $2}')"
        os="$(printf '%s' "$line"   | awk '{print $4}')"
        rest="$(printf '%s' "$line" | awk '{$1=$2=$3=$4=""; print}')"
        # 只接受 100.64.0.0/10 的 CGNAT 地址, 过滤掉表头 / 提示文字
        case "$ip" in 100.*) ;; *) continue ;; esac
        [ "$ip" = "$self_ip" ] && continue     # 排除本机
        if printf '%s' "$rest" | grep -qi 'offline'; then online=0; else online=1; fi
        PEER_IP+=("$ip"); PEER_HOST+=("$host"); PEER_OS+=("$os"); PEER_ONLINE+=("$online")
    done <<EOF
$("$ts" status 2>/dev/null)
EOF
    [ "${#PEER_IP[@]}" -gt 0 ]
}

# 按"在线优先"重排, 保证菜单里在线设备排在前面
sort_peers_online_first() {
    local n=${#PEER_IP[@]} i
    local ip=() host=() os=() on=()
    for ((i=0; i<n; i++)); do
        [ "${PEER_ONLINE[i]}" -eq 1 ] || continue
        ip+=("${PEER_IP[i]}"); host+=("${PEER_HOST[i]}"); os+=("${PEER_OS[i]}"); on+=(1)
    done
    for ((i=0; i<n; i++)); do
        [ "${PEER_ONLINE[i]}" -eq 0 ] || continue
        ip+=("${PEER_IP[i]}"); host+=("${PEER_HOST[i]}"); os+=("${PEER_OS[i]}"); on+=(0)
    done
    PEER_IP=("${ip[@]}"); PEER_HOST=("${host[@]}"); PEER_OS=("${os[@]}"); PEER_ONLINE=("${on[@]}")
}

print_peer_table() {
    local n=${#PEER_IP[@]} i mark color state dw pad
    section "可选设备 (共 $n 台)"
    for ((i=0; i<n; i++)); do
        if [ "${PEER_ONLINE[i]}" -eq 1 ]; then
            mark='●'; color="$C_GREEN"; state='在线'
        else
            mark='○'; color="$C_GRAY";  state='离线'
        fi
        dw="$(_disp_width "${PEER_HOST[i]}")"
        pad=$((22 - dw)); [ "$pad" -lt 0 ] && pad=0
        printf '  %s%2d)%s %s%s%s %s' \
            "$C_BOLD" "$((i+1))" "$C_RESET" "$color" "$mark" "$C_RESET" "${PEER_HOST[i]}"
        printf '%*s' "$pad" ''
        printf '%s%-16s %-8s %s%s\n' "$C_DIM" "${PEER_IP[i]}" "${PEER_OS[i]}" "$state" "$C_RESET"
    done
    echo
}

# 依据 --to 的值 (主机名或 IP) 定位设备下标, 找不到返回 1
find_peer_index() {
    local want="$1" n=${#PEER_IP[@]} i
    for ((i=0; i<n; i++)); do
        if [ "${PEER_IP[i]}" = "$want" ] || [ "${PEER_HOST[i]}" = "$want" ]; then
            echo "$i"; return 0
        fi
    done
    return 1
}

# ---------------------------------------------------------------------------
# 远端用户名记忆 (每台设备记住上次用的用户名, 免去重复输入)
# ---------------------------------------------------------------------------
CONF_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/ylx-copy-to-peer"
CONF_FILE="$CONF_DIR/peers.conf"

# 文件格式: 每行 `<IP><TAB><用户名>`
remembered_user() {
    [ -f "$CONF_FILE" ] || return 1
    awk -F'\t' -v ip="$1" '$1 == ip { print $2; found = 1; exit } END { exit !found }' "$CONF_FILE"
}

remember_user() {
    local ip="$1" user="$2" tmp
    mkdir -p "$CONF_DIR" 2>/dev/null || return 0
    tmp="$CONF_FILE.tmp.$$"
    if [ -f "$CONF_FILE" ]; then
        awk -F'\t' -v ip="$ip" '$1 != ip' "$CONF_FILE" > "$tmp" 2>/dev/null || : > "$tmp"
    else
        : > "$tmp"
    fi
    printf '%s\t%s\n' "$ip" "$user" >> "$tmp"
    mv -f "$tmp" "$CONF_FILE" 2>/dev/null || rm -f "$tmp"
    chmod 600 "$CONF_FILE" 2>/dev/null || true
}

# ---------------------------------------------------------------------------
# 传输前体检
# ---------------------------------------------------------------------------
SSH_PROBE_OPTS=(-o BatchMode=yes -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new)

# TCP 可达性: 优先 nc, 其次 bash 内建 /dev/tcp (无外部依赖)
tcp_reachable() {
    local host="$1" port="$2"
    if command -v nc >/dev/null 2>&1; then
        nc -z -w 5 "$host" "$port" >/dev/null 2>&1 && return 0
        return 1
    fi
    (exec 3<>"/dev/tcp/$host/$port") >/dev/null 2>&1 && return 0
    return 1
}

REMOTE_HAS_RSYNC=0
REMOTE_IS_WINDOWS=0

preflight() {
    local ts="$1" ip="$2" user="$3" port="$4" os="$5" online="$6"
    section '传输前体检'

    if pgrep -x tailscaled >/dev/null 2>&1 \
        || (command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet tailscaled 2>/dev/null); then
        check_line ok 'tailscaled 守护进程' '运行中'
    elif [ "$OS_TYPE" = 'Darwin' ]; then
        check_line warn 'tailscaled 守护进程' '未检测到 (macOS 后台服务可能仍正常)'
    else
        check_line fail 'tailscaled 守护进程' '未运行, 请先启动 Tailscale'
    fi

    if [ "$online" -eq 1 ]; then
        check_line ok '目标设备在线状态' '在线'
    else
        check_line warn '目标设备在线状态' '离线 (仍会尝试连接, 大概率超时)'
    fi

    if tcp_reachable "$ip" "$port"; then
        check_line ok "目标 ${port} 端口可达" "$ip:$port"
    else
        check_line fail "目标 ${port} 端口可达" "无法连接 $ip:$port, 确认目标机 sshd 已启动"
    fi

    # 免密登录是整个流程的前提: 失败时给出明确的修复指引
    if ssh "${SSH_PROBE_OPTS[@]}" -p "$port" "$user@$ip" true >/dev/null 2>&1; then
        check_line ok '免密登录 (公钥认证)' "$user@$ip"
    else
        check_line fail '免密登录 (公钥认证)' "以 $user 身份登录失败"
        printf '      %s提示: 把本机 ~/.ssh/id_ed25519.pub 加入目标机 SETUP.sh 的%s\n' "$C_DIM" "$C_RESET"
        printf '      %sSSH_PUBLIC_KEYS 数组后重跑 SETUP, 或直接追加到目标机 authorized_keys%s\n' "$C_DIM" "$C_RESET"
    fi

    case "$os" in
        windows|Windows) REMOTE_IS_WINDOWS=1 ;;
    esac

    if [ "$REMOTE_IS_WINDOWS" -eq 1 ]; then
        REMOTE_HAS_RSYNC=0
        check_line info '远端 rsync' 'Windows 目标, 使用 scp'
    elif ssh "${SSH_PROBE_OPTS[@]}" -p "$port" "$user@$ip" 'command -v rsync' >/dev/null 2>&1; then
        REMOTE_HAS_RSYNC=1
        check_line ok '远端 rsync' '可用'
    else
        REMOTE_HAS_RSYNC=0
        check_line warn '远端 rsync' '不可用, 将回退 scp'
    fi

    [ "$FAIL_N" -eq 0 ]
}

# ---------------------------------------------------------------------------
# 源路径 / 目标路径
# ---------------------------------------------------------------------------
SRC_ABS=""; SRC_IS_DIR=0; SRC_SIZE=""; SRC_COUNT=""

# 展开 ~ 并解析为绝对路径; 校验存在性; 统计大小与文件数
resolve_src() {
    local p="$1"
    case "$p" in
        '~')   p="$HOME" ;;
        '~/'*) p="$HOME/${p#'~/'}" ;;
    esac
    [ -e "$p" ] || return 1
    if command -v realpath >/dev/null 2>&1; then
        SRC_ABS="$(realpath "$p" 2>/dev/null || printf '%s' "$p")"
    else
        # macOS 无 realpath 时的回退: cd 进目录取 pwd
        if [ -d "$p" ]; then
            SRC_ABS="$(cd "$p" 2>/dev/null && pwd)"
        else
            SRC_ABS="$(cd "$(dirname "$p")" 2>/dev/null && pwd)/$(basename "$p")"
        fi
    fi
    [ -n "$SRC_ABS" ] || SRC_ABS="$p"
    if [ -d "$SRC_ABS" ]; then SRC_IS_DIR=1; else SRC_IS_DIR=0; fi
    SRC_SIZE="$(du -sh "$SRC_ABS" 2>/dev/null | awk '{print $1}')"
    [ -n "$SRC_SIZE" ] || SRC_SIZE='未知'
    if [ "$SRC_IS_DIR" -eq 1 ]; then
        SRC_COUNT="$(find "$SRC_ABS" -type f 2>/dev/null | wc -l | tr -d ' ')"
    else
        SRC_COUNT=1
    fi
    return 0
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
main() {
    export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/sbin:/sbin:$PATH"

    banner '向 tailnet 设备复制文件' "本机 $(hostname 2>/dev/null) · 平台 $OS_TYPE"

    local ts
    ts="$(find_tailscale)" || die '未找到 tailscale 命令, 请先运行 SETUP.sh 安装 Tailscale。'

    local self_ip
    self_ip="$("$ts" ip -4 2>/dev/null | head -n1)"
    [ -n "$self_ip" ] || die '无法获取本机 Tailscale IP, 请确认已登录 tailnet (tailscale status)。'

    load_peers "$ts" "$self_ip" || die 'tailnet 内没有发现其它设备, 请确认设备 B 已加入同一 tailnet。'
    sort_peers_online_first

    if [ "$DO_LIST" -eq 1 ]; then
        print_peer_table
        exit 0
    fi

    # --- 选择目标设备 ---
    local idx=""
    if [ -n "$OPT_TO" ]; then
        idx="$(find_peer_index "$OPT_TO")" \
            || die "在 tailnet 中找不到设备 '$OPT_TO' (用 --list 查看可选设备)。"
    else
        print_peer_table
        local n=${#PEER_IP[@]} choice
        while :; do
            choice="$(_ask "${C_BOLD}请选择目标设备编号${C_RESET} [1-$n] (q 退出): " '')"
            case "$choice" in
                q|Q) echo '已取消。'; exit 0 ;;
                ''|*[!0-9]*) printf '%s请输入 1-%d 之间的数字。%s\n' "$C_YELLOW" "$n" "$C_RESET" ;;
                *)
                    if [ "$choice" -ge 1 ] && [ "$choice" -le "$n" ]; then
                        idx=$((choice-1)); break
                    fi
                    printf '%s请输入 1-%d 之间的数字。%s\n' "$C_YELLOW" "$n" "$C_RESET"
                    ;;
            esac
        done
    fi

    local t_ip="${PEER_IP[idx]}" t_host="${PEER_HOST[idx]}"
    local t_os="${PEER_OS[idx]}" t_online="${PEER_ONLINE[idx]}"

    # 远端是否为 Windows 要在提示目标路径之前确定 (影响默认值与建目录方式)
    case "$t_os" in windows|Windows) REMOTE_IS_WINDOWS=1 ;; esac

    # --- 远端用户名 ---
    local t_user="$OPT_USER"
    if [ -z "$t_user" ]; then
        local default_user
        default_user="$(remembered_user "$t_ip")" || default_user="$(id -un)"
        [ -n "$default_user" ] || default_user="$(id -un)"
        if [ "$ASSUME_YES" -eq 1 ]; then
            t_user="$default_user"
        else
            t_user="$(_ask "${C_BOLD}远端用户名${C_RESET} [$default_user]: " "$default_user")"
        fi
    fi

    # --- 源路径 ---
    local src="$OPT_SRC"
    while :; do
        [ -n "$src" ] || src="$(_ask "${C_BOLD}要复制的文件/目录 (本机路径)${C_RESET}: " '')"
        [ -n "$src" ] || { printf '%s源路径不能为空。%s\n' "$C_YELLOW" "$C_RESET"; continue; }
        if resolve_src "$src"; then break; fi
        printf '%s路径不存在: %s%s\n' "$C_YELLOW" "$src" "$C_RESET"
        [ -n "$OPT_SRC" ] && die "源路径不存在: $OPT_SRC"
        src=""
    done

    # --- 目标路径 ---
    local dst="$OPT_DST"
    if [ -z "$dst" ]; then
        local default_dst='~/inbox/'
        # Windows OpenSSH 的默认 shell 多为 cmd.exe, 不展开 ~; 相对路径即用户主目录
        [ "$REMOTE_IS_WINDOWS" -eq 1 ] && default_dst='inbox/'
        dst="$(_ask "${C_BOLD}目标路径 (远端)${C_RESET} [$default_dst]: " "$default_dst")"
    fi

    # --- 体检 ---
    preflight "$ts" "$t_ip" "$t_user" "$OPT_PORT" "$t_os" "$t_online" || {
        echo
        printf '%s体检存在失败项, 已中止传输。%s\n\n' "$C_RED" "$C_RESET"
        exit 1
    }

    # --- 决定后端 ---
    # rsync 需要两端都有; --protect-args 让远端路径无需二次 shell 转义, 天然支持空格/CJK
    local backend='scp'
    if [ "$FORCE_SCP" -eq 0 ] && [ "$REMOTE_HAS_RSYNC" -eq 1 ] && command -v rsync >/dev/null 2>&1; then
        backend='rsync'
    fi

    # rsync 的尾斜杠语义容易误伤: 统一按"把源整体放进目标目录"处理,
    # 即源目录一律不带尾斜杠, 与 scp -r 的行为保持一致, 避免用户意外铺平目录。
    local src_arg="$SRC_ABS"
    local dst_display="$dst"

    # --- 预览 ---
    section '传输预览'
    check_line info '源路径' "$SRC_ABS"
    check_line info '源类型' "$([ "$SRC_IS_DIR" -eq 1 ] && echo "目录 · ${SRC_COUNT} 个文件" || echo '单个文件')"
    check_line info '源大小' "$SRC_SIZE"
    check_line info '目标设备' "$t_host ($t_ip · $t_os)"
    check_line info '目标路径' "$t_user@$t_ip:$dst_display"
    check_line info '传输后端' "$([ "$backend" = 'rsync' ] && echo 'rsync -avz --partial --progress' || echo 'scp -r')"
    [ "$DRY_RUN" -eq 1 ] && check_line warn '演练模式' '不会实际写入远端'

    # --- 构造命令 ---
    local -a cmd
    if [ "$backend" = 'rsync' ]; then
        cmd=(rsync -avz --partial --human-readable --progress --protect-args
             -e "ssh -p $OPT_PORT -o StrictHostKeyChecking=accept-new"
             "$src_arg" "$t_user@$t_ip:$dst")
        [ "$DRY_RUN" -eq 1 ] && cmd=("${cmd[@]:0:1}" -n "${cmd[@]:1}")
    else
        # scp 会把远端路径再交给一次远端 shell 解析, 必须自行转义
        cmd=(scp -r -P "$OPT_PORT" -o StrictHostKeyChecking=accept-new
             "$src_arg" "$t_user@$t_ip:$(_shq_remote "$dst")")
    fi

    echo
    printf '  %s实际命令:%s\n  %s%s%s\n' "$C_GRAY" "$C_RESET" "$C_DIM" "${cmd[*]}" "$C_RESET"
    echo

    # --- 确认 ---
    if [ "$ASSUME_YES" -eq 0 ]; then
        local ans
        ans="$(_ask "${C_BOLD}确认开始传输?${C_RESET} [y/N]: " 'n')"
        case "$ans" in
            y|Y|yes|YES) ;;
            *) echo '已取消。'; exit 0 ;;
        esac
    fi

    # --- 远端建目录 ---
    # 只在目标以 / 结尾 (明确是目录) 时创建, 避免把"目标文件名"误建成目录
    case "$dst" in
        */)
            if [ "$DRY_RUN" -eq 0 ]; then
                if [ "$REMOTE_IS_WINDOWS" -eq 1 ]; then
                    # Windows OpenSSH 默认 shell 可能是 cmd.exe, mkdir -p 不可用
                    ssh -p "$OPT_PORT" -o StrictHostKeyChecking=accept-new "$t_user@$t_ip" \
                        "powershell -NoProfile -Command \"New-Item -ItemType Directory -Force -Path '${dst}' | Out-Null\"" \
                        >/dev/null 2>&1 || true
                else
                    ssh -p "$OPT_PORT" -o StrictHostKeyChecking=accept-new "$t_user@$t_ip" \
                        "mkdir -p $(_shq_remote "$dst")" >/dev/null 2>&1 || true
                fi
            fi
            ;;
    esac

    # --- 执行 ---
    if [ "$backend" = 'scp' ] && [ "$DRY_RUN" -eq 1 ]; then
        printf '\n%s演练模式: scp 无 --dry-run, 上方命令未执行。%s\n\n' "$C_YELLOW" "$C_RESET"
        exit 0
    fi

    section '开始传输'
    local start_ts=$SECONDS rc=0
    # 不捕获 stdout: rsync/scp 的进度条需要直连终端
    "${cmd[@]}" || rc=$?
    local elapsed=$((SECONDS - start_ts))

    remember_user "$t_ip" "$t_user"

    # --- 结果 ---
    echo
    _rule '╭' '' '╮'
    if [ "$rc" -eq 0 ]; then
        _bar_text "${C_BOLD}${C_GREEN}传输完成${C_RESET}"
        _bar_text "${C_GRAY}耗时 ${elapsed}s · ${SRC_SIZE} · ${SRC_COUNT} 个文件${C_RESET}"
        _bar_text "${C_GRAY}校验:${C_RESET} ssh -p ${OPT_PORT} ${t_user}@${t_ip} ls -la ${dst}"
        _rule '╰' '' '╯'
        echo
        return 0
    fi

    _bar_text "${C_BOLD}${C_RED}传输失败 (退出码 $rc)${C_RESET}"
    _rule '╰' '' '╯'
    echo
    printf '  %s常见原因:%s\n' "$C_BOLD" "$C_RESET"
    printf '  %s· 远端目标目录不存在或无写权限 → 目标路径以 / 结尾可自动创建%s\n' "$C_DIM" "$C_RESET"
    printf '  %s· 远端磁盘空间不足 → ssh 过去 df -h 查看%s\n' "$C_DIM" "$C_RESET"
    printf '  %s· 传输中途 tailnet 断开 → rsync 可直接重跑续传, scp 需重来%s\n' "$C_DIM" "$C_RESET"
    printf '  %s· 目标为 Windows 且路径含反斜杠 → 改用正斜杠, 如 C:/Users/xxx/%s\n' "$C_DIM" "$C_RESET"
    echo
    return "$rc"
}

main "$@"

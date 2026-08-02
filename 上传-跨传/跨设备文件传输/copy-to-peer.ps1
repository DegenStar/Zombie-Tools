<#
    copy-to-peer.ps1 — 向 tailnet 内的另一台设备复制文件 / 目录 (Windows 原生)

    前提: 本机 (设备 A) 与目标机 (设备 B) 均已加入同一 tailnet, 且目标机已由
          SETUP.sh / SETUP.ps1 配置好 sshd (端口 22) 与 ed25519 公钥免密登录。

    功能:
      1. 从 tailscale status --json 枚举同 tailnet 的设备, 在线优先, 交互式编号选择
      2. 传输前逐项体检 (Tailscale 服务 / 设备在线 / 22 端口可达 / 免密登录 / 远端 rsync)
      3. 优先 rsync (若本机有), 缺失时回退 scp -r (随 OpenSSH 自带, Windows 必定可用)
      4. 传输前打印预览 (源大小、文件数、目标路径、实际命令) 并要求确认

    本脚本独立自足, 不依赖 SETUP.ps1。除远端 mkdir 外不改动任何一侧的系统配置。
    无需管理员权限。

    用法:
      powershell -ExecutionPolicy Bypass -File copy-to-peer.ps1
      powershell -ExecutionPolicy Bypass -File copy-to-peer.ps1 -List
      powershell -ExecutionPolicy Bypass -File copy-to-peer.ps1 -To nucbox-m6 -Src C:\data -Dst '~/inbox/'
      powershell -ExecutionPolicy Bypass -File copy-to-peer.ps1 -To 100.75.62.55 -Src .\a -Dst '~/' -Yes
#>

param(
    [string]$To,
    [string]$Src,
    [string]$Dst,
    [string]$User,
    [int]$Port = 22,
    [switch]$List,
    [switch]$Scp,
    [switch]$DryRun,
    [switch]$Yes,
    [switch]$NoColor,
    [switch]$LibraryOnly
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

# 控制台切到 UTF-8, 否则 CJK 与制表符会显示为乱码
try {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    $null = & chcp.com 65001 2>$null
} catch {}

$UseColor = -not $NoColor
if ($env:NO_COLOR) { $UseColor = $false }

# ---------------------------------------------------------------------------
# 外观: 颜色 / 图标 / 排版 (与 verify-ssh-config.ps1 保持一致)
# ---------------------------------------------------------------------------
$Width = 64
$script:PassN = 0
$script:FailN = 0
$script:WarnN = 0

function Get-DisplayWidth {
    param([string]$Text)
    $plain = [regex]::Replace($Text, "`e\[[0-9;]*m", '')
    $w = 0
    foreach ($ch in $plain.ToCharArray()) {
        $code = [int][char]$ch
        # ✔ (0x2714) / ✗ (0x2717) 等 dingbat 终端渲染宽度为 1
        if ($code -eq 0x2714 -or $code -eq 0x2717) { $w += 1; continue }
        # CJK / 全角区间记 2 列
        if ( ($code -ge 0x1100 -and $code -le 0x115F) -or
             ($code -ge 0x2E80 -and $code -le 0xA4CF) -or
             ($code -ge 0xAC00 -and $code -le 0xD7A3) -or
             ($code -ge 0xF900 -and $code -le 0xFAFF) -or
             ($code -ge 0xFF00 -and $code -le 0xFF60) -or
             ($code -ge 0xFFE0 -and $code -le 0xFFE6) ) {
            $w += 2
        } else {
            $w += 1
        }
    }
    return $w
}

function Write-Part {
    param([string]$Text, [string]$Color, [switch]$NoNewline)
    if ($UseColor -and $Color) {
        Write-Host $Text -ForegroundColor $Color -NoNewline:$NoNewline
    } else {
        Write-Host $Text -NoNewline:$NoNewline
    }
}

function Write-Rule {
    param([string]$Left, [string]$Right)
    Write-Part ($Left + ('─' * $Width) + $Right) 'DarkGray'
}

function Write-BarText {
    param([string]$Text)
    $pad = $Width - 1 - (Get-DisplayWidth $Text)
    if ($pad -lt 0) { $pad = 0 }
    Write-Part '│' 'DarkGray' -NoNewline
    Write-Host (' ' + $Text + (' ' * $pad)) -NoNewline
    Write-Part '│' 'DarkGray'
}

function Write-Banner {
    param([string]$Title, [string]$Sub)
    Write-Host ''
    Write-Rule '╭' '╮'
    Write-BarText $Title
    if ($Sub) { Write-BarText $Sub }
    Write-Rule '╰' '╯'
    Write-Host ''
}

function Write-Section {
    param([string]$Title)
    Write-Host ''
    Write-Part ('  ' + $Title) 'Blue'
    Write-Rule '├' '┤'
}

function Write-Check {
    param(
        [ValidateSet('ok', 'fail', 'warn', 'info')]
        [string]$Status,
        [string]$Label,
        [string]$Detail = ''
    )
    switch ($Status) {
        'ok'   { $icon = '✔'; $color = 'Green';  $script:PassN++ }
        'fail' { $icon = '✗'; $color = 'Red';    $script:FailN++ }
        'warn' { $icon = '!'; $color = 'Yellow'; $script:WarnN++ }
        'info' { $icon = '·'; $color = 'Cyan' }
    }
    $pad = 26 - (Get-DisplayWidth $Label)
    if ($pad -lt 0) { $pad = 0 }
    Write-Host '  ' -NoNewline
    Write-Part $icon $color -NoNewline
    Write-Host ('  ' + $Label + (' ' * $pad)) -NoNewline
    if ($Detail) { Write-Part $Detail 'DarkGray' } else { Write-Host '' }
}

function Stop-WithError {
    param([string]$Message)
    Write-Host ''
    Write-Part "[ERROR] $Message" 'Red'
    Write-Host ''
    exit 1
}

# ---------------------------------------------------------------------------
# 路径转义
# ---------------------------------------------------------------------------
# 远端路径要经过远端 shell 再解析一次, 需单引号硬转义以容纳空格 / CJK / 引号。
# 但开头的 ~ 必须留在引号外, 否则远端 shell 视其为字面量, 会建出名为 "~" 的目录。
function ConvertTo-RemotePath {
    param([string]$Path)
    $esc = { param($s) "'" + ($s -replace "'", "'\''") + "'" }
    if ($Path -eq '~') { return '~' }
    if ($Path.StartsWith('~/')) { return '~/' + (& $esc $Path.Substring(2)) }
    return (& $esc $Path)
}

# ---------------------------------------------------------------------------
# Tailscale: 定位 CLI 与枚举设备
# ---------------------------------------------------------------------------
function Get-TailscaleExe {
    $cmd = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($base in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $base) { continue }
        $candidate = Join-Path $base 'Tailscale\tailscale.exe'
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

function Test-TailscaleIPv4 {
    param([string]$Address)
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    $bytes = $parsed.GetAddressBytes()
    return $bytes.Length -eq 4 -and $bytes[0] -eq 100 -and $bytes[1] -ge 64 -and $bytes[1] -le 127
}

# 解析 tailscale status --json 的 Peer 映射, 排除本机, 在线设备排前面
function Get-TailnetPeer {
    param([string]$TailscaleExe)
    $raw = & $TailscaleExe status --json 2>$null
    if (-not $raw) { return @() }
    try { $status = ($raw | Out-String) | ConvertFrom-Json } catch { return @() }
    if (-not $status.Peer) { return @() }

    $peers = @()
    foreach ($prop in $status.Peer.PSObject.Properties) {
        $p = $prop.Value
        $ip = @($p.TailscaleIPs) | Where-Object { Test-TailscaleIPv4 $_ } | Select-Object -First 1
        if (-not $ip) { continue }
        $peers += [pscustomobject]@{
            HostName = [string]$p.HostName
            Ip       = [string]$ip
            Os       = [string]$p.OS
            Online   = [bool]$p.Online
        }
    }
    # 在线优先, 同组内按主机名排序
    return @($peers | Sort-Object @{Expression = { -not $_.Online }}, HostName)
}

function Write-PeerTable {
    param([object[]]$Peers)
    Write-Section "可选设备 (共 $($Peers.Count) 台)"
    for ($i = 0; $i -lt $Peers.Count; $i++) {
        $p = $Peers[$i]
        if ($p.Online) { $mark = '●'; $color = 'Green'; $state = '在线' }
        else           { $mark = '○'; $color = 'DarkGray'; $state = '离线' }
        $pad = 22 - (Get-DisplayWidth $p.HostName)
        if ($pad -lt 0) { $pad = 0 }
        Write-Host ('  {0,2}) ' -f ($i + 1)) -NoNewline
        Write-Part $mark $color -NoNewline
        Write-Host (' ' + $p.HostName + (' ' * $pad)) -NoNewline
        Write-Part ('{0,-16} {1,-8} {2}' -f $p.Ip, $p.Os, $state) 'DarkGray'
    }
    Write-Host ''
}

# ---------------------------------------------------------------------------
# 远端用户名记忆 (每台设备记住上次用的用户名, 免去重复输入)
# ---------------------------------------------------------------------------
# APPDATA 在 Windows 上恒有值; 回退分支是为了让本文件能在 pwsh (Linux/macOS) 下点源测试
$script:ConfBase = if ($env:APPDATA) { $env:APPDATA } else { Join-Path $HOME '.config' }
$script:ConfDir  = Join-Path $script:ConfBase 'ylx-copy-to-peer'
$script:ConfFile = Join-Path $script:ConfDir 'peers.conf'

# 文件格式: 每行 `<IP><TAB><用户名>`
function Get-RememberedUser {
    param([string]$Ip)
    if (-not (Test-Path $script:ConfFile)) { return $null }
    foreach ($line in (Get-Content -LiteralPath $script:ConfFile -Encoding UTF8 -ErrorAction SilentlyContinue)) {
        $parts = $line -split "`t", 2
        if ($parts.Count -eq 2 -and $parts[0] -eq $Ip) { return $parts[1] }
    }
    return $null
}

function Set-RememberedUser {
    param([string]$Ip, [string]$UserName)
    try {
        if (-not (Test-Path $script:ConfDir)) {
            $null = New-Item -ItemType Directory -Force -Path $script:ConfDir
        }
        $kept = @()
        if (Test-Path $script:ConfFile) {
            $kept = @(Get-Content -LiteralPath $script:ConfFile -Encoding UTF8 -ErrorAction SilentlyContinue |
                      Where-Object { ($_ -split "`t", 2)[0] -ne $Ip })
        }
        $kept += ("{0}`t{1}" -f $Ip, $UserName)
        Set-Content -LiteralPath $script:ConfFile -Value $kept -Encoding UTF8
    } catch {
        # 记忆失败不影响传输本身
    }
}

# ---------------------------------------------------------------------------
# 传输前体检
# ---------------------------------------------------------------------------
$script:SshProbeOpts = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=accept-new')

function Test-TcpReachable {
    param([string]$ComputerName, [int]$TcpPort, [int]$TimeoutMs = 5000)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($ComputerName, $TcpPort, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)) { return $false }
        $client.EndConnect($async)
        return $client.Connected
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Invoke-Preflight {
    param([object]$Peer, [string]$UserName, [int]$TcpPort)
    Write-Section '传输前体检'

    $svc = Get-Service -Name Tailscale -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -eq 'Running') {
        Write-Check ok 'Tailscale 服务' '运行中'
    } else {
        Write-Check fail 'Tailscale 服务' '未运行, 请先启动 Tailscale'
    }

    if ($Peer.Online) {
        Write-Check ok '目标设备在线状态' '在线'
    } else {
        Write-Check warn '目标设备在线状态' '离线 (仍会尝试连接, 大概率超时)'
    }

    if (Test-TcpReachable $Peer.Ip $TcpPort) {
        Write-Check ok "目标 $TcpPort 端口可达" "$($Peer.Ip):$TcpPort"
    } else {
        Write-Check fail "目标 $TcpPort 端口可达" "无法连接 $($Peer.Ip):$TcpPort, 确认目标机 sshd 已启动"
    }

    # 免密登录是整个流程的前提: 失败时给出明确的修复指引
    $null = & ssh @script:SshProbeOpts -p $TcpPort "$UserName@$($Peer.Ip)" 'exit 0' 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Check ok '免密登录 (公钥认证)' "$UserName@$($Peer.Ip)"
    } else {
        Write-Check fail '免密登录 (公钥认证)' "以 $UserName 身份登录失败"
        Write-Part '      提示: 把本机 .ssh\id_ed25519.pub 加入目标机 SETUP 脚本的' 'DarkGray'
        Write-Part '      SSH_PUBLIC_KEYS 后重跑 SETUP, 或直接追加到目标机 authorized_keys' 'DarkGray'
    }

    $script:RemoteIsWindows = ($Peer.Os -match '^(?i)windows$')
    if ($script:RemoteIsWindows) {
        $script:RemoteHasRsync = $false
        Write-Check info '远端 rsync' 'Windows 目标, 使用 scp'
    } else {
        $null = & ssh @script:SshProbeOpts -p $TcpPort "$UserName@$($Peer.Ip)" 'command -v rsync' 2>$null
        $script:RemoteHasRsync = ($LASTEXITCODE -eq 0)
        if ($script:RemoteHasRsync) {
            Write-Check ok '远端 rsync' '可用'
        } else {
            Write-Check warn '远端 rsync' '不可用, 将回退 scp'
        }
    }

    return ($script:FailN -eq 0)
}

# ---------------------------------------------------------------------------
# 源路径
# ---------------------------------------------------------------------------
function Resolve-SourcePath {
    param([string]$Path)
    if ($Path -eq '~')          { $Path = $HOME }
    elseif ($Path.StartsWith('~/') -or $Path.StartsWith('~\')) {
        $Path = Join-Path $HOME $Path.Substring(2)
    }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item) { return $null }

    $isDir = $item.PSIsContainer
    if ($isDir) {
        $files = @(Get-ChildItem -LiteralPath $item.FullName -Recurse -File -ErrorAction SilentlyContinue)
        $bytes = ($files | Measure-Object -Property Length -Sum).Sum
        $count = $files.Count
    } else {
        $bytes = $item.Length
        $count = 1
    }
    if (-not $bytes) { $bytes = 0 }

    return [pscustomobject]@{
        FullName = $item.FullName
        IsDir    = $isDir
        Count    = $count
        Size     = (Format-ByteSize $bytes)
    }
}

function Format-ByteSize {
    param([double]$Bytes)
    $units = @('B', 'K', 'M', 'G', 'T')
    $i = 0
    while ($Bytes -ge 1024 -and $i -lt ($units.Count - 1)) { $Bytes /= 1024; $i++ }
    if ($i -eq 0) { return ('{0:N0}{1}' -f $Bytes, $units[$i]) }
    return ('{0:N1}{1}' -f $Bytes, $units[$i])
}

# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
function Invoke-CopyToPeer {
    Write-Banner '向 tailnet 设备复制文件' "本机 $env:COMPUTERNAME · 平台 Windows"

    $ts = Get-TailscaleExe
    if (-not $ts) { Stop-WithError '未找到 tailscale.exe, 请先运行 SETUP.ps1 安装 Tailscale。' }

    $peers = Get-TailnetPeer -TailscaleExe $ts
    if ($peers.Count -eq 0) {
        Stop-WithError 'tailnet 内没有发现其它设备, 请确认设备 B 已加入同一 tailnet。'
    }

    if ($List) { Write-PeerTable $peers; return 0 }

    # --- 选择目标设备 ---
    $peer = $null
    if ($To) {
        $byIp = @($peers | Where-Object { $_.Ip -eq $To })
        if ($byIp.Count -eq 1) {
            $peer = $byIp[0]
        } else {
            # JSON 里的 HostName 并不保证唯一 (多台同名机器很常见), 命中多个时
            # 绝不能默默选第一个 —— 那可能把文件发到错误的设备上。
            $byName = @($peers | Where-Object { $_.HostName -ieq $To })
            if ($byName.Count -eq 1) {
                $peer = $byName[0]
            } elseif ($byName.Count -gt 1) {
                Write-Part "设备名 '$To' 对应多台设备, 请改用 Tailscale IP 指定:" 'Yellow'
                $byName | ForEach-Object { Write-Host ("    {0,-16} {1} {2}" -f $_.Ip, $_.HostName, $_.Os) }
                Stop-WithError '目标设备不唯一。'
            } else {
                Stop-WithError "在 tailnet 中找不到设备 '$To' (用 -List 查看可选设备)。"
            }
        }
    } else {
        Write-PeerTable $peers
        while (-not $peer) {
            $choice = Read-Host "请选择目标设备编号 [1-$($peers.Count)] (q 退出)"
            if ($choice -ieq 'q') { Write-Host '已取消。'; return 0 }
            $n = 0
            if ([int]::TryParse($choice, [ref]$n) -and $n -ge 1 -and $n -le $peers.Count) {
                $peer = $peers[$n - 1]
            } else {
                Write-Part "请输入 1-$($peers.Count) 之间的数字。" 'Yellow'
            }
        }
    }

    # 远端是否为 Windows 要在提示目标路径之前确定 (影响默认值与建目录方式)
    $script:RemoteIsWindows = ($peer.Os -match '^(?i)windows$')

    # --- 远端用户名 ---
    $userName = $User
    if (-not $userName) {
        $defaultUser = Get-RememberedUser $peer.Ip
        if (-not $defaultUser) { $defaultUser = $env:USERNAME }
        if ($Yes) {
            $userName = $defaultUser
        } else {
            $userName = Read-Host "远端用户名 [$defaultUser]"
            if (-not $userName) { $userName = $defaultUser }
        }
    }

    # --- 源路径 ---
    $source = $null
    $srcInput = $Src
    while (-not $source) {
        if (-not $srcInput) { $srcInput = Read-Host '要复制的文件/目录 (本机路径)' }
        if (-not $srcInput) { Write-Part '源路径不能为空。' 'Yellow'; continue }
        $source = Resolve-SourcePath $srcInput
        if (-not $source) {
            Write-Part "路径不存在: $srcInput" 'Yellow'
            if ($Src) { Stop-WithError "源路径不存在: $Src" }
            $srcInput = $null
        }
    }

    # --- 目标路径 ---
    $destination = $Dst
    if (-not $destination) {
        # Windows OpenSSH 的默认 shell 多为 cmd.exe, 不展开 ~; 相对路径即用户主目录
        $defaultDst = if ($script:RemoteIsWindows) { 'inbox/' } else { '~/inbox/' }
        $destination = Read-Host "目标路径 (远端) [$defaultDst]"
        if (-not $destination) { $destination = $defaultDst }
    }

    # --- 体检 ---
    if (-not (Invoke-Preflight -Peer $peer -UserName $userName -TcpPort $Port)) {
        Write-Host ''
        Write-Part '体检存在失败项, 已中止传输。' 'Red'
        Write-Host ''
        return 1
    }

    # --- 决定后端 ---
    $hasLocalRsync = [bool](Get-Command rsync -ErrorAction SilentlyContinue)
    $backend = if ((-not $Scp) -and $script:RemoteHasRsync -and $hasLocalRsync) { 'rsync' } else { 'scp' }

    # --- 预览 ---
    Write-Section '传输预览'
    Write-Check info '源路径'   $source.FullName
    Write-Check info '源类型'   $(if ($source.IsDir) { "目录 · $($source.Count) 个文件" } else { '单个文件' })
    Write-Check info '源大小'   $source.Size
    Write-Check info '目标设备' "$($peer.HostName) ($($peer.Ip) · $($peer.Os))"
    Write-Check info '目标路径' "$userName@$($peer.Ip):$destination"
    Write-Check info '传输后端' $(if ($backend -eq 'rsync') { 'rsync -avz --partial --progress' } else { 'scp -r' })
    if ($DryRun) { Write-Check warn '演练模式' '不会实际写入远端' }

    # --- 构造命令 ---
    $target = "$userName@$($peer.Ip):"
    if ($backend -eq 'rsync') {
        # --protect-args 让远端路径免于二次 shell 拆分, 天然支持空格 / CJK
        $exe  = 'rsync'
        $argv = @('-avz', '--partial', '--human-readable', '--progress', '--protect-args',
                  '-e', "ssh -p $Port -o StrictHostKeyChecking=accept-new",
                  $source.FullName, ($target + $destination))
        if ($DryRun) { $argv = @('-n') + $argv }
    } else {
        $exe  = 'scp'
        $argv = @('-r', '-P', "$Port", '-o', 'StrictHostKeyChecking=accept-new',
                  $source.FullName, ($target + (ConvertTo-RemotePath $destination)))
    }

    Write-Host ''
    Write-Part '  实际命令:' 'DarkGray'
    Write-Part ('  ' + $exe + ' ' + ($argv -join ' ')) 'DarkGray'
    Write-Host ''

    # --- 确认 ---
    if (-not $Yes) {
        $ans = Read-Host '确认开始传输? [y/N]'
        if ($ans -notmatch '^(?i)y(es)?$') { Write-Host '已取消。'; return 0 }
    }

    # --- 远端建目录 (仅当目标以 / 结尾, 明确是目录时) ---
    if ($destination.EndsWith('/') -and -not $DryRun) {
        try {
            if ($script:RemoteIsWindows) {
                # Windows OpenSSH 默认 shell 可能是 cmd.exe, mkdir -p 不可用
                $psCmd = "New-Item -ItemType Directory -Force -Path '$destination' | Out-Null"
                $null = & ssh -p $Port -o StrictHostKeyChecking=accept-new "$userName@$($peer.Ip)" `
                            "powershell -NoProfile -Command `"$psCmd`"" 2>$null
            } else {
                $null = & ssh -p $Port -o StrictHostKeyChecking=accept-new "$userName@$($peer.Ip)" `
                            ("mkdir -p " + (ConvertTo-RemotePath $destination)) 2>$null
            }
        } catch {}
    }

    if ($backend -eq 'scp' -and $DryRun) {
        Write-Host ''
        Write-Part '演练模式: scp 无 --dry-run, 上方命令未执行。' 'Yellow'
        Write-Host ''
        return 0
    }

    # --- 执行 ---
    Write-Section '开始传输'
    $sw = [Diagnostics.Stopwatch]::StartNew()
    # 不捕获输出: rsync/scp 的进度条需要直连终端
    & $exe @argv
    $rc = $LASTEXITCODE
    $sw.Stop()

    Set-RememberedUser -Ip $peer.Ip -UserName $userName

    # --- 结果 ---
    Write-Host ''
    Write-Rule '╭' '╮'
    if ($rc -eq 0) {
        Write-BarText '传输完成'
        Write-BarText ("耗时 {0}s · {1} · {2} 个文件" -f [int]$sw.Elapsed.TotalSeconds, $source.Size, $source.Count)
        Write-BarText ("校验: ssh -p $Port $userName@$($peer.Ip) ls -la $destination")
        Write-Rule '╰' '╯'
        Write-Host ''
        return 0
    }

    Write-BarText "传输失败 (退出码 $rc)"
    Write-Rule '╰' '╯'
    Write-Host ''
    Write-Host '  常见原因:'
    Write-Part '  · 远端目标目录不存在或无写权限 → 目标路径以 / 结尾可自动创建' 'DarkGray'
    Write-Part '  · 远端磁盘空间不足 → ssh 过去 df -h 查看' 'DarkGray'
    Write-Part '  · 传输中途 tailnet 断开 → rsync 可直接重跑续传, scp 需重来' 'DarkGray'
    Write-Part '  · 目标为 Windows 且路径含反斜杠 → 改用正斜杠, 如 C:/Users/xxx/' 'DarkGray'
    Write-Host ''
    return $rc
}

# -LibraryOnly 供 tests/ 点源加载本文件后单独测试各函数
if ($LibraryOnly) { return }

exit (Invoke-CopyToPeer)

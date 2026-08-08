param(
    [string]$RelaunchWorkingDirectory,
    [switch]$LibraryOnly,
    [switch]$NoPause
)

if (-not $LibraryOnly -and -not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $scriptPath = $PSCommandPath
    if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Definition }
    $psExe = (Get-Process -Id $PID).Path
    if (-not $psExe) { $psExe = 'powershell.exe' }
    $quote = { param($value) '"' + ($value -replace '"', '\"') + '"' }
    $workDir = if ($PWD.Path) { $PWD.Path } else { '' }
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', (& $quote $scriptPath),
        '-RelaunchWorkingDirectory', (& $quote $workDir)
    )
    if ($NoPause) { $arguments += '-NoPause' }

    try {
        $elevated = Start-Process -FilePath $psExe -ArgumentList $arguments -Verb RunAs -Wait -PassThru
        exit $(if ($null -ne $elevated.ExitCode) { $elevated.ExitCode } else { 0 })
    } catch {
        Write-Host '[ERROR] 需要管理员权限；UAC 提权被取消或阻止。' -ForegroundColor Red
        exit 1
    }
}

if ($RelaunchWorkingDirectory -and (Test-Path -LiteralPath $RelaunchWorkingDirectory -PathType Container)) {
    Set-Location -LiteralPath $RelaunchWorkingDirectory
}

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
try {
    $OutputEncoding = [Text.Encoding]::UTF8
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    if (Get-Command chcp -ErrorAction SilentlyContinue) { chcp 65001 > $null 2>&1 }
} catch {}

# Telegram 是邀请文件和一次性密码的必需交付通道；凭据从环境变量读取。
$TgBotToken = $env:REMOTE_ASSISTANCE_TG_BOT_TOKEN
$TgChatId   = $env:REMOTE_ASSISTANCE_TG_CHAT_ID

$script:TailnetCidr = '100.64.0.0/10'
$script:RemoteAssistancePort = 3389
$script:RemoteAssistanceRuleName = 'Remote-Assistance-Tailscale-TCP'

function Write-Log  { param([string]$Message) Write-Host "[*] $Message" -ForegroundColor Cyan }
function Write-Warn { param([string]$Message) Write-Host "[!] $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

function Wait-BeforeExit {
    Write-Host ''
    try { [void](Read-Host '按 Enter 键关闭窗口') } catch {}
}

function Assert-TelegramConfig {
    if ([string]::IsNullOrWhiteSpace($TgBotToken) -or [string]::IsNullOrWhiteSpace($TgChatId)) {
        throw 'Telegram Bot Token 和 Chat ID 必须配置。'
    }
}

function Test-TailscaleIPv4 {
    param([string]$Address)
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    $bytes = $parsed.GetAddressBytes()
    return $bytes.Length -eq 4 -and $bytes[0] -eq 100 -and (($bytes[1] -band 192) -eq 64)
}

function New-InvitationPassword {
    param([ValidateRange(12, 64)][int]$Length = 20)
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
    $builder = New-Object Text.StringBuilder
    $random = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $buffer = New-Object byte[] 1
        while ($builder.Length -lt $Length) {
            $random.GetBytes($buffer)
            if ($buffer[0] -ge 248) { continue }
            [void]$builder.Append($alphabet[[int]($buffer[0] % $alphabet.Length)])
        }
        return $builder.ToString()
    } finally {
        $random.Dispose()
    }
}

function Test-InvitationEndpoint {
    param([string]$Content, [string]$TailscaleIp)
    if (-not $Content -or -not (Test-TailscaleIPv4 $TailscaleIp)) { return $false }
    $escaped = [regex]::Escape($TailscaleIp)
    return $Content -match "(?<![0-9.])${escaped}:$($script:RemoteAssistancePort)(?![0-9])"
}

function Get-TailscaleExe {
    $command = Get-Command tailscale -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidate = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    return $null
}

function Assert-TailscaleReady {
    $service = Get-Service -Name Tailscale -ErrorAction Stop
    if ($service.Status -ne 'Running') { throw 'Tailscale 服务未运行。' }
    $tailscale = Get-TailscaleExe
    if (-not $tailscale) { throw '未找到 tailscale.exe。' }

    $global:LASTEXITCODE = 0
    $rawStatus = (& $tailscale status --json 2>$null) -join "`n"
    if ($global:LASTEXITCODE -ne 0 -or -not $rawStatus) { throw '无法读取 Tailscale 状态。' }
    $status = $rawStatus | ConvertFrom-Json
    if ($status.BackendState -ne 'Running') { throw "Tailscale BackendState=$($status.BackendState)，未就绪。" }

    $global:LASTEXITCODE = 0
    $addresses = @(& $tailscale ip -4 2>$null)
    $address = $addresses | ForEach-Object { $_.ToString().Trim() } |
        Where-Object { Test-TailscaleIPv4 $_ } | Select-Object -First 1
    if ($global:LASTEXITCODE -ne 0 -or -not $address) { throw '未获取到有效的 Tailnet IPv4 地址。' }
    return $address
}

function Get-MsraExe {
    if (-not $env:SystemRoot) { throw '无法确定 Windows 系统目录。' }
    $candidate = Join-Path $env:SystemRoot 'System32\msra.exe'
    if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
        throw '当前 Windows 未提供 msra.exe；请改用 RDP 或 Quick Assist。'
    }
    return $candidate
}

function Enable-SolicitedRemoteAssistance {
    $policyPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
    $systemPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Remote Assistance'
    foreach ($path in @($policyPath, $systemPath)) {
        New-Item -Path $path -Force -ErrorAction Stop | Out-Null
        Set-ItemProperty -LiteralPath $path -Name fAllowToGetHelp -Type DWord -Value 1
        Set-ItemProperty -LiteralPath $path -Name fAllowFullControl -Type DWord -Value 1
    }
}

function Assert-RemoteAssistancePolicy {
    $policyPath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services'
    $systemPath = 'HKLM:\SYSTEM\CurrentControlSet\Control\Remote Assistance'
    foreach ($path in @($policyPath, $systemPath)) {
        $settings = Get-ItemProperty -LiteralPath $path -ErrorAction Stop
        if ($settings.fAllowToGetHelp -ne 1) { throw "Remote Assistance 受邀式连接策略未启用：$path" }
        if ($settings.fAllowFullControl -ne 1) { throw "Remote Assistance 完整控制请求策略未启用：$path" }
    }
}

function Test-RemoteAssistancePortExpression {
    param([object]$LocalPort)
    foreach ($entry in @($LocalPort)) {
        foreach ($token in ("$entry" -split ',')) {
            $value = $token.Trim()
            if ($value -eq "$($script:RemoteAssistancePort)") { return $true }
            if ($value -match '^(\d+)-(\d+)$') {
                $lower = [int]$Matches[1]
                $upper = [int]$Matches[2]
                if ($lower -le $script:RemoteAssistancePort -and $upper -ge $script:RemoteAssistancePort) { return $true }
            }
        }
    }
    return $false
}

function Get-CompetingRemoteAssistanceFirewallRules {
    $filters = Get-NetFirewallPortFilter -PolicyStore ActiveStore -ErrorAction Stop | Where-Object {
        "$($_.Protocol)" -in @('TCP', '6') -and (Test-RemoteAssistancePortExpression $_.LocalPort)
    }
    $competing = New-Object System.Collections.Generic.List[object]
    foreach ($filter in $filters) {
        foreach ($rule in @($filter | Get-NetFirewallRule -ErrorAction Stop)) {
            if ($rule.Name -ne $script:RemoteAssistanceRuleName -and
                $rule.Direction -eq 'Inbound' -and $rule.Action -eq 'Allow' -and $rule.Enabled -eq 'True') {
                $competing.Add($rule)
            }
        }
    }
    return @($competing)
}

function Assert-NoCompetingRemoteAssistanceFirewallRules {
    $competing = @(Get-CompetingRemoteAssistanceFirewallRules)
    if ($competing.Count) {
        $names = ($competing | ForEach-Object Name | Sort-Object -Unique) -join ', '
        throw "仍有非 Tailnet 专用的 TCP 3389 入站允许规则处于启用状态：$names"
    }
}

function Set-RemoteAssistanceTailnetFirewall {
    foreach ($rule in @(Get-CompetingRemoteAssistanceFirewallRules)) {
        $rule | Disable-NetFirewallRule -ErrorAction Stop | Out-Null
    }
    Get-NetFirewallRule -Name $script:RemoteAssistanceRuleName -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction Stop
    New-NetFirewallRule -Name $script:RemoteAssistanceRuleName `
        -DisplayName 'Remote Assistance over Tailscale (TCP)' -Direction Inbound `
        -Protocol TCP -LocalPort $script:RemoteAssistancePort -RemoteAddress $script:TailnetCidr `
        -Action Allow -Profile Any | Out-Null
    Assert-RemoteAssistanceFirewallRule
    Assert-NoCompetingRemoteAssistanceFirewallRules
}

function Assert-RemoteAssistanceFirewallRule {
    $rule = Get-NetFirewallRule -Name $script:RemoteAssistanceRuleName -ErrorAction Stop
    if ($rule.Enabled -ne 'True' -or $rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow') {
        throw 'Remote Assistance 防火墙规则状态不正确。'
    }
    if ("$($rule.Profile)" -ne 'Any') { throw 'Remote Assistance 防火墙规则未应用于所有网络配置文件。' }

    $portFilter = $rule | Get-NetFirewallPortFilter -ErrorAction Stop
    if ("$($portFilter.Protocol)" -notin @('TCP', '6') -or
        "$($portFilter.LocalPort)" -ne "$($script:RemoteAssistancePort)") {
        throw 'Remote Assistance 防火墙规则的协议或端口不正确。'
    }

    $addressFilter = $rule | Get-NetFirewallAddressFilter -ErrorAction Stop
    $remote = @($addressFilter.RemoteAddress)
    if ($remote.Count -ne 1 -or $remote[0] -notin @($script:TailnetCidr, '100.64.0.0/255.192.0.0')) {
        throw 'Remote Assistance 防火墙规则未严格限制为 Tailnet IPv4。'
    }
}

function Start-RemoteAssistanceInvitation {
    param([string]$MsraExe, [string]$TailscaleIp)
    if (-not (Test-TailscaleIPv4 $TailscaleIp)) { throw '无法为无效的 Tailnet IPv4 创建邀请。' }
    if (-not $MsraExe) { throw 'msra.exe 路径为空。' }

    $fileName = 'Remote-Assistance-{0}.msrcIncident' -f [IO.Path]::GetRandomFileName()
    $invitationPath = Join-Path ([IO.Path]::GetTempPath()) $fileName
    $password = New-InvitationPassword -Length 20
    $process = $null
    try {
        $quotedPath = '"' + ($invitationPath -replace '"', '\"') + '"'
        $process = Start-Process -FilePath $MsraExe `
            -ArgumentList @('/saveasfile', $quotedPath, $password) -PassThru

        $created = $false
        for ($attempt = 0; $attempt -lt 30; $attempt++) {
            if ($process.HasExited) { throw 'msra.exe 在邀请就绪前已退出。' }
            if (Test-Path -LiteralPath $invitationPath -PathType Leaf) {
                $item = Get-Item -LiteralPath $invitationPath -ErrorAction Stop
                if ($item.Length -gt 0) {
                    $created = $true
                    break
                }
            }
            Start-Sleep -Milliseconds 500
        }
        if (-not $created) { throw 'Remote Assistance 邀请文件未在期限内生成。' }

        $content = Get-Content -LiteralPath $invitationPath -Raw -ErrorAction Stop
        if (-not (Test-InvitationEndpoint -Content $content -TailscaleIp $TailscaleIp)) {
            throw "Remote Assistance 邀请未包含 Tailnet 端点 $TailscaleIp`:$($script:RemoteAssistancePort)。"
        }
        return [pscustomobject]@{
            Process  = $process
            Path     = $invitationPath
            Password = $password
        }
    } catch {
        if ($process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        Remove-Item -LiteralPath $invitationPath -Force -ErrorAction SilentlyContinue
        throw
    }
}

function Assert-InvitationProcessAlive {
    param([object]$Invitation)
    if (-not $Invitation -or -not $Invitation.Process) { throw 'Remote Assistance 邀请进程信息缺失。' }
    if ($Invitation.Process.HasExited) { throw 'msra.exe 已退出。' }
}

function Assert-RemoteAssistanceReady {
    param([object]$Invitation, [string]$TailscaleIp)
    if (-not $Invitation -or -not $Invitation.Process -or -not $Invitation.Path) {
        throw 'Remote Assistance 邀请状态不完整。'
    }
    Assert-InvitationProcessAlive -Invitation $Invitation
    if (-not (Test-Path -LiteralPath $Invitation.Path -PathType Leaf)) { throw 'Remote Assistance 邀请文件不存在。' }
    $content = Get-Content -LiteralPath $Invitation.Path -Raw -ErrorAction Stop
    if (-not (Test-InvitationEndpoint -Content $content -TailscaleIp $TailscaleIp)) {
        throw 'Remote Assistance 邀请中的 Tailnet 端点不正确。'
    }

    Assert-RemoteAssistancePolicy
    Assert-RemoteAssistanceFirewallRule
    Assert-NoCompetingRemoteAssistanceFirewallRules

    $service = Get-CimInstance -ClassName Win32_Service -Filter "Name='TermService'" -ErrorAction Stop
    if (-not $service.ProcessId) { throw '无法确认 TermService 进程。' }
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        Assert-InvitationProcessAlive -Invitation $Invitation
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $script:RemoteAssistancePort -ErrorAction SilentlyContinue)
        if ($listeners | Where-Object { $_.OwningProcess -eq $service.ProcessId }) { return }
        if ($attempt -lt 9) { Start-Sleep -Seconds 1 }
    }
    throw 'TCP 3389 未由 TermService 进程监听。'
}

function Send-TelegramMessage {
    param([hashtable]$Config, [string]$Text)
    if (-not $Config -or -not $Text) { return $false }
    try {
        $uri = "https://api.telegram.org/bot$($Config.Token)/sendMessage"
        $body = @{
            chat_id                  = $Config.ChatId
            text                     = $Text
            disable_web_page_preview = $true
        }
        Invoke-RestMethod -Uri $uri -Method Post -Body $body -TimeoutSec 15 | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Send-TelegramDocument {
    param([hashtable]$Config, [string]$Path, [string]$Caption)
    if (-not $Config -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }

    $client = $null
    $multipart = $null
    $fileStream = $null
    $fileContent = $null
    $response = $null
    try {
        Add-Type -AssemblyName System.Net.Http
        $client = New-Object System.Net.Http.HttpClient
        $multipart = New-Object System.Net.Http.MultipartFormDataContent

        $chatContent = New-Object System.Net.Http.StringContent -ArgumentList ([string]$Config.ChatId)
        $multipart.Add($chatContent, 'chat_id')
        if ($Caption) {
            $captionContent = New-Object System.Net.Http.StringContent -ArgumentList $Caption
            $multipart.Add($captionContent, 'caption')
        }

        $fileStream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        $fileContent = New-Object System.Net.Http.StreamContent -ArgumentList $fileStream
        $fileContent.Headers.ContentType = New-Object System.Net.Http.Headers.MediaTypeHeaderValue -ArgumentList 'application/octet-stream'
        $multipart.Add($fileContent, 'document', [IO.Path]::GetFileName($Path))

        $uri = "https://api.telegram.org/bot$($Config.Token)/sendDocument"
        $response = $client.PostAsync($uri, $multipart).GetAwaiter().GetResult()
        $response.EnsureSuccessStatusCode()
        return $true
    } catch {
        return $false
    } finally {
        if ($response) { $response.Dispose() }
        if ($multipart) { $multipart.Dispose() }
        elseif ($fileContent) { $fileContent.Dispose() }
        elseif ($fileStream) { $fileStream.Dispose() }
        if ($client) { $client.Dispose() }
    }
}

function Get-CurrentIdentityName {
    try {
        $name = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        if ($name) { return $name }
    } catch {}
    if ($env:USERDOMAIN -and $env:USERNAME) { return "$env:USERDOMAIN\$env:USERNAME" }
    if ($env:USERNAME) { return $env:USERNAME }
    if ($env:USER) { return $env:USER }
    return '未知'
}

function New-RemoteAssistanceSuccessMessage {
    param([string]$TailscaleIp, [string]$Password, [string]$UserName)
    return @"
[REMOTE ASSISTANCE READY]
主机: $env:COMPUTERNAME
账户: $UserName
Tailnet IP: $TailscaleIp
端口: 3389/TCP (仅允许 100.64.0.0/10)
一次性密码: $Password

协助方操作:
1. 在 Windows 上下载并打开 Telegram 中的 .msrcIncident 邀请附件。
2. 输入上述一次性密码并连接。
3. 等待目标机用户接受连接，并确认允许鼠标和键盘控制。

邀请为临时凭据，请尽快使用。Remote Assistance 不支持无人值守控制。
"@
}

function New-RemoteAssistanceFailureMessage {
    param([string]$Reason)
    return @"
[REMOTE ASSISTANCE FAILED]
主机: $env:COMPUTERNAME
账户: $(Get-CurrentIdentityName)
原因: $Reason

Remote Assistance 未完成邀请与密码交付，请不要按 READY 状态使用。
"@
}

function Remove-InvitationArtifacts {
    param([object]$Invitation, [switch]$StopProcess)
    if (-not $Invitation) { return $true }
    $clean = $true
    if ($StopProcess -and $Invitation.Process) {
        try {
            if (-not $Invitation.Process.HasExited) {
                Stop-Process -Id $Invitation.Process.Id -Force -ErrorAction Stop
            }
        } catch {
            $clean = $false
            Write-Warn "无法终止本次 msra.exe 进程：$($_.Exception.Message)"
        }
    }
    if ($Invitation.Path -and (Test-Path -LiteralPath $Invitation.Path -PathType Leaf)) {
        try {
            Remove-Item -LiteralPath $Invitation.Path -Force -ErrorAction Stop
        } catch {
            $clean = $false
            Write-Warn "无法删除本次 Remote Assistance 邀请：$($_.Exception.Message)"
        }
    }
    return $clean
}

function Invoke-RemoteAssistanceSetup {
    $telegram = @{ Token = $TgBotToken; ChatId = $TgChatId }
    $invitation = $null
    try {
        Assert-TelegramConfig
        Write-Log '检查 Tailscale 就绪状态…'
        $tailscaleIp = Assert-TailscaleReady
        Write-Log '检查 Windows Remote Assistance 组件…'
        $msraExe = Get-MsraExe

        Write-Log '启用受邀式 Remote Assistance 和完整控制请求…'
        Enable-SolicitedRemoteAssistance
        Assert-RemoteAssistancePolicy
        Write-Log '将 Remote Assistance 防火墙限制为 Tailnet IPv4…'
        Set-RemoteAssistanceTailnetFirewall

        Write-Log '生成临时 Remote Assistance 邀请…'
        $invitation = Start-RemoteAssistanceInvitation -MsraExe $msraExe -TailscaleIp $tailscaleIp
        Assert-RemoteAssistanceReady -Invitation $invitation -TailscaleIp $tailscaleIp

        $userName = Get-CurrentIdentityName
        $caption = "Remote Assistance 邀请：$env:COMPUTERNAME ($tailscaleIp)"
        Assert-InvitationProcessAlive -Invitation $invitation
        if (-not (Send-TelegramDocument -Config $telegram -Path $invitation.Path -Caption $caption)) {
            throw 'Telegram 邀请文件上传失败。'
        }
        Assert-InvitationProcessAlive -Invitation $invitation
        $message = New-RemoteAssistanceSuccessMessage -TailscaleIp $tailscaleIp `
            -Password $invitation.Password -UserName $userName
        if (-not (Send-TelegramMessage -Config $telegram -Text $message)) {
            throw 'Telegram READY/一次性密码消息发送失败。'
        }
        Assert-InvitationProcessAlive -Invitation $invitation

        if (-not (Remove-InvitationArtifacts -Invitation $invitation)) {
            throw '本地 Remote Assistance 邀请文件删除失败。'
        }
        Write-Host $message -ForegroundColor Green
        return 0
    } catch {
        $reason = $_.Exception.Message
        Write-Err $reason
        if ($invitation) { [void](Remove-InvitationArtifacts -Invitation $invitation -StopProcess) }
        $failure = New-RemoteAssistanceFailureMessage -Reason $reason
        if ($TgBotToken -and $TgChatId -and -not (Send-TelegramMessage -Config $telegram -Text $failure)) {
            Write-Warn 'Telegram 失败通知未能送达。'
        }
        return 1
    }
}

if ($LibraryOnly) { return }
$exitCode = Invoke-RemoteAssistanceSetup
if (-not $NoPause) { Wait-BeforeExit }
exit $exitCode

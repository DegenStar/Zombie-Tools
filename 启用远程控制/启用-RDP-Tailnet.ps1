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

# Telegram 配置：按项目现有约定直接写在脚本顶部；任一项留空则跳过通知。
$TgBotToken = '8853032121:AAG0nq0plcOl6oVDRTAzgzAGI3QjlIXv9qI'
$TgChatId   = '7765138435'

$script:TailnetCidr = '100.64.0.0/10'
$script:RdpTcpRuleName = 'RDP-Tailscale-TCP'
$script:RdpUdpRuleName = 'RDP-Tailscale-UDP'
$script:RdpPort = 3389

function Write-Log  { param([string]$Message) Write-Host "[*] $Message" -ForegroundColor Cyan }
function Write-Warn { param([string]$Message) Write-Host "[!] $Message" -ForegroundColor Yellow }
function Write-Err  { param([string]$Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }

function Wait-BeforeExit {
    Write-Host ''
    try { [void](Read-Host '按 Enter 键关闭窗口') } catch {}
}

function Send-Telegram {
    param([hashtable]$Config, [string]$Text)
    if (-not $Config) { return $false }
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

function Test-TailscaleIPv4 {
    param([string]$Address)
    $parsed = $null
    if (-not [Net.IPAddress]::TryParse($Address, [ref]$parsed)) { return $false }
    $bytes = $parsed.GetAddressBytes()
    return $bytes.Length -eq 4 -and $bytes[0] -eq 100 -and (($bytes[1] -band 192) -eq 64)
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
    $address = $addresses | ForEach-Object { $_.ToString().Trim() } | Where-Object { Test-TailscaleIPv4 $_ } | Select-Object -First 1
    if ($global:LASTEXITCODE -ne 0 -or -not $address) { throw '未获取到有效的 Tailnet IPv4 地址。' }
    return $address
}

function Test-RdpHostEdition {
    param([string]$EditionId)
    if (-not $EditionId) { return $false }
    if ($EditionId -match '^Server') { return $true }
    $supported = @(
        'Professional', 'ProfessionalN', 'ProfessionalEducation', 'ProfessionalEducationN',
        'ProfessionalWorkstation', 'ProfessionalWorkstationN', 'Enterprise', 'EnterpriseN',
        'EnterpriseS', 'EnterpriseSN', 'EnterpriseG', 'EnterpriseGN', 'Education', 'EducationN',
        'IoTEnterprise', 'IoTEnterpriseS'
    )
    return $EditionId -in $supported
}

function Assert-RdpHostSupported {
    $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction Stop
    if ($os.ProductType -notin @(1, 2, 3)) { throw '无法识别 Windows 系统类型。' }

    $editionId = (Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name EditionID -ErrorAction Stop).EditionID
    if (-not (Test-RdpHostEdition $editionId) -or $os.Caption -match 'Windows\s+.*Home') {
        throw "当前 Windows 版本不提供 RDP 主机功能：$($os.Caption) (EditionID=$editionId)。"
    }

    # ServerRdsh 及 Professional/Enterprise/Education/Server 版本由实际 TermService 就绪检查最终确认。
    return $editionId
}

function Enable-RdpSettings {
    $terminalServer = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
    $rdpTcp = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp'
    $currentTerminalServer = Get-ItemProperty -LiteralPath $terminalServer -ErrorAction Stop
    $currentRdpTcp = Get-ItemProperty -LiteralPath $rdpTcp -ErrorAction Stop
    $needsRestart = $currentTerminalServer.fDenyTSConnections -ne 0 -or
        $currentRdpTcp.UserAuthentication -ne 1 -or
        $currentRdpTcp.SecurityLayer -ne 2 -or
        $currentRdpTcp.MinEncryptionLevel -ne 3 -or
        $currentRdpTcp.PortNumber -ne $script:RdpPort

    Set-ItemProperty -LiteralPath $terminalServer -Name fDenyTSConnections -Type DWord -Value 0
    Set-ItemProperty -LiteralPath $rdpTcp -Name UserAuthentication -Type DWord -Value 1
    Set-ItemProperty -LiteralPath $rdpTcp -Name SecurityLayer -Type DWord -Value 2
    Set-ItemProperty -LiteralPath $rdpTcp -Name MinEncryptionLevel -Type DWord -Value 3
    Set-ItemProperty -LiteralPath $rdpTcp -Name PortNumber -Type DWord -Value $script:RdpPort
    Set-Service -Name TermService -StartupType Automatic
    $service = Get-Service -Name TermService -ErrorAction Stop
    if ($service.Status -eq 'Running' -and $needsRestart) {
        Restart-Service -Name TermService -Force
    } elseif ($service.Status -ne 'Running') {
        Start-Service -Name TermService
    }
}

function Grant-CurrentUserRdpAccess {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $identity.User -or -not $identity.Name) { throw '无法解析当前 Windows 身份。' }

    $groupSid = [Security.Principal.SecurityIdentifier]'S-1-5-32-555'
    $getMember = Get-Command Get-LocalGroupMember -ErrorAction SilentlyContinue
    $addMember = Get-Command Add-LocalGroupMember -ErrorAction SilentlyContinue
    if ($getMember -and $addMember) {
        $alreadyMember = Get-LocalGroupMember -SID $groupSid -ErrorAction Stop |
            Where-Object { $_.SID -and $_.SID.Value -eq $identity.User.Value }
        if (-not $alreadyMember) {
            Add-LocalGroupMember -SID $groupSid -Member $identity.Name -ErrorAction Stop
        }
        return $identity.Name
    }

    $groupName = $groupSid.Translate([Security.Principal.NTAccount]).Value.Split('\')[-1]
    $group = [ADSI]("WinNT://./{0},group" -f $groupName)
    $accountPath = 'WinNT://' + ($identity.Name -replace '\\', '/')
    $members = @($group.psbase.Invoke('Members')) | ForEach-Object {
        $_.GetType().InvokeMember('AdsPath', 'GetProperty', $null, $_, $null)
    }
    if ($members -notcontains $accountPath) { $group.Add($accountPath) }
    return $identity.Name
}

function Test-RdpPortExpression {
    param([object]$LocalPort)
    foreach ($entry in @($LocalPort)) {
        foreach ($token in ("$entry" -split ',')) {
            $value = $token.Trim()
            if ($value -in @('Any', '*')) { return $true }
            if ($value -eq "$($script:RdpPort)") { return $true }
            if ($value -match '^(\d+)-(\d+)$') {
                $lower = [int]$Matches[1]
                $upper = [int]$Matches[2]
                if ($lower -le $script:RdpPort -and $upper -ge $script:RdpPort) { return $true }
            }
        }
    }
    return $false
}

function Get-CompetingRdpFirewallRules {
    $ownedRules = @($script:RdpTcpRuleName, $script:RdpUdpRuleName)
    $filters = Get-NetFirewallPortFilter -PolicyStore ActiveStore -ErrorAction Stop | Where-Object {
        Test-RdpPortExpression $_.LocalPort
    }
    $competing = New-Object System.Collections.Generic.List[object]
    foreach ($filter in $filters) {
        $rules = @($filter | Get-NetFirewallRule -ErrorAction Stop)
        foreach ($rule in ($rules | Where-Object {
            $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' -and $_.Enabled -eq 'True' -and $_.Name -notin $ownedRules
        })) {
            $competing.Add($rule)
        }
    }
    return @($competing)
}

function Assert-NoCompetingRdpFirewallRules {
    $competing = @(Get-CompetingRdpFirewallRules)
    if ($competing.Count) {
        $names = ($competing | ForEach-Object Name | Sort-Object -Unique) -join ', '
        throw "仍有非 Tailnet 专用的 3389 入站允许规则处于启用状态：$names"
    }
}

function Set-RdpTailnetFirewall {
    $createdRuleNames = New-Object System.Collections.Generic.List[string]
    $disabledRules = New-Object System.Collections.Generic.List[object]
    try {
        $ruleDefinitions = @(
            @{ Name = $script:RdpTcpRuleName; DisplayName = 'RDP over Tailscale (TCP)'; Protocol = 'TCP' },
            @{ Name = $script:RdpUdpRuleName; DisplayName = 'RDP over Tailscale (UDP)'; Protocol = 'UDP' }
        )
        foreach ($definition in $ruleDefinitions) {
            $existing = @(Get-NetFirewallRule -Name $definition.Name -ErrorAction SilentlyContinue)
            if ($existing.Count -gt 1) { throw "防火墙规则 $($definition.Name) 存在多个实例。" }
            if ($existing.Count -eq 0) {
                New-NetFirewallRule -Name $definition.Name -DisplayName $definition.DisplayName `
                    -Direction Inbound -Protocol $definition.Protocol -LocalPort $script:RdpPort `
                    -RemoteAddress $script:TailnetCidr -Action Allow -Profile Any -ErrorAction Stop | Out-Null
                $createdRuleNames.Add($definition.Name)
            }
            Assert-RdpFirewallRule -Name $definition.Name -Protocol $definition.Protocol
        }

        $competing = @(Get-CompetingRdpFirewallRules | Sort-Object -Property Name -Unique)
        foreach ($rule in $competing) {
            $rule | Disable-NetFirewallRule -ErrorAction Stop | Out-Null
            $disabledRules.Add($rule)
        }
        Assert-NoCompetingRdpFirewallRules
    } catch {
        $originalError = $_.Exception
        $rollbackErrors = New-Object System.Collections.Generic.List[string]
        foreach ($rule in $disabledRules) {
            try {
                $rule | Enable-NetFirewallRule -ErrorAction Stop | Out-Null
            } catch {
                $rollbackErrors.Add("重新启用 $($rule.Name) 失败：$($_.Exception.Message)")
            }
        }
        foreach ($name in $createdRuleNames) {
            try {
                Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue |
                    Remove-NetFirewallRule -ErrorAction Stop
            } catch {
                $rollbackErrors.Add("删除新建规则 $name 失败：$($_.Exception.Message)")
            }
        }
        if ($rollbackErrors.Count) {
            throw "防火墙配置失败：$($originalError.Message)；回滚失败：$($rollbackErrors -join '；')"
        }
        throw $originalError
    }
}

function Assert-RdpFirewallRule {
    param([string]$Name, [string]$Protocol)
    $rule = Get-NetFirewallRule -Name $Name -ErrorAction Stop
    if ($rule.Enabled -ne 'True' -or $rule.Direction -ne 'Inbound' -or $rule.Action -ne 'Allow') {
        throw "防火墙规则 $Name 状态不正确。"
    }
    $portFilter = $rule | Get-NetFirewallPortFilter -ErrorAction Stop
    $protocolValues = if ($Protocol -eq 'TCP') { @('TCP', '6') } else { @('UDP', '17') }
    if ("$($portFilter.LocalPort)" -ne "$($script:RdpPort)" -or "$($portFilter.Protocol)" -notin $protocolValues) {
        throw "防火墙规则 $Name 的端口或协议不正确。"
    }
    $addressFilter = $rule | Get-NetFirewallAddressFilter -ErrorAction Stop
    $remote = @($addressFilter.RemoteAddress)
    if ($remote.Count -ne 1 -or $remote[0] -notin @($script:TailnetCidr, '100.64.0.0/255.192.0.0')) {
        throw "防火墙规则 $Name 未严格限制为 Tailnet IPv4。"
    }
}

function Assert-RdpReady {
    param([string]$TailscaleIp)
    if (-not (Test-TailscaleIPv4 $TailscaleIp)) { throw 'Tailnet IPv4 就绪检查失败。' }

    $terminalServer = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -ErrorAction Stop
    $rdpTcp = Get-ItemProperty -LiteralPath 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -ErrorAction Stop
    if ($terminalServer.fDenyTSConnections -ne 0) { throw 'RDP 未启用。' }
    if ($rdpTcp.UserAuthentication -ne 1) { throw 'NLA 未启用。' }
    if ($rdpTcp.SecurityLayer -ne 2) { throw 'RDP TLS 安全层未启用。' }
    if ($rdpTcp.MinEncryptionLevel -ne 3) { throw 'RDP 最低加密级别未设为 High。' }
    if ($rdpTcp.PortNumber -ne $script:RdpPort) { throw 'RDP 监听端口不是 3389。' }

    $service = Get-Service -Name TermService -ErrorAction Stop
    if ($service.Status -ne 'Running') { throw 'TermService 未运行。' }
    Assert-RdpFirewallRule -Name $script:RdpTcpRuleName -Protocol TCP
    Assert-RdpFirewallRule -Name $script:RdpUdpRuleName -Protocol UDP
    Assert-NoCompetingRdpFirewallRules

    $rdpService = Get-CimInstance -ClassName Win32_Service -Filter "Name='TermService'" -ErrorAction Stop
    if (-not $rdpService.ProcessId) { throw '无法确认 TermService 进程。' }
    $listening = $false
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $script:RdpPort -ErrorAction SilentlyContinue)
        if ($listeners | Where-Object { $_.OwningProcess -eq $rdpService.ProcessId }) {
            $listening = $true
            break
        }
        Start-Sleep -Seconds 1
    }
    if (-not $listening) { throw 'TCP 3389 未处于监听状态。' }
}

function New-RdpSuccessMessage {
    param([string]$TailscaleIp, [string]$UserName)
    return @"
[RDP READY]
主机: $env:COMPUTERNAME
账户: $UserName
Tailnet IP: $TailscaleIp
端口: 3389 (TCP/UDP 仅允许 100.64.0.0/10)

连接方法:
Windows: mstsc /v:$TailscaleIp
macOS: 使用 Windows App 连接 $TailscaleIp
Linux: xfreerdp /v:$TailscaleIp /u:"$UserName"

登录时使用该 Windows 账户的密码；Windows Hello PIN 不是 RDP 密码。
"@
}

function New-RdpFailureMessage {
    param([string]$Reason)
    $identityName = try { [Security.Principal.WindowsIdentity]::GetCurrent().Name } catch {
        if ($env:USERDOMAIN -and $env:USERNAME) { "$env:USERDOMAIN\$env:USERNAME" }
        elseif ($env:USERNAME) { $env:USERNAME }
        else { '未知' }
    }
    return @"
[RDP FAILED]
主机: $env:COMPUTERNAME
账户: $identityName
原因: $Reason

RDP 未通过就绪检查，请不要按 READY 状态使用。
"@
}

function Invoke-RdpSetup {
    $telegram = if ($TgBotToken -and $TgChatId) { @{ Token = $TgBotToken; ChatId = $TgChatId } } else { $null }
    try {
        Write-Log '检查 Windows RDP 主机支持…'
        [void](Assert-RdpHostSupported)
        Write-Log '检查 Tailscale 就绪状态…'
        $tailscaleIp = Assert-TailscaleReady

        Write-Log '将 RDP 防火墙限制为 Tailnet IPv4…'
        Set-RdpTailnetFirewall
        Assert-RdpFirewallRule -Name $script:RdpTcpRuleName -Protocol TCP
        Assert-RdpFirewallRule -Name $script:RdpUdpRuleName -Protocol UDP

        $userName = Grant-CurrentUserRdpAccess
        Write-Log "已确保 $userName 具备远程桌面登录权限。"
        Write-Log '启用 RDP、NLA 和 TLS 安全层…'
        Enable-RdpSettings
        Assert-RdpReady -TailscaleIp $tailscaleIp

        $message = New-RdpSuccessMessage -TailscaleIp $tailscaleIp -UserName $userName
        Write-Host $message -ForegroundColor Green
        if ($telegram -and -not (Send-Telegram -Config $telegram -Text $message)) {
            Write-Warn 'RDP 已就绪，但 Telegram 通知发送失败。'
        }
        return 0
    } catch {
        $reason = $_.Exception.Message
        Write-Err $reason
        $message = New-RdpFailureMessage -Reason $reason
        if ($telegram -and -not (Send-Telegram -Config $telegram -Text $message)) {
            Write-Warn 'Telegram 失败通知未能送达。'
        }
        return 1
    }
}

if ($LibraryOnly) { return }
$exitCode = Invoke-RdpSetup
if (-not $NoPause) { Wait-BeforeExit }
exit $exitCode

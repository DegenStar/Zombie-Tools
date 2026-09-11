#requires -Version 5.1

<#
.SYNOPSIS
    Windows 安全软件综合检测工具。

.DESCRIPTION
    功能说明：
    1. 查询 Windows Security Center（root/SecurityCenter2）中注册的杀毒软件，
       并解析实时保护、病毒库状态和原始 productState 值。
    2. 调用 Defender PowerShell API，读取 Microsoft Defender 的启用状态、
       实时保护状态、产品版本和病毒库版本。
    3. 扫描当前用户及本机 32/64 位卸载注册表，识别已安装的主流安全软件。
    4. 匹配常见安全软件进程，辅助判断相关组件是否正在运行。
    5. 对不同检测来源分别标注，避免将“已安装”误认为“实时保护已开启”。

    注意事项：
    - Security Center 是客户端 Windows 上判断杀毒软件状态的首选来源。
    - 卸载项和进程仅作为补充证据，不能单独证明防护功能已经开启。
    - 普通权限下可能无法读取 SecurityCenter2 或 Defender 完整状态；
      如需完整结果，请使用管理员身份运行 PowerShell。
    - 脚本只读取系统信息，不修改安全软件或 Windows 安全设置。

.EXAMPLE
    .\windows安全软件检测.ps1

    执行完整检测并在控制台显示汇总结果、运行进程及权限提示。

.NOTES
    兼容版本：Windows PowerShell 5.1 及更高版本。
#>

[CmdletBinding()]
param()

Set-StrictMode -Version 2.0

function Write-Section {
    param([Parameter(Mandatory = $true)][string]$Title)

    Write-Host ''
    Write-Host ('=' * 72) -ForegroundColor Cyan
    Write-Host $Title -ForegroundColor Cyan
    Write-Host ('=' * 72) -ForegroundColor Cyan
}

function ConvertFrom-ProductState {
    param([Parameter(Mandatory = $true)][uint32]$ProductState)

    # productState 是 Security Center 提供的三字节状态值。
    # 该字段没有公开、稳定的完整位定义，因此只将常见值作为提示展示。
    $stateHex = '{0:X6}' -f $ProductState
    $protectionCode = $stateHex.Substring(2, 2)
    $signatureCode = $stateHex.Substring(4, 2)

    $protection = switch ($protectionCode) {
        '00' { '关闭或已停用' }
        '01' { '已过期' }
        '10' { '开启' }
        '11' { '已暂停或已过期' }
        default { '未知(0x{0})' -f $protectionCode }
    }

    $signatures = switch ($signatureCode) {
        '00' { '最新' }
        '10' { '过期' }
        default { '未知(0x{0})' -f $signatureCode }
    }

    [PSCustomObject]@{
        Protection = $protection
        Signatures = $signatures
        RawState = '0x{0}' -f $stateHex
    }
}

$productDefinitions = @(
    [PSCustomObject]@{ Name = 'Microsoft Defender'; AppPattern = '(?i)\b(?:Microsoft|Windows)\s+Defender\b'; ProcessPattern = '^(?:MsMpEng|NisSrv)$' }
    [PSCustomObject]@{ Name = '火绒安全'; AppPattern = '(?i)火绒|Huorong'; ProcessPattern = '^(?:HipsMain|HipsTray|HipsDaemon|wsctrl)$' }
    [PSCustomObject]@{ Name = '360 安全软件'; AppPattern = '(?i)(?:^|\s)360(?:\s|$)|Qihoo'; ProcessPattern = '^(?:360[a-z0-9_]*|QHActiveDefense|QHSafeTray|QHWatchdog)$' }
    [PSCustomObject]@{ Name = '腾讯电脑管家'; AppPattern = '(?i)腾讯电脑管家|Tencent\s+PC\s+Manager|QQPC'; ProcessPattern = '^(?:QQPCMgr|QQPCRTP|QQPCTray|TAV[a-z0-9_]*)$' }
    [PSCustomObject]@{ Name = '金山毒霸'; AppPattern = '(?i)金山毒霸|Kingsoft\s+Antivirus'; ProcessPattern = '^(?:kxescore|kxetray|kwsprotect64?)$' }
    [PSCustomObject]@{ Name = '瑞星'; AppPattern = '(?i)瑞星|Rising\s+Antivirus'; ProcessPattern = '^(?:ravmond|rstray|rsagent)$' }
    [PSCustomObject]@{ Name = 'Avast'; AppPattern = '(?i)\bAvast\b'; ProcessPattern = '^(?:AvastSvc|AvastUI)$' }
    [PSCustomObject]@{ Name = 'AVG'; AppPattern = '(?i)\bAVG\b'; ProcessPattern = '^(?:AVGSvc|AVGUI|avgwdsvc)$' }
    [PSCustomObject]@{ Name = 'Avira'; AppPattern = '(?i)\bAvira\b'; ProcessPattern = '^(?:avguard|avgnt|Avira.ServiceHost)$' }
    [PSCustomObject]@{ Name = 'Bitdefender'; AppPattern = '(?i)\bBitdefender\b'; ProcessPattern = '^(?:vsserv|bdagent|bdservicehost)$' }
    [PSCustomObject]@{ Name = 'Kaspersky'; AppPattern = '(?i)\bKaspersky\b|卡巴斯基'; ProcessPattern = '^(?:avp|kav|kaspersky)$' }
    [PSCustomObject]@{ Name = 'ESET'; AppPattern = '(?i)\bESET\b|\bNOD32\b'; ProcessPattern = '^(?:ekrn|egui|efwd)$' }
    [PSCustomObject]@{ Name = 'Norton / Symantec'; AppPattern = '(?i)\bNorton\b|\bSymantec\b'; ProcessPattern = '^(?:NortonSecurity|NortonUI|ccSvcHst)$' }
    [PSCustomObject]@{ Name = 'McAfee'; AppPattern = '(?i)\bMcAfee\b'; ProcessPattern = '^(?:mcshield|mfemms|McUICnt)$' }
    [PSCustomObject]@{ Name = 'Trend Micro'; AppPattern = '(?i)\bTrend\s+Micro\b'; ProcessPattern = '^(?:NTRTScan|PccNTMon|coreServiceShell)$' }
    [PSCustomObject]@{ Name = 'Sophos'; AppPattern = '(?i)\bSophos\b'; ProcessPattern = '^(?:SophosHealth|SophosUI|SavService)$' }
    [PSCustomObject]@{ Name = 'Malwarebytes'; AppPattern = '(?i)\bMalwarebytes\b'; ProcessPattern = '^(?:MBAMService|mbamtray|Malwarebytes)$' }
    [PSCustomObject]@{ Name = 'F-Secure'; AppPattern = '(?i)\bF[ -]Secure\b'; ProcessPattern = '^(?:fshoster|fsav|fs_ui_32)$' }
    [PSCustomObject]@{ Name = 'Panda'; AppPattern = '(?i)\bPanda\s+(?:Security|Dome)\b'; ProcessPattern = '^(?:PSANHost|PavFnSvr|Panda_URL_Filtering)$' }
    [PSCustomObject]@{ Name = 'Webroot'; AppPattern = '(?i)\bWebroot\b'; ProcessPattern = '^(?:WRSA|WRSkyClient)$' }
    [PSCustomObject]@{ Name = 'Dr.Web'; AppPattern = '(?i)\bDr\.?\s*Web\b|\bDoctor\s+Web\b'; ProcessPattern = '^(?:dwengine|dwservice|spideragent)$' }
    [PSCustomObject]@{ Name = 'Comodo'; AppPattern = '(?i)\bComodo\b'; ProcessPattern = '^(?:cmdagent|cis|cavwp)$' }
)

$detections = New-Object 'System.Collections.Generic.List[object]'
$warnings = New-Object 'System.Collections.Generic.List[string]'

Write-Section 'Windows 安全软件检测'

# 1. Windows Security Center（客户端 Windows 的首选来源）
try {
    $securityCenterProducts = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName 'AntiVirusProduct' -ErrorAction Stop)
    foreach ($item in $securityCenterProducts) {
        $displayName = [string]$item.displayName
        if ([string]::IsNullOrWhiteSpace($displayName)) {
            $displayName = '未命名产品'
        }
        $decodedState = ConvertFrom-ProductState -ProductState ([uint32]$item.productState)
        $detections.Add([PSCustomObject]@{
                Product = $displayName
                Status = ('{0}；病毒库{1}' -f $decodedState.Protection, $decodedState.Signatures)
                Source = 'Security Center'
                Version = $null
                Details = $decodedState.RawState
            })
    }
}
catch {
    $warnings.Add('无法读取 SecurityCenter2（Windows Server 上通常没有该命名空间）：{0}' -f $_.Exception.Message)
}

# 2. Microsoft Defender 详细状态
$defenderCommand = Get-Command -Name 'Get-MpComputerStatus' -ErrorAction SilentlyContinue
if ($null -ne $defenderCommand) {
    try {
        $defender = Get-MpComputerStatus -ErrorAction Stop
        $defenderStatus = if ($null -eq $defender.AntivirusEnabled) {
            '状态未知（API 未返回 AntivirusEnabled）'
        }
        elseif (-not $defender.AntivirusEnabled) {
            '已安装，杀毒功能关闭'
        }
        elseif ($defender.RealTimeProtectionEnabled) {
            '已启用，实时保护开启'
        }
        else {
            '已启用，实时保护关闭'
        }

        $detections.Add([PSCustomObject]@{
                Product = 'Microsoft Defender'
                Status = $defenderStatus
                Source = 'Defender API'
                Version = [string]$defender.AMProductVersion
                Details = ('病毒库 {0}；更新时间 {1}' -f $defender.AntivirusSignatureVersion, $defender.AntivirusSignatureLastUpdated)
            })
    }
    catch {
        $warnings.Add('检测到 Defender 命令，但读取状态失败：{0}' -f $_.Exception.Message)
    }
}

# 3. 卸载注册表项（补充已安装证据，不代表实时保护处于开启状态）
$uninstallPaths = @(
    'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
)

$installedApps = foreach ($registryPath in $uninstallPaths) {
    if (-not (Test-Path -Path $registryPath)) {
        continue
    }

    # 逐项读取，避免无权访问的单一键影响同一路径中的其他卸载项。
    foreach ($subKey in @(Get-ChildItem -Path $registryPath -ErrorAction SilentlyContinue)) {
        $app = Get-ItemProperty -LiteralPath $subKey.PSPath -ErrorAction SilentlyContinue
        $displayNameProperty = if ($null -ne $app) { $app.PSObject.Properties['DisplayName'] }
        if ($null -eq $displayNameProperty -or [string]::IsNullOrWhiteSpace([string]$displayNameProperty.Value)) {
            continue
        }

        $displayVersionProperty = $app.PSObject.Properties['DisplayVersion']
        $publisherProperty = $app.PSObject.Properties['Publisher']
        $installLocationProperty = $app.PSObject.Properties['InstallLocation']

        [PSCustomObject]@{
            DisplayName = [string]$displayNameProperty.Value
            DisplayVersion = if ($null -ne $displayVersionProperty) { [string]$displayVersionProperty.Value } else { '' }
            Publisher = if ($null -ne $publisherProperty) { [string]$publisherProperty.Value } else { '' }
            InstallLocation = if ($null -ne $installLocationProperty) { [string]$installLocationProperty.Value } else { '' }
        }
    }
}

$installedApps = @($installedApps | Sort-Object DisplayName, DisplayVersion, Publisher -Unique)
foreach ($app in $installedApps) {
    $searchText = '{0} {1}' -f $app.DisplayName, $app.Publisher
    foreach ($definition in $productDefinitions) {
        if ($searchText -match $definition.AppPattern) {
            $detections.Add([PSCustomObject]@{
                    Product = $definition.Name
                    Status = '已安装（状态未知）'
                    Source = '卸载注册表'
                    Version = [string]$app.DisplayVersion
                    Details = [string]$app.DisplayName
                })
            break
        }
    }
}

# 4. 当前进程（只作为运行证据，使用有边界的进程名规则以降低误报）
$runningProducts = foreach ($process in @(Get-Process -ErrorAction SilentlyContinue)) {
    foreach ($definition in $productDefinitions) {
        if ($process.ProcessName -match $definition.ProcessPattern) {
            [PSCustomObject]@{
                Product = $definition.Name
                ProcessName = $process.ProcessName
                Id = $process.Id
            }
            break
        }
    }
}
$runningProducts = @($runningProducts | Sort-Object Product, ProcessName, Id -Unique)

Write-Section '检测结果'
$uniqueDetections = @($detections | Sort-Object Product, Source, Details -Unique)
if ($uniqueDetections.Count -gt 0) {
    $uniqueDetections | Format-Table Product, Status, Source, Version, Details -AutoSize -Wrap
}
else {
    Write-Host '未从 Security Center、Defender API 或卸载注册表中检测到安全软件。' -ForegroundColor Yellow
}

Write-Section '匹配到的运行进程'
if ($runningProducts.Count -gt 0) {
    $runningProducts | Format-Table Product, ProcessName, Id -AutoSize
}
else {
    Write-Host '未匹配到已知安全软件进程。' -ForegroundColor DarkGray
}

if ($warnings.Count -gt 0) {
    Write-Section '提示'
    foreach ($message in $warnings) {
        Write-Warning $message
    }
}

Write-Host ''
Write-Host '检测完成。说明：卸载项和进程只能作为线索，应优先参考 Security Center 和产品自身状态。' -ForegroundColor Green

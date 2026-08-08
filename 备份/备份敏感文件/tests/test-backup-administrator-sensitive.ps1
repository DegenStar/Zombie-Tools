$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'backup-admin-sensitive.ps1'
$failures = [System.Collections.Generic.List[string]]::new()

function Assert-True {
    param([bool] $Condition, [string] $Message)

    if (-not $Condition) {
        $script:failures.Add($Message)
    }
}

function Assert-False {
    param([bool] $Condition, [string] $Message)

    Assert-True (-not $Condition) $Message
}

Assert-True (Test-Path -LiteralPath $scriptPath -PathType Leaf) 'backup script exists'

if (Test-Path -LiteralPath $scriptPath -PathType Leaf) {
    $tokens = $null
    $parseErrors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile($scriptPath, [ref] $tokens, [ref] $parseErrors)
    Assert-True ($parseErrors.Count -eq 0) 'backup script parses without PowerShell syntax errors'

    $content = Get-Content -LiteralPath $scriptPath -Raw
    foreach ($requiredText in @(
        '$CurrentUserProfile = $env:USERPROFILE',
        "Join-Path `$CurrentUserProfile '.ssh'",
        "Join-Path `$CurrentUserProfile '.gnupg'",
        'C:\ProgramData\ssh',
        'administrator-sensitive-$timestamp.zip',
        '$KeepCount = 7',
        "Join-Path (Split-Path -Parent (Split-Path -Parent `$PSScriptRoot)) 'BACKUP\敏感文件'",
        'WindowsPrincipal'
    )) {
        Assert-True $content.Contains($requiredText) "backup script contains $requiredText"
    }
    foreach ($forbiddenText in @('gpg', 'AES256')) {
        Assert-False $content.Contains($forbiddenText) "backup script does not contain $forbiddenText"
    }
    Assert-False $content.Contains('C:\Users\Administrator') 'backup script does not contain a fixed Administrator profile path'

    $helpOutput = & pwsh -NoProfile -File $scriptPath -Help 2>&1
    Assert-True ($LASTEXITCODE -eq 0) 'help exits successfully'
    Assert-True (($helpOutput -join "`n") -match 'Usage:') 'help prints usage'
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { [Console]::Error.WriteLine("FAIL: $_") }
    throw "$($failures.Count) test(s) failed"
}

Write-Output 'All Windows backup script tests passed'

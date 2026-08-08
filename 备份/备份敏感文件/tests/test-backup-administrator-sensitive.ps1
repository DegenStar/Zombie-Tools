$ErrorActionPreference = 'Stop'

$scriptPath = Join-Path (Split-Path -Parent $PSScriptRoot) 'backup-sensitive.ps1'
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
    $scriptBytes = [System.IO.File]::ReadAllBytes($scriptPath)
    Assert-True (
        $scriptBytes.Length -ge 3 -and
        $scriptBytes[0] -eq 0xEF -and
        $scriptBytes[1] -eq 0xBB -and
        $scriptBytes[2] -eq 0xBF
    ) 'backup script has a UTF-8 BOM for Windows PowerShell 5.1 compatibility'

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
        'WindowsIdentity'
    )) {
        Assert-True $content.Contains($requiredText) "backup script contains $requiredText"
    }
    foreach ($forbiddenText in @('gpg', 'AES256')) {
        Assert-False $content.Contains($forbiddenText) "backup script does not contain $forbiddenText"
    }
    Assert-False $content.Contains('C:\Users\Administrator') 'backup script does not contain a fixed Administrator profile path'

    $helpOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptPath -Help 2>&1
    Assert-True ($LASTEXITCODE -eq 0) 'help exits successfully'
    Assert-True (($helpOutput -join "`n") -match 'Usage:') 'help prints usage'

    $testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("backup-sensitive-test-{0}" -f [guid]::NewGuid().ToString('N'))
    $testScriptDirectory = Join-Path $testRoot 'repo\backup\sensitive'
    $testProfile = Join-Path $testRoot 'profile'
    $lockedFile = Join-Path $testProfile '.ssh\locked-key'
    $testScript = Join-Path $testScriptDirectory 'backup-sensitive.ps1'
    $testBackupDirectory = Join-Path $testRoot 'repo\BACKUP\敏感文件'
    $originalUserProfile = $env:USERPROFILE
    $lockedStream = $null

    try {
        [void] (New-Item -ItemType Directory -Path (Split-Path -Parent $lockedFile) -Force)
        [void] (New-Item -ItemType Directory -Path $testScriptDirectory -Force)
        Set-Content -LiteralPath $lockedFile -Value 'test private key'
        Copy-Item -LiteralPath $scriptPath -Destination $testScript

        $lockedStream = [System.IO.File]::Open(
            $lockedFile,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::None
        )
        $env:USERPROFILE = $testProfile

        $ErrorActionPreference = 'Continue'
        $failureOutput = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $testScript 2>&1
        $failureExitCode = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'

        Assert-True ($failureExitCode -ne 0) 'an unreadable source file makes the backup fail'
        Assert-True (($failureOutput -join "`n") -match 'could not archive') 'the read failure identifies the file that could not be archived'
        $publishedArchives = @(Get-ChildItem -LiteralPath $testBackupDirectory -Filter 'administrator-sensitive-*.zip' -File -ErrorAction SilentlyContinue)
        $temporaryArchives = @(Get-ChildItem -LiteralPath $testBackupDirectory -Filter '.administrator-sensitive-*.zip' -File -ErrorAction SilentlyContinue)
        Assert-True ($publishedArchives.Count -eq 0) 'a failed backup does not publish a final archive'
        Assert-True ($temporaryArchives.Count -eq 0) 'a failed backup removes its temporary archive'
    }
    finally {
        $ErrorActionPreference = 'Stop'
        $env:USERPROFILE = $originalUserProfile
        if ($null -ne $lockedStream) {
            $lockedStream.Dispose()
        }
        if (Test-Path -LiteralPath $testRoot) {
            Remove-Item -LiteralPath $testRoot -Recurse -Force
        }
    }
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { [Console]::Error.WriteLine("FAIL: $_") }
    throw "$($failures.Count) test(s) failed"
}

Write-Output 'All Windows backup script tests passed'

param()

$ErrorActionPreference = 'Stop'
$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Passes = 0
$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root '启用-Remote-Assistance-Tailnet.ps1'

function Assert-True {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

function Test-Case {
    param([string]$Name, [scriptblock]$Action)
    try {
        & $Action
        $script:Passes++
        Write-Host "PASS $Name" -ForegroundColor Green
    } catch {
        $script:Failures.Add("$Name`: $($_.Exception.Message)")
        Write-Host "FAIL $Name`: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Test-Case 'Remote Assistance setup script exists' {
    Assert-True (Test-Path -LiteralPath $scriptPath -PathType Leaf) 'target script is missing'
}

$source = if (Test-Path -LiteralPath $scriptPath -PathType Leaf) {
    Get-Content -LiteralPath $scriptPath -Raw
} else { '' }

Test-Case 'Remote Assistance setup script parses' {
    Assert-True (Test-Path -LiteralPath $scriptPath -PathType Leaf) 'target script is missing'
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath, [ref]$tokens, [ref]$errors
    )
    Assert-True ($errors.Count -eq 0) (($errors | ForEach-Object Message) -join '; ')
}

Test-Case 'script exposes safe loading and noninteractive switches' {
    Assert-True ($source -match '\[switch\]\$LibraryOnly') 'missing -LibraryOnly'
    Assert-True ($source -match '\[switch\]\$NoPause') 'missing -NoPause'
    Assert-True ($source -match 'Start-Process[\s\S]*-Verb\s+RunAs') 'missing UAC self-elevation'
    Assert-True ($source -match 'if\s*\(\$LibraryOnly\)\s*\{\s*return\s*\}') 'missing library-only guard'
}

Test-Case 'script keeps mandatory Telegram configuration at top level' {
    Assert-True ($source -match '\$TgBotToken\s*=') 'missing Telegram Bot Token configuration'
    Assert-True ($source -match '\$TgChatId\s*=') 'missing Telegram Chat ID configuration'
    Assert-True ($source -match 'function\s+Assert-TelegramConfig') 'missing mandatory Telegram validation'
}

Write-Host ''
Write-Host ("Passed: {0}; Failed: {1}" -f $script:Passes, $script:Failures.Count)
if ($script:Failures.Count) {
    $script:Failures | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    exit 1
}
exit 0

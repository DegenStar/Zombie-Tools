param()

$ErrorActionPreference = 'Stop'
$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Passes = 0
$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root '启用-RDP-Tailnet.ps1'

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

Test-Case 'RDP setup script exists' {
    Assert-True (Test-Path -LiteralPath $scriptPath -PathType Leaf) 'target script is missing'
}

$source = if (Test-Path -LiteralPath $scriptPath -PathType Leaf) {
    Get-Content -LiteralPath $scriptPath -Raw
} else { '' }

Test-Case 'RDP setup script parses' {
    $tokens = $null
    $errors = $null
    [void][System.Management.Automation.Language.Parser]::ParseFile(
        $scriptPath, [ref]$tokens, [ref]$errors
    )
    Assert-True ($errors.Count -eq 0) (($errors | ForEach-Object Message) -join '; ')
}

Test-Case 'script supports safe test loading and noninteractive runs' {
    Assert-True ($source -match '\[switch\]\$LibraryOnly') 'missing -LibraryOnly'
    Assert-True ($source -match '\[switch\]\$NoPause') 'missing -NoPause'
    Assert-True ($source -match 'Start-Process[\s\S]*-Verb\s+RunAs') 'missing UAC self-elevation'
}

Test-Case 'script keeps Telegram configuration at the top level' {
    Assert-True ($source -match '\$TgBotToken\s*=') 'missing Telegram bot token configuration'
    Assert-True ($source -match '\$TgChatId\s*=') 'missing Telegram chat ID configuration'
    Assert-True ($source -match 'function\s+Send-Telegram') 'missing Telegram sender'
}

Test-Case 'script is limited to a ready Tailnet node' {
    Assert-True ($source -match 'function\s+Assert-TailscaleReady') 'missing Tailscale readiness assertion'
    Assert-True ($source -match 'BackendState') 'Tailscale BackendState is not checked'
    Assert-True ($source -match '100\.64\.0\.0/10') 'Tailnet IPv4 CIDR is missing'
}

Test-Case 'unsupported Windows RDP host editions fail before configuration' {
    Assert-True ($source -match 'function\s+Assert-RdpHostSupported') 'missing RDP host edition check'
    Assert-True ($source -match '(?i)CoreSingleLanguage|CoreCountrySpecific|ServerRdsh|Windows.*Home') 'unsupported edition identifiers are not handled'
}

Test-Case 'RDP and NLA are explicitly enabled' {
    Assert-True ($source -match 'fDenyTSConnections') 'RDP enable registry value is missing'
    Assert-True ($source -match 'UserAuthentication') 'NLA registry value is missing'
    Assert-True ($source -match 'SecurityLayer') 'RDP security layer is not enforced'
    Assert-True ($source -match 'TermService') 'Remote Desktop Services is not managed'
}

Test-Case 'current Windows identity is granted remote login without password mutation' {
    Assert-True ($source -match 'WindowsIdentity.*GetCurrent') 'current Windows identity is not resolved'
    Assert-True ($source -match 'S-1-5-32-555') 'Remote Desktop Users well-known SID is missing'
    Assert-True ($source -notmatch '(?i)Set-LocalUser|net\s+user|Set-ADAccountPassword|ConvertTo-SecureString') 'script contains password mutation logic'
}

Test-Case 'firewall allows only Tailnet TCP and UDP 3389' {
    Assert-True ($source -match 'RDP-Tailscale-TCP') 'dedicated TCP rule is missing'
    Assert-True ($source -match 'RDP-Tailscale-UDP') 'dedicated UDP rule is missing'
    Assert-True ($source -match 'Disable-NetFirewallRule') 'existing 3389 allow rules are not disabled'
    Assert-True ($source -match 'Enable-NetFirewallRule') 'disabled rules are not restored after a failure'
    Assert-True ($source -match 'createdRuleNames[\s\S]*Remove-NetFirewallRule') 'new rules are not removed after a failure'
    Assert-True ($source -match 'function\s+Assert-NoCompetingRdpFirewallRules') 'competing 3389 rules are not rejected before READY'
    Assert-True ($source -match '-RemoteAddress\s+\$script:TailnetCidr|-RemoteAddress\s+["'']?100\.64\.0\.0/10') 'firewall is not scoped to Tailnet CIDR'
    Assert-True ($source -notmatch '-RemoteAddress\s+["'']?(Any|\*)') 'firewall permits unrestricted remote addresses'
}

Test-Case 'ready status requires end-to-end local checks' {
    Assert-True ($source -match 'function\s+Assert-RdpReady') 'missing RDP readiness assertion'
    Assert-True ($source -match 'Get-NetTCPConnection') 'TCP 3389 listener is not checked'
    Assert-True ($source -match 'PortNumber') 'configured RDP port is not fixed and validated'
    Assert-True ($source -match '\$rdpTcp\.MinEncryptionLevel\s+-ne\s+3') 'minimum RDP encryption level is not validated'
    Assert-True ($source -match 'Win32_Service') 'listener ownership is not tied to TermService'
    Assert-True ($source -match 'Restart-Service\s+-Name\s+TermService') 'TermService is not restarted after material registry changes'
    Assert-True ($source -match 'Get-NetFirewallAddressFilter') 'firewall remote address is not validated'
    Assert-True ($source -match '\[RDP READY\]') 'ready notification is missing'
    Assert-True ($source -match '\[RDP FAILED\]') 'failure notification is missing'
}

Test-Case 'notifications include connection guidance and credential warning' {
    Assert-True ($source -match 'mstsc') 'Windows connection command is missing'
    Assert-True ($source -match 'xfreerdp') 'Linux connection command is missing'
    Assert-True ($source -match '(?i)Windows App') 'macOS client guidance is missing'
    Assert-True ($source -match '(?i)PIN') 'Windows Hello PIN warning is missing'
}

$loaded = $false
if (Test-Path -LiteralPath $scriptPath -PathType Leaf) {
    try {
        . $scriptPath -LibraryOnly
        $loaded = $true
    } catch {
        $script:Failures.Add("library load: $($_.Exception.Message)")
    }
}

if ($loaded) {
    Test-Case 'Tailnet IPv4 range boundaries are enforced' {
        Assert-True (Test-TailscaleIPv4 '100.64.0.1') 'lower valid address was rejected'
        Assert-True (Test-TailscaleIPv4 '100.127.255.254') 'upper valid address was rejected'
        Assert-True (-not (Test-TailscaleIPv4 '100.63.255.255')) 'address below range was accepted'
        Assert-True (-not (Test-TailscaleIPv4 '100.128.0.1')) 'address above range was accepted'
        Assert-True (-not (Test-TailscaleIPv4 'not-an-ip')) 'invalid address was accepted'
    }

    Test-Case 'RDP host edition helper rejects Home editions' {
        Assert-True (-not (Test-RdpHostEdition 'Core')) 'Core was accepted'
        Assert-True (-not (Test-RdpHostEdition 'CoreSingleLanguage')) 'CoreSingleLanguage was accepted'
        Assert-True (-not (Test-RdpHostEdition 'CloudEdition')) 'CloudEdition was accepted'
        Assert-True (-not (Test-RdpHostEdition 'CloudEditionN')) 'CloudEditionN was accepted'
        Assert-True (Test-RdpHostEdition 'Professional') 'Professional was rejected'
        Assert-True (Test-RdpHostEdition 'Enterprise') 'Enterprise was rejected'
        Assert-True (Test-RdpHostEdition 'ServerStandard') 'ServerStandard was rejected'
    }

    Test-Case 'RDP port expression detects exact, list, range, and any-port rules' {
        Assert-True (Test-RdpPortExpression '3389') 'exact RDP port was missed'
        Assert-True (Test-RdpPortExpression @('80', '3389')) 'RDP port in an array was missed'
        Assert-True (Test-RdpPortExpression '3388-3390') 'range containing RDP port was missed'
        Assert-True (-not (Test-RdpPortExpression '3390')) 'unrelated port was accepted'
        Assert-True (Test-RdpPortExpression 'Any') 'Any-port rule was missed'
        Assert-True (Test-RdpPortExpression '*') 'wildcard port rule was missed'
    }

    Test-Case 'material RDP registry changes restart a running TermService' {
        $commandNames = @('Get-ItemProperty', 'Set-ItemProperty', 'Get-Service', 'Set-Service', 'Restart-Service', 'Start-Service')
        $oldFunctions = @{}
        foreach ($name in $commandNames) {
            $existing = Get-Command $name -CommandType Function -ErrorAction SilentlyContinue
            if ($existing) { $oldFunctions[$name] = $existing.ScriptBlock }
        }
        try {
            $script:RestartCalled = $false
            $script:StartCalled = $false
            Set-Item Function:\Get-ItemProperty {
                param($LiteralPath, $Name, $ErrorAction)
                if ($LiteralPath -match 'RDP-Tcp$') {
                    return [pscustomobject]@{ UserAuthentication = 0; SecurityLayer = 1; MinEncryptionLevel = 2; PortNumber = 3390 }
                }
                return [pscustomobject]@{ fDenyTSConnections = 1 }
            }
            Set-Item Function:\Set-ItemProperty { param($LiteralPath, $Name, $Type, $Value) }
            Set-Item Function:\Get-Service { param($Name, $ErrorAction) return [pscustomobject]@{ Status = 'Running' } }
            Set-Item Function:\Set-Service { param($Name, $StartupType) }
            Set-Item Function:\Restart-Service { param($Name, [switch]$Force) $script:RestartCalled = $true }
            Set-Item Function:\Start-Service { param($Name) $script:StartCalled = $true }

            Enable-RdpSettings
            Assert-True $script:RestartCalled 'running TermService was not restarted'
            Assert-True (-not $script:StartCalled) 'Start-Service was used instead of restart'
        } finally {
            foreach ($name in $commandNames) {
                if ($oldFunctions.ContainsKey($name)) { Set-Item -Path ("Function:\$name") -Value $oldFunctions[$name] }
                else { Remove-Item -Path ("Function:\$name") -ErrorAction SilentlyContinue }
            }
        }
    }

    Test-Case 'firewall is secured before RDP is enabled' {
        $names = @(
            'Assert-RdpHostSupported', 'Assert-TailscaleReady', 'Set-RdpTailnetFirewall',
            'Assert-RdpFirewallRule', 'Grant-CurrentUserRdpAccess', 'Enable-RdpSettings',
            'Assert-RdpReady', 'Send-Telegram'
        )
        $originals = @{}
        foreach ($name in $names) { $originals[$name] = (Get-Command $name).ScriptBlock }
        try {
            $script:CallOrder = New-Object System.Collections.Generic.List[string]
            Set-Item Function:\Assert-RdpHostSupported { return 'Professional' }
            Set-Item Function:\Assert-TailscaleReady { return '100.64.0.20' }
            Set-Item Function:\Set-RdpTailnetFirewall { $script:CallOrder.Add('firewall') }
            Set-Item Function:\Assert-RdpFirewallRule { param($Name, $Protocol) $script:CallOrder.Add("verify-$Protocol") }
            Set-Item Function:\Grant-CurrentUserRdpAccess { $script:CallOrder.Add('grant'); return 'TEST\user' }
            Set-Item Function:\Enable-RdpSettings { $script:CallOrder.Add('enable') }
            Set-Item Function:\Assert-RdpReady { param($TailscaleIp) $script:CallOrder.Add('ready') }
            Set-Item Function:\Send-Telegram { param($Config, $Text) return $true }

            $result = Invoke-RdpSetup
            Assert-True ($result -eq 0) 'setup did not succeed under controlled dependencies'
            $firewallIndex = $script:CallOrder.IndexOf('firewall')
            $enableIndex = $script:CallOrder.IndexOf('enable')
            Assert-True ($firewallIndex -ge 0 -and $firewallIndex -lt $enableIndex) 'RDP was enabled before firewall reconciliation'
            Assert-True ($script:CallOrder.IndexOf('verify-TCP') -lt $enableIndex) 'TCP firewall was not verified before enabling RDP'
            Assert-True ($script:CallOrder.IndexOf('verify-UDP') -lt $enableIndex) 'UDP firewall was not verified before enabling RDP'
        } finally {
            foreach ($name in $names) { Set-Item -Path ("Function:\$name") -Value $originals[$name] }
        }
    }

    Test-Case 'firewall failure cannot enable RDP and sends FAILED' {
        $names = @('Assert-RdpHostSupported', 'Assert-TailscaleReady', 'Set-RdpTailnetFirewall', 'Enable-RdpSettings', 'Send-Telegram')
        $originals = @{}
        foreach ($name in $names) { $originals[$name] = (Get-Command $name).ScriptBlock }
        try {
            $script:RdpEnableCalled = $false
            $script:SentText = $null
            Set-Item Function:\Assert-RdpHostSupported { return 'Professional' }
            Set-Item Function:\Assert-TailscaleReady { return '100.64.0.20' }
            Set-Item Function:\Set-RdpTailnetFirewall { throw 'injected firewall failure' }
            Set-Item Function:\Enable-RdpSettings { $script:RdpEnableCalled = $true }
            Set-Item Function:\Send-Telegram { param($Config, $Text) $script:SentText = $Text; return $true }

            $result = Invoke-RdpSetup
            Assert-True ($result -eq 1) 'firewall failure did not fail setup'
            Assert-True (-not $script:RdpEnableCalled) 'RDP was enabled after firewall failure'
            Assert-True ($script:SentText -match '\[RDP FAILED\]') 'failure notification was not sent'
        } finally {
            foreach ($name in $names) { Set-Item -Path ("Function:\$name") -Value $originals[$name] }
        }
    }
}

Write-Host ''
Write-Host ("Passed: {0}; Failed: {1}" -f $script:Passes, $script:Failures.Count)
if ($script:Failures.Count) {
    $script:Failures | ForEach-Object { Write-Host " - $_" -ForegroundColor Red }
    exit 1
}
exit 0

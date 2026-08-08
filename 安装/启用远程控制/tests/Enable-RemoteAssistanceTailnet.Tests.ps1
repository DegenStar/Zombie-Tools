param()

$ErrorActionPreference = 'Stop'
$script:Failures = New-Object System.Collections.Generic.List[string]
$script:Passes = 0
$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root '启用-Remote-Assistance-Tailnet.ps1'
$rdpScriptPath = Join-Path $root '启用-RDP-Tailnet.ps1'

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
    Assert-True (Test-Path -LiteralPath $rdpScriptPath -PathType Leaf) 'RDP reference script is missing'
    $rdpSource = Get-Content -LiteralPath $rdpScriptPath -Raw
    $tokenPattern = '(?m)^\$TgBotToken\s*=\s*''([^'']+)''\s*$'
    $chatPattern = '(?m)^\$TgChatId\s*=\s*''([^'']+)''\s*$'
    $rdpToken = [regex]::Match($rdpSource, $tokenPattern)
    $remoteToken = [regex]::Match($source, $tokenPattern)
    $rdpChat = [regex]::Match($rdpSource, $chatPattern)
    $remoteChat = [regex]::Match($source, $chatPattern)
    Assert-True ($rdpToken.Success -and $remoteToken.Success) 'literal Bot Token assignment is missing'
    Assert-True ($rdpChat.Success -and $remoteChat.Success) 'literal Chat ID assignment is missing'
    Assert-True ($remoteToken.Groups[1].Value -ceq $rdpToken.Groups[1].Value) 'Bot Token differs from RDP script'
    Assert-True ($remoteChat.Groups[1].Value -ceq $rdpChat.Groups[1].Value) 'Chat ID differs from RDP script'
    Assert-True ($source -notmatch 'REMOTE_ASSISTANCE_TG_BOT_TOKEN|REMOTE_ASSISTANCE_TG_CHAT_ID') 'environment credential dependency remains'
}

Test-Case 'script checks native Remote Assistance and Tailnet prerequisites' {
    Assert-True ($source -match 'function\s+Get-MsraExe') 'missing msra.exe capability check'
    Assert-True ($source -match 'function\s+Assert-TailscaleReady') 'missing Tailscale readiness check'
    Assert-True ($source -match 'BackendState') 'Tailscale BackendState is not checked'
    Assert-True ($source -match '100\.64\.0\.0/10') 'Tailnet CIDR is missing'
}

Test-Case 'password generation uses a cryptographic source and does not mutate accounts' {
    Assert-True ($source -match 'RandomNumberGenerator') 'cryptographic random generator is missing'
    Assert-True ($source -notmatch '(?i)Set-LocalUser|net\s+user|Set-ADAccountPassword|ConvertTo-SecureString') 'script contains account password mutation logic'
}

Test-Case 'only solicited full-control Remote Assistance policy is enabled' {
    Assert-True ($source -match 'function\s+Enable-SolicitedRemoteAssistance') 'missing solicited assistance policy function'
    Assert-True ($source -match 'fAllowToGetHelp') 'solicited assistance policy is missing'
    Assert-True ($source -match 'fAllowFullControl') 'full-control request policy is missing'
    Assert-True ($source -notmatch '(?i)fAllowUnsolicited|RAUnsolicit') 'unsolicited assistance policy must not be enabled'
}

Test-Case 'firewall source contract is TCP-only Tailnet-only and all-profile' {
    Assert-True ($source -match 'Remote-Assistance-Tailscale-TCP') 'owned firewall rule is missing'
    Assert-True ($source -match 'function\s+Set-RemoteAssistanceTailnetFirewall') 'firewall reconciliation is missing'
    Assert-True ($source -match '-Protocol\s+TCP') 'TCP firewall protocol is missing'
    Assert-True ($source -match '-LocalPort\s+\$script:RemoteAssistancePort') 'TCP 3389 port is missing'
    Assert-True ($source -match '-RemoteAddress\s+\$script:TailnetCidr') 'Tailnet remote scope is missing'
    Assert-True ($source -match '-Profile\s+Any') 'all-profile firewall scope is missing'
    Assert-True ($source -match 'Disable-NetFirewallRule') 'competing allow rules are not disabled'
    Assert-True ($source -match 'Enable-NetFirewallRule') 'disabled rules are not restored after a failure'
    Assert-True ($source -match 'function\s+Undo-RemoteAssistanceTailnetFirewall') 'firewall rollback helper is missing'
    Assert-True ($source -match 'Get-NetFirewallAddressFilter') 'remote scope is not read back'
    Assert-True ($source -notmatch '(?i)-Protocol\s+UDP') 'Remote Assistance must not expose UDP'
    Assert-True ($source -notmatch '-RemoteAddress\s+["'']?(Any|\*)') 'firewall permits unrestricted sources'
}

Test-Case 'native invitation startup and readiness checks are present' {
    Assert-True ($source -match 'function\s+Start-RemoteAssistanceInvitation') 'invitation startup function is missing'
    Assert-True ($source -match '/saveasfile') 'native msra save-as-file mode is missing'
    Assert-True ($source -match 'Start-Process[\s\S]*-PassThru') 'run-owned msra process is not captured'
    Assert-True ($source -match 'GetTempPath|GetRandomFileName') 'unique temporary invitation path is missing'
    Assert-True ($source -match 'function\s+Assert-RemoteAssistanceReady') 'readiness assertion is missing'
    Assert-True ($source -match "Win32_Service[\s\S]*TermService") 'TermService process ownership is not checked'
    Assert-True ($source -match 'Get-NetTCPConnection') 'TCP listener is not checked'
}

Test-Case 'Telegram transports support document then password delivery' {
    Assert-True ($source -match 'function\s+Send-TelegramDocument') 'Telegram document sender is missing'
    Assert-True ($source -match 'sendDocument') 'Telegram sendDocument endpoint is missing'
    Assert-True ($source -match 'MultipartFormDataContent') 'multipart document upload is missing'
    Assert-True ($source -match 'StreamContent') 'streaming document content is missing'
    Assert-True ($source -match 'function\s+Send-TelegramMessage') 'Telegram message sender is missing'
    Assert-True ($source -match 'sendMessage') 'Telegram sendMessage endpoint is missing'
    Assert-True ($source -match '\[REMOTE ASSISTANCE READY\]') 'READY message marker is missing'
    Assert-True ($source -match '\[REMOTE ASSISTANCE FAILED\]') 'FAILED message marker is missing'
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

    Test-Case 'invitation passwords are distinct and command-safe' {
        $first = New-InvitationPassword -Length 20
        $second = New-InvitationPassword -Length 20
        Assert-True ($first -match '^[A-Za-z0-9]{20}$') 'first password has unsafe characters or length'
        Assert-True ($second -match '^[A-Za-z0-9]{20}$') 'second password has unsafe characters or length'
        Assert-True ($first -ne $second) 'two generated passwords unexpectedly match'
    }

    Test-Case 'invitation endpoint matching requires exact Tailnet IP and port' {
        Assert-True (Test-InvitationEndpoint -Content '65538,1,100.64.1.2:3389;*,ticket' -TailscaleIp '100.64.1.2') 'exact endpoint rejected'
        Assert-True (-not (Test-InvitationEndpoint -Content '65538,1,100.64.1.20:3389;*,ticket' -TailscaleIp '100.64.1.2')) 'partial IP match accepted'
        Assert-True (-not (Test-InvitationEndpoint -Content '65538,1,100.64.1.2:3390;*,ticket' -TailscaleIp '100.64.1.2')) 'wrong port accepted'
    }

    Test-Case 'Remote Assistance port expressions detect exact, list, range, and any-port rules' {
        Assert-True (Test-RemoteAssistancePortExpression '3389') 'exact port was missed'
        Assert-True (Test-RemoteAssistancePortExpression @('80', '3389')) 'port in array was missed'
        Assert-True (Test-RemoteAssistancePortExpression '3388-3390') 'range containing port was missed'
        Assert-True (-not (Test-RemoteAssistancePortExpression '3390')) 'unrelated port was accepted'
        Assert-True (Test-RemoteAssistancePortExpression 'Any') 'Any-port rule was missed'
        Assert-True (Test-RemoteAssistancePortExpression '*') 'wildcard port rule was missed'
    }

    Test-Case 'TCP and any-protocol allow rules compete with Remote Assistance' {
        $commandNames = @('Get-NetFirewallPortFilter', 'Get-NetFirewallRule')
        $oldFunctions = @{}
        foreach ($name in $commandNames) {
            $existing = Get-Command $name -CommandType Function -ErrorAction SilentlyContinue
            if ($existing) { $oldFunctions[$name] = $existing.ScriptBlock }
        }
        try {
            Set-Item Function:\Get-NetFirewallPortFilter {
                param($PolicyStore, $ErrorAction)
                @(
                    [pscustomobject]@{ Protocol = 'TCP'; LocalPort = '3389'; Id = 'tcp' },
                    [pscustomobject]@{ Protocol = 'Any'; LocalPort = 'Any'; Id = 'any' },
                    [pscustomobject]@{ Protocol = '256'; LocalPort = '*'; Id = 'numeric-any' },
                    [pscustomobject]@{ Protocol = 'UDP'; LocalPort = '3389'; Id = 'udp' }
                )
            }
            Set-Item Function:\Get-NetFirewallRule {
                [CmdletBinding()]
                param([Parameter(ValueFromPipeline = $true)]$InputObject)
                process {
                    [pscustomobject]@{
                        Name = "rule-$($InputObject.Id)"
                        Direction = 'Inbound'
                        Action = 'Allow'
                        Enabled = 'True'
                    }
                }
            }

            $rules = @(Get-CompetingRemoteAssistanceFirewallRules)
            Assert-True ($rules.Count -eq 3) "expected three competing rules, got $($rules.Count)"
            Assert-True ($rules.Name -contains 'rule-any') 'Protocol=Any rule was missed'
            Assert-True ($rules.Name -contains 'rule-numeric-any') 'Protocol=256 rule was missed'
            Assert-True ($rules.Name -notcontains 'rule-udp') 'UDP-only rule was incorrectly treated as competing'
        } finally {
            foreach ($name in $commandNames) {
                if ($oldFunctions.ContainsKey($name)) { Set-Item -Path ("Function:\$name") -Value $oldFunctions[$name] }
                else { Remove-Item -Path ("Function:\$name") -ErrorAction SilentlyContinue }
            }
        }
    }

    Test-Case 'policy writer sets solicited assistance and full control to one' {
        $commandNames = @('New-Item', 'Set-ItemProperty')
        $oldFunctions = @{}
        foreach ($name in $commandNames) {
            $existing = Get-Command $name -CommandType Function -ErrorAction SilentlyContinue
            if ($existing) { $oldFunctions[$name] = $existing.ScriptBlock }
        }
        try {
            $script:PolicyWrites = New-Object System.Collections.Generic.List[string]
            Set-Item Function:\New-Item { param($Path, [switch]$Force, $ErrorAction) return $null }
            Set-Item Function:\Set-ItemProperty {
                param($LiteralPath, $Name, $Type, $Value)
                $script:PolicyWrites.Add("$Name=$Value")
            }
            Enable-SolicitedRemoteAssistance
            Assert-True ($script:PolicyWrites -contains 'fAllowToGetHelp=1') 'fAllowToGetHelp was not enabled'
            Assert-True ($script:PolicyWrites -contains 'fAllowFullControl=1') 'fAllowFullControl was not enabled'
        } finally {
            foreach ($name in $commandNames) {
                if ($oldFunctions.ContainsKey($name)) { Set-Item -Path ("Function:\$name") -Value $oldFunctions[$name] }
                else { Remove-Item -Path ("Function:\$name") -ErrorAction SilentlyContinue }
            }
        }
    }

    Test-Case 'readiness accepts only a TermService-owned TCP listener' {
        $commandNames = @(
            'Assert-RemoteAssistancePolicy', 'Assert-RemoteAssistanceFirewallRule',
            'Assert-NoCompetingRemoteAssistanceFirewallRules', 'Get-CimInstance',
            'Get-NetTCPConnection', 'Start-Sleep'
        )
        $oldFunctions = @{}
        foreach ($name in $commandNames) {
            $existing = Get-Command $name -CommandType Function -ErrorAction SilentlyContinue
            if ($existing) { $oldFunctions[$name] = $existing.ScriptBlock }
        }
        $tempPath = Join-Path ([IO.Path]::GetTempPath()) ([IO.Path]::GetRandomFileName() + '.msrcIncident')
        try {
            Set-Content -LiteralPath $tempPath -Value '65538,1,100.64.1.2:3389;*,ticket' -NoNewline
            Set-Item Function:\Assert-RemoteAssistancePolicy { }
            Set-Item Function:\Assert-RemoteAssistanceFirewallRule { }
            Set-Item Function:\Assert-NoCompetingRemoteAssistanceFirewallRules { }
            Set-Item Function:\Get-CimInstance { param($ClassName, $Filter, $ErrorAction) [pscustomobject]@{ ProcessId = 4242 } }
            Set-Item Function:\Get-NetTCPConnection { param($State, $LocalPort, $ErrorAction) [pscustomobject]@{ OwningProcess = 4242 } }
            Set-Item Function:\Start-Sleep { param($Seconds) }
            $invitation = [pscustomobject]@{
                Process = [pscustomobject]@{ HasExited = $false; Id = 3131 }
                Path = $tempPath
                Password = 'Abcdef1234567890WXYZ'
            }
            Assert-RemoteAssistanceReady -Invitation $invitation -TailscaleIp '100.64.1.2'

            Set-Item Function:\Get-NetTCPConnection { param($State, $LocalPort, $ErrorAction) [pscustomobject]@{ OwningProcess = 9999 } }
            $threw = $false
            try { Assert-RemoteAssistanceReady -Invitation $invitation -TailscaleIp '100.64.1.2' } catch { $threw = $true }
            Assert-True $threw 'listener owned by an unrelated process was accepted'

            $script:ExitChecks = 0
            $exitingProcess = [pscustomobject]@{ Id = 3131 }
            $exitingProcess | Add-Member -MemberType ScriptProperty -Name HasExited -Value {
                $script:ExitChecks++
                return $script:ExitChecks -ge 2
            }
            $invitation.Process = $exitingProcess
            Set-Item Function:\Get-NetTCPConnection { param($State, $LocalPort, $ErrorAction) [pscustomobject]@{ OwningProcess = 4242 } }
            $threw = $false
            try { Assert-RemoteAssistanceReady -Invitation $invitation -TailscaleIp '100.64.1.2' } catch { $threw = $true }
            Assert-True $threw 'msra.exe exit during listener polling was accepted'
        } finally {
            Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
            foreach ($name in $commandNames) {
                if ($oldFunctions.ContainsKey($name)) { Set-Item -Path ("Function:\$name") -Value $oldFunctions[$name] }
                else { Remove-Item -Path ("Function:\$name") -ErrorAction SilentlyContinue }
            }
        }
    }

    Test-Case 'success and failure messages carry the required handoff guidance' {
        $ready = New-RemoteAssistanceSuccessMessage -TailscaleIp '100.64.1.2' -Password 'Abc123' -UserName 'TEST\user'
        Assert-True ($ready -match '\[REMOTE ASSISTANCE READY\]') 'READY marker missing'
        Assert-True ($ready -match '100\.64\.1\.2') 'Tailnet IP missing'
        Assert-True ($ready -match 'Abc123') 'one-time password missing'
        Assert-True ($ready -match '(?i)msrcIncident|\u9080\u8bf7') 'attachment instructions missing'
        Assert-True ($ready -match '\u63a5\u53d7') 'target acceptance guidance missing'
        Assert-True ($ready -match '\u63a7\u5236') 'full-control confirmation guidance missing'
        Assert-True ($ready -match '\u4e34\u65f6|\u5c3d\u5feb') 'temporary invitation guidance missing'

        $failed = New-RemoteAssistanceFailureMessage -Reason 'injected failure'
        Assert-True ($failed -match '\[REMOTE ASSISTANCE FAILED\]') 'FAILED marker missing'
        Assert-True ($failed -match 'injected failure') 'failure reason missing'
    }

    Test-Case 'complete Telegram handoff succeeds and keeps the waiting process alive' {
        $names = @(
            'Assert-TelegramConfig', 'Assert-TailscaleReady', 'Get-MsraExe',
            'Get-RemoteAssistancePolicySnapshot', 'Enable-SolicitedRemoteAssistance',
            'Assert-RemoteAssistancePolicy', 'Restore-RemoteAssistancePolicy',
            'Set-RemoteAssistanceTailnetFirewall', 'Undo-RemoteAssistanceTailnetFirewall',
            'Start-RemoteAssistanceInvitation',
            'Assert-RemoteAssistanceReady', 'Send-TelegramDocument',
            'Send-TelegramMessage', 'Remove-InvitationArtifacts'
        )
        $originals = @{}
        foreach ($name in $names) { $originals[$name] = (Get-Command $name).ScriptBlock }
        try {
            $script:CallOrder = New-Object System.Collections.Generic.List[string]
            $script:CleanupStopped = $null
            $script:CleanupSucceeds = $true
            Set-Item Function:\Assert-TelegramConfig { $script:CallOrder.Add('telegram-config') }
            Set-Item Function:\Assert-TailscaleReady { $script:CallOrder.Add('tailscale'); return '100.64.1.2' }
            Set-Item Function:\Get-MsraExe { $script:CallOrder.Add('msra-capability'); return 'C:\Windows\System32\msra.exe' }
            Set-Item Function:\Get-RemoteAssistancePolicySnapshot { $script:CallOrder.Add('policy-snapshot'); return @() }
            Set-Item Function:\Enable-SolicitedRemoteAssistance { $script:CallOrder.Add('policy') }
            Set-Item Function:\Assert-RemoteAssistancePolicy { }
            Set-Item Function:\Restore-RemoteAssistancePolicy { param($Snapshot) $script:CallOrder.Add('policy-rollback') }
            Set-Item Function:\Set-RemoteAssistanceTailnetFirewall {
                $script:CallOrder.Add('firewall')
                return [pscustomobject]@{ CreatedRuleNames = @('test-rule'); DisabledRules = @() }
            }
            Set-Item Function:\Undo-RemoteAssistanceTailnetFirewall { param($State) $script:CallOrder.Add('firewall-rollback'); return $true }
            Set-Item Function:\Start-RemoteAssistanceInvitation {
                param($MsraExe, $TailscaleIp)
                $script:CallOrder.Add('invitation')
                return [pscustomobject]@{ Process = [pscustomobject]@{ Id = 10; HasExited = $false }; Path = 'test.msrcIncident'; Password = 'Pass123' }
            }
            Set-Item Function:\Assert-RemoteAssistanceReady { param($Invitation, $TailscaleIp) $script:CallOrder.Add('ready-check') }
            Set-Item Function:\Send-TelegramDocument { param($Config, $Path, $Caption) $script:CallOrder.Add('document'); return $true }
            Set-Item Function:\Send-TelegramMessage { param($Config, $Text) $script:CallOrder.Add('ready-message'); return $true }
            Set-Item Function:\Remove-InvitationArtifacts {
                param($Invitation, [switch]$StopProcess)
                [void]$script:CallOrder.Add('remove-local-file')
                [void]($script:CleanupStopped = $StopProcess.IsPresent)
                return $script:CleanupSucceeds
            }

            $result = Invoke-RemoteAssistanceSetup
            Assert-True ($result -eq 0) 'complete handoff did not return success'
            Assert-True (-not $script:CleanupStopped) 'successful handoff stopped the waiting process'
            $expected = 'telegram-config,tailscale,msra-capability,policy-snapshot,policy,firewall,invitation,ready-check,document,ready-message,remove-local-file'
            Assert-True (($script:CallOrder -join ',') -eq $expected) "unexpected order: $($script:CallOrder -join ',')"

            $script:CleanupSucceeds = $false
            $script:CleanupStopped = $false
            $result = Invoke-RemoteAssistanceSetup
            Assert-True ($result -eq 1) 'local invitation deletion failure returned success'
            Assert-True $script:CleanupStopped 'deletion failure did not stop the invitation process'
        } finally {
            foreach ($name in $names) { Set-Item -Path ("Function:\$name") -Value $originals[$name] }
        }
    }

    Test-Case 'document and password-message failures are fatal and stop their invitations' {
        $names = @(
            'Assert-TelegramConfig', 'Assert-TailscaleReady', 'Get-MsraExe',
            'Get-RemoteAssistancePolicySnapshot', 'Enable-SolicitedRemoteAssistance',
            'Assert-RemoteAssistancePolicy', 'Restore-RemoteAssistancePolicy',
            'Set-RemoteAssistanceTailnetFirewall', 'Undo-RemoteAssistanceTailnetFirewall',
            'Start-RemoteAssistanceInvitation',
            'Assert-RemoteAssistanceReady', 'Send-TelegramDocument',
            'Send-TelegramMessage', 'Remove-InvitationArtifacts'
        )
        $originals = @{}
        foreach ($name in $names) { $originals[$name] = (Get-Command $name).ScriptBlock }
        try {
            $script:CleanupStopped = $false
            $script:DocumentSucceeds = $false
            $script:FirewallRolledBack = $false
            $script:PolicyRolledBack = $false
            Set-Item Function:\Assert-TelegramConfig { }
            Set-Item Function:\Assert-TailscaleReady { return '100.64.1.2' }
            Set-Item Function:\Get-MsraExe { return 'C:\Windows\System32\msra.exe' }
            Set-Item Function:\Get-RemoteAssistancePolicySnapshot { return @() }
            Set-Item Function:\Enable-SolicitedRemoteAssistance { }
            Set-Item Function:\Assert-RemoteAssistancePolicy { }
            Set-Item Function:\Restore-RemoteAssistancePolicy { param($Snapshot) $script:PolicyRolledBack = $true }
            Set-Item Function:\Set-RemoteAssistanceTailnetFirewall {
                return [pscustomobject]@{ CreatedRuleNames = @('test-rule'); DisabledRules = @() }
            }
            Set-Item Function:\Undo-RemoteAssistanceTailnetFirewall { param($State) $script:FirewallRolledBack = $true; return $true }
            Set-Item Function:\Start-RemoteAssistanceInvitation {
                param($MsraExe, $TailscaleIp)
                return [pscustomobject]@{ Process = [pscustomobject]@{ Id = 10; HasExited = $false }; Path = 'test.msrcIncident'; Password = 'Pass123' }
            }
            Set-Item Function:\Assert-RemoteAssistanceReady { param($Invitation, $TailscaleIp) }
            Set-Item Function:\Send-TelegramDocument { param($Config, $Path, $Caption) return $script:DocumentSucceeds }
            Set-Item Function:\Send-TelegramMessage {
                param($Config, $Text)
                if ($Text -match '\[REMOTE ASSISTANCE READY\]') { return $false }
                return $true
            }
            Set-Item Function:\Remove-InvitationArtifacts {
                param($Invitation, [switch]$StopProcess)
                $script:CleanupStopped = $StopProcess.IsPresent
            }

            $result = Invoke-RemoteAssistanceSetup
            Assert-True ($result -eq 1) 'document delivery failure returned success'
            Assert-True $script:CleanupStopped 'document failure did not stop the invitation process'
            Assert-True $script:FirewallRolledBack 'document failure did not roll back the firewall'
            Assert-True $script:PolicyRolledBack 'document failure did not roll back Remote Assistance policy'

            $script:DocumentSucceeds = $true
            $script:CleanupStopped = $false
            $script:FirewallRolledBack = $false
            $script:PolicyRolledBack = $false
            $result = Invoke-RemoteAssistanceSetup
            Assert-True ($result -eq 1) 'password delivery failure returned success'
            Assert-True $script:CleanupStopped 'password failure did not stop the invitation process'
            Assert-True $script:FirewallRolledBack 'password failure did not roll back the firewall'
            Assert-True $script:PolicyRolledBack 'password failure did not roll back Remote Assistance policy'
        } finally {
            foreach ($name in $names) { Set-Item -Path ("Function:\$name") -Value $originals[$name] }
        }
    }

    Test-Case 'cleanup errors do not escape or suppress failure handling' {
        $names = @('Stop-Process', 'Remove-Item', 'Test-Path')
        $oldFunctions = @{}
        foreach ($name in $names) {
            $existing = Get-Command $name -CommandType Function -ErrorAction SilentlyContinue
            if ($existing) { $oldFunctions[$name] = $existing.ScriptBlock }
        }
        try {
            Set-Item Function:\Stop-Process { param($Id, [switch]$Force, $ErrorAction) throw 'injected stop failure' }
            Set-Item Function:\Remove-Item { param($LiteralPath, [switch]$Force, $ErrorAction) throw 'injected remove failure' }
            Set-Item Function:\Test-Path { param($LiteralPath, $PathType) return $true }
            $artifact = [pscustomobject]@{
                Process = [pscustomobject]@{ Id = 10; HasExited = $false }
                Path = 'test.msrcIncident'
            }
            $threw = $false
            $cleanupResult = $null
            try { $cleanupResult = Remove-InvitationArtifacts -Invitation $artifact -StopProcess } catch { $threw = $true }
            Assert-True (-not $threw) 'cleanup exception escaped to the orchestration layer'
            Assert-True ($cleanupResult -eq $false) 'cleanup failure was not reported to the caller'
        } finally {
            foreach ($name in $names) {
                if ($oldFunctions.ContainsKey($name)) { Set-Item -Path ("Function:\$name") -Value $oldFunctions[$name] }
                else { Microsoft.PowerShell.Management\Remove-Item -Path ("Function:\$name") -ErrorAction SilentlyContinue }
            }
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

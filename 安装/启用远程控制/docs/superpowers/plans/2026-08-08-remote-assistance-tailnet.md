# Remote Assistance over Tailnet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a standalone Windows PowerShell script that creates a native solicited Remote Assistance invitation, limits it to Tailnet clients, and delivers the invitation and one-time password through Telegram.

**Architecture:** One Windows PowerShell 5.1-compatible entry point owns elevation, prerequisites, policy, firewall reconciliation, native `msra.exe` invitation startup, readiness assertions, Telegram handoff, and fail-closed cleanup. A separate PowerShell test file loads the entry point in library mode and uses pure helper tests, source-contract assertions, and narrowly scoped command substitution so Linux CI never mutates Windows state.

**Tech Stack:** Windows PowerShell 5.1-compatible PowerShell, `msra.exe`, Windows registry/service/firewall cmdlets, Tailscale CLI, Telegram Bot API, `System.Net.Http` multipart upload, custom PowerShell test harness.

---

## File map

- Create `启用-Remote-Assistance-Tailnet.ps1`: all production behavior and the executable entry point, following the shape of `启用-RDP-Tailnet.ps1`.
- Create `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`: parse, static security-contract, pure helper, substituted dependency, orchestration-order, and cleanup tests.
- Modify `🔴功能说明.md`: prerequisites, behavior, Telegram handoff, connection instructions, consent boundary, and troubleshooting for the new alternative.

### Task 1: Establish a red test harness and entry-point contract

**Files:**
- Create: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`
- Test: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Write the failing existence and parse tests**

Create the same small `Assert-True`/`Test-Case` harness used by `tests/Enable-RdpTailnet.Tests.ps1`, but resolve the target directly from this directory:

```powershell
$root = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $root '启用-Remote-Assistance-Tailnet.ps1'

Test-Case 'Remote Assistance setup script exists' {
    Assert-True (Test-Path -LiteralPath $scriptPath -PathType Leaf) 'target script is missing'
}
```

When the file exists, parse it with `[System.Management.Automation.Language.Parser]::ParseFile`. Add source assertions for `[switch]$LibraryOnly`, `[switch]$NoPause`, UAC `Start-Process ... -Verb RunAs`, top-level `$TgBotToken`/`$TgChatId`, and a terminal `if ($LibraryOnly) { return }` guard.

- [ ] **Step 2: Run the test and verify RED**

Run `pwsh -NoProfile -File tests/Enable-RemoteAssistanceTailnet.Tests.ps1`.

Expected: exit `1`, with `Remote Assistance setup script exists: target script is missing` (and parse failure may also be reported).

- [ ] **Step 3: Commit the red test**

```bash
git add -f tests/Enable-RemoteAssistanceTailnet.Tests.ps1
git commit -m "test: define Remote Assistance script contract"
```

### Task 2: Add the load-safe shell and pure prerequisite helpers

**Files:**
- Create: `启用-Remote-Assistance-Tailnet.ps1`
- Modify: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Extend tests for pure helpers**

After dot-sourcing with `. $scriptPath -LibraryOnly`, test Tailnet range boundaries, two distinct 20-character passwords matching `^[A-Za-z0-9]{20}$`, and exact invitation endpoint matching:

```powershell
Assert-True (Test-TailscaleIPv4 '100.64.0.1') 'lower Tailnet boundary rejected'
Assert-True (-not (Test-TailscaleIPv4 '100.128.0.1')) 'address above Tailnet accepted'
Assert-True (Test-InvitationEndpoint -Content '...,100.64.1.2:3389;...' -TailscaleIp '100.64.1.2') 'exact endpoint rejected'
Assert-True (-not (Test-InvitationEndpoint -Content '...,100.64.1.20:3389;...' -TailscaleIp '100.64.1.2')) 'partial IP match accepted'
```

Also assert the source uses `RandomNumberGenerator`, requires non-empty Telegram credentials, checks Tailscale `BackendState`, resolves `msra.exe`, and contains no password mutation commands.

- [ ] **Step 2: Run the focused suite and verify RED**

Expected: helper assertions fail because `Test-TailscaleIPv4`, `New-InvitationPassword`, and `Test-InvitationEndpoint` do not exist.

- [ ] **Step 3: Implement the minimal load-safe shell**

Copy only the established elevation/UTF-8/logging/pause conventions from `启用-RDP-Tailnet.ps1`. Keep Windows-only operations inside functions or below the library guard. Define Tailnet CIDR, TCP 3389, and rule name constants. Implement `Assert-TelegramConfig`, `Test-TailscaleIPv4`, `Get-TailscaleExe`, `Assert-TailscaleReady`, and `Get-MsraExe`.

Implement `New-InvitationPassword` with `RandomNumberGenerator.Create()` and rejection sampling over `A-Z`, `a-z`, and `0-9` (discard bytes `>= 248` before modulo 62), disposing the generator in `finally`. Implement `Test-InvitationEndpoint` with an escaped, digit/dot-bounded regex for the exact IP followed by `:3389`.

- [ ] **Step 4: Run tests and verify GREEN**

Expected: all Task 1-2 cases pass, and no Windows cmdlet is called during `-LibraryOnly` loading.

- [ ] **Step 5: Commit**

```bash
git add -f 启用-Remote-Assistance-Tailnet.ps1 tests/Enable-RemoteAssistanceTailnet.Tests.ps1
git commit -m "feat: add Remote Assistance prerequisites"
```

### Task 3: Configure solicited assistance and the Tailnet-only firewall

**Files:**
- Modify: `启用-Remote-Assistance-Tailnet.ps1`
- Modify: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Add failing policy tests**

Statically require both `fAllowToGetHelp` and `fAllowFullControl` with DWORD value `1`, and reject source containing unsolicited-assistance policy names such as `fAllowUnsolicited` or `RAUnsolicit`. Substitute `New-Item`, `Set-ItemProperty`, and `Get-ItemProperty`, call `Enable-SolicitedRemoteAssistance`, record writes, and assert `Assert-RemoteAssistancePolicy` accepts only the pair `{1,1}`.

- [ ] **Step 2: Add failing firewall tests**

Require the owned rule name, TCP, local port 3389, `-RemoteAddress $script:TailnetCidr`, `-Profile Any`, and no UDP rule or `Any`/`*` remote address. Exercise `Test-RemoteAssistancePortExpression` for exact, list, range, and unrelated ports. Statically require disabling competing inbound enabled TCP allow rules and post-write checks through `Get-NetFirewallPortFilter` and `Get-NetFirewallAddressFilter`.

- [ ] **Step 3: Run tests and verify RED**

Expected failures: missing policy, firewall, and port-expression functions.

- [ ] **Step 4: Implement policy functions**

Use `HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services` and `HKLM:\SYSTEM\CurrentControlSet\Control\Remote Assistance`. Ensure keys exist. Set solicited assistance and full-control values to `1`; do not write any unsolicited policy. `Assert-RemoteAssistancePolicy` reads back both required effective values and throws unless both are enabled.

- [ ] **Step 5: Implement firewall reconciliation**

Add `Test-RemoteAssistancePortExpression`, `Get-CompetingRemoteAssistanceFirewallRules`, `Assert-NoCompetingRemoteAssistanceFirewallRules`, `Set-RemoteAssistanceTailnetFirewall`, and `Assert-RemoteAssistanceFirewallRule`.

The competing-rule filter requires inbound, enabled, allow, TCP, explicit local port/range covering 3389, and a name unequal to the owned rule. Disable those rules, remove/recreate the owned rule with `-Profile Any`, and assert its action, direction, enabled state, all-profile applicability, TCP protocol, local port 3389, and sole remote address equal to Tailnet CIDR (accept Windows' equivalent netmask rendering).

- [ ] **Step 6: Run tests and verify GREEN**

Expected: all pure and substituted policy/firewall cases pass.

- [ ] **Step 7: Commit**

```bash
git add -f 启用-Remote-Assistance-Tailnet.ps1 tests/Enable-RemoteAssistanceTailnet.Tests.ps1
git commit -m "feat: secure Remote Assistance policy and firewall"
```

### Task 4: Generate and validate the native invitation

**Files:**
- Modify: `启用-Remote-Assistance-Tailnet.ps1`
- Modify: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Add failing invitation lifecycle tests**

Require `Start-Process` with `/saveasfile` and `-PassThru`. Substitute process/file commands around `Start-RemoteAssistanceInvitation`; assert it returns one object containing the run-owned process, path, and password after finding an exact Tailnet endpoint. Add cases for an absent/empty file, wrong IP, wrong port, and an exited process; each must throw. Require a unique file under `[IO.Path]::GetTempPath()` and never overwrite a fixed filename.

- [ ] **Step 2: Add failing listener tests**

Substitute `Get-CimInstance`, `Get-NetTCPConnection`, and `Start-Sleep`. Assert `Assert-RemoteAssistanceReady` accepts only TCP 3389 owned by the `Win32_Service` `TermService.ProcessId`, rejects an unrelated owner or exited run-owned process, and performs no more than ten one-second polls.

- [ ] **Step 3: Run tests and verify RED**

Expected: missing invitation and readiness functions.

- [ ] **Step 4: Implement invitation startup**

Create a unique temp `.msrcIncident` path, generate a 20-character password, and invoke resolved `msra.exe` with `/saveasfile`, safely quoted path, password, and `Start-Process -PassThru`. Poll briefly for a non-empty file while checking `$process.HasExited`. Require the exact Tailnet endpoint. Return a custom object with `Process`, `Path`, and `Password`; on failure stop only that process and remove only that temp file.

- [ ] **Step 5: Implement readiness**

`Assert-RemoteAssistanceReady` rechecks invitation content, policy, owned firewall, competing rules, and run-owned process. Resolve `TermService` through `Win32_Service`, require a nonzero PID, and poll `Get-NetTCPConnection -State Listen -LocalPort 3389` up to ten times for the same owning PID.

- [ ] **Step 6: Run tests and verify GREEN**

Expected: invitation failure paths clean only their own artifacts, and listener ownership/poll cases pass.

- [ ] **Step 7: Commit**

```bash
git add -f 启用-Remote-Assistance-Tailnet.ps1 tests/Enable-RemoteAssistanceTailnet.Tests.ps1
git commit -m "feat: create and verify Remote Assistance invitations"
```

### Task 5: Deliver through Telegram and enforce fail-closed orchestration

**Files:**
- Modify: `启用-Remote-Assistance-Tailnet.ps1`
- Modify: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Add failing Telegram transport tests**

Statically require `sendDocument`, `MultipartFormDataContent`, `StreamContent`, and `sendMessage`. Substitute transports for orchestration tests; never perform network calls. Require READY text to include marker, host, account, Tailnet IP, password, temporary-expiry guidance, attachment instructions, and target acceptance/control confirmation. Require FAILED text to include marker and reason.

- [ ] **Step 2: Add failing order and cleanup tests**

Replace orchestration dependencies with functions that append to `$script:CallOrder`. Assert this order:

```text
telegram-config -> tailscale -> msra-capability -> policy -> firewall -> invitation -> ready-check -> document -> ready-message -> remove-local-file
```

Inject document-upload and READY/password-message failures. Both return `1`, stop only the run-owned process, remove the local invitation, and attempt FAILED messaging. Success returns `0`, deletes the local file, and leaves the run-owned process alive.

- [ ] **Step 3: Run tests and verify RED**

Expected: Telegram functions, message builders, cleanup helper, and `Invoke-RemoteAssistanceSetup` are missing.

- [ ] **Step 4: Implement Telegram transports**

Implement `Send-TelegramMessage` with `Invoke-RestMethod` and a 15-second timeout. Implement `Send-TelegramDocument` with `System.Net.Http.HttpClient`, `MultipartFormDataContent`, `StringContent`, a read-only `FileStream`, and `StreamContent`; post to the Bot API, call `EnsureSuccessStatusCode()`, and dispose all resources in `finally`. Return `$false` on transport errors.

- [ ] **Step 5: Implement messages, cleanup, and orchestration**

Add `New-RemoteAssistanceSuccessMessage`, `New-RemoteAssistanceFailureMessage`, and `Remove-InvitationArtifacts`. Cleanup accepts the invitation object plus `-StopProcess`; it removes only the stored temp file and stops only the stored process when requested.

Implement `Invoke-RemoteAssistanceSetup` with `try/catch/finally`. Complete all local checks before upload, treat false from either Telegram call as failure, remove the file on success and failure, stop the process only on failure, attempt FAILED messaging from `catch`, and return `0` only after complete delivery. Wire `LibraryOnly`, pause, and exit handling at the bottom.

- [ ] **Step 6: Run tests and verify GREEN**

Expected: every test passes without Windows mutation or network requests.

- [ ] **Step 7: Commit**

```bash
git add -f 启用-Remote-Assistance-Tailnet.ps1 tests/Enable-RemoteAssistanceTailnet.Tests.ps1
git commit -m "feat: deliver Remote Assistance invitations"
```

### Task 6: Document usage and verify the complete change

**Files:**
- Modify: `🔴功能说明.md`
- Test: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Add documentation**

Document prerequisites, changes, exact commands, exit codes, opening the attachment, entering the separate password, target acceptance/control confirmation, temporary invitation behavior, Tailnet-only TCP 3389, repeated runs, and relevant troubleshooting. Explicitly warn that the configured Telegram chat receives both the invitation and its password, so access to that chat and Bot Token is part of the security boundary.

- [ ] **Step 2: Run focused verification**

Run `pwsh -NoProfile -File tests/Enable-RemoteAssistanceTailnet.Tests.ps1`.

Expected: exit `0`, `Failed: 0`.

- [ ] **Step 3: Run existing regression tests**

```bash
pwsh -NoProfile -File tests/Enable-RdpTailnet.Tests.ps1
bash tests/enable-macos-screen-sharing-tailnet-tests.sh
```

Expected: each suite exits `0`. Record any pre-existing path/layout failure without changing unrelated production files.

- [ ] **Step 4: Parse production directly**

Run the PowerShell parser against `./启用-Remote-Assistance-Tailnet.ps1`; expected exit `0` with no parser errors.

- [ ] **Step 5: Review diff and security contract**

```bash
git diff --check
git diff -- 启用-Remote-Assistance-Tailnet.ps1 tests/Enable-RemoteAssistanceTailnet.Tests.ps1 🔴功能说明.md
```

Confirm there is no unrestricted firewall address, UDP rule, password mutation, unsolicited policy, hard-coded invitation password, undocumented invitation rewrite, or unrelated edit.

- [ ] **Step 6: Commit documentation**

```bash
git add -f 🔴功能说明.md
git commit -m "docs: explain Remote Assistance over Tailnet"
```

- [ ] **Step 7: Apply verification-before-completion**

Invoke `superpowers:verification-before-completion`, rerun focused tests, parser check, regressions, `git diff --check`, and `git status --short`, then report exact results and the Linux limitation for a real Windows consent handshake.

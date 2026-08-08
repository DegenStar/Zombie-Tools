# Hard-Code Remote Assistance Telegram Credentials Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace environment-based Telegram credentials in the Remote Assistance script with literal values identical to the existing RDP script.

**Architecture:** Keep the current top-level `$TgBotToken` and `$TgChatId` interface and all notification behavior unchanged. Tests derive the expected values from `启用-RDP-Tailnet.ps1` instead of duplicating secrets, then require literal assignments in the Remote Assistance source; documentation describes the approved plaintext/Git exposure.

**Tech Stack:** Windows PowerShell 5.1-compatible PowerShell, custom PowerShell test harness, Markdown.

---

### Task 1: Lock the literal credential contract with a failing test

**Files:**
- Modify: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`
- Reference: `启用-RDP-Tailnet.ps1`
- Test: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Replace the environment-credential assertions**

Read `启用-RDP-Tailnet.ps1` in the test setup. Extract its single-quoted `$TgBotToken` and `$TgChatId` assignments with anchored multiline regular expressions. Extract the same assignments from the Remote Assistance source, then assert:

```powershell
Assert-True ($rdpToken.Success -and $remoteToken.Success) 'literal Bot Token assignment is missing'
Assert-True ($rdpChat.Success -and $remoteChat.Success) 'literal Chat ID assignment is missing'
Assert-True ($remoteToken.Groups[1].Value -ceq $rdpToken.Groups[1].Value) 'Bot Token differs from RDP script'
Assert-True ($remoteChat.Groups[1].Value -ceq $rdpChat.Groups[1].Value) 'Chat ID differs from RDP script'
Assert-True ($source -notmatch 'REMOTE_ASSISTANCE_TG_BOT_TOKEN|REMOTE_ASSISTANCE_TG_CHAT_ID') 'environment credential dependency remains'
```

Do not copy either secret into the test file.

- [ ] **Step 2: Run the focused suite and verify RED**

Run:

```bash
pwsh -NoProfile -File tests/Enable-RemoteAssistanceTailnet.Tests.ps1
```

Expected: exit `1`; the credential-contract test fails because the Remote Assistance assignments still reference environment variables.

### Task 2: Hard-code the approved values

**Files:**
- Modify: `启用-Remote-Assistance-Tailnet.ps1:42-44`
- Test: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Replace both environment assignments**

Copy the current single-quoted `$TgBotToken` and `$TgChatId` assignment lines from `启用-RDP-Tailnet.ps1` into the Remote Assistance script. Keep `Assert-TelegramConfig` and every consumer unchanged. Remove the environment-variable wording from the adjacent comment.

- [ ] **Step 2: Run the focused suite and verify GREEN**

Run the focused PowerShell test. Expected: `Passed: 20; Failed: 0`.

### Task 3: Align documentation and verify

**Files:**
- Modify: `🔴功能说明.md:113-140`
- Test: `tests/Enable-RemoteAssistanceTailnet.Tests.ps1`

- [ ] **Step 1: Update usage and security wording**

State that both credentials are literal variables at the top of the Remote Assistance script, remove environment-variable setup commands, and explicitly warn that source/repository readers can obtain both values and that a leaked token must be rotated in BotFather.

- [ ] **Step 2: Run final verification**

Run:

```bash
pwsh -NoProfile -File tests/Enable-RemoteAssistanceTailnet.Tests.ps1
pwsh -NoProfile -Command '$tokens=$null; $errors=$null; [void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path "./启用-Remote-Assistance-Tailnet.ps1"), [ref]$tokens, [ref]$errors); if ($errors.Count) { exit 1 }'
git diff --check
```

Expected: focused suite exits `0` with 20 passes, parser exits `0`, and diff check is clean.

- [ ] **Step 3: Commit**

```bash
git add -f 启用-Remote-Assistance-Tailnet.ps1 tests/Enable-RemoteAssistanceTailnet.Tests.ps1 🔴功能说明.md
git commit -m "refactor: hard-code Remote Assistance Telegram credentials"
```

- [ ] **Step 4: Apply verification-before-completion**

Invoke `superpowers:verification-before-completion`, rerun the commands above, and report the plaintext credential/Git-history consequence without printing either secret in the response.

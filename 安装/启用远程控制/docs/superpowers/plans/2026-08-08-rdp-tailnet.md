# RDP over Tailnet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a standalone PowerShell script that enables secure RDP access for the current Windows user over Tailnet and sends Telegram results.

**Architecture:** A single testable PowerShell entry point owns elevation, prerequisite checks, RDP configuration, Tailnet-only firewall rules, readiness assertions, and notification formatting. A separate PowerShell test file validates helpers and the fail-closed orchestration contract without mutating the test host.

**Tech Stack:** Windows PowerShell 5.1-compatible PowerShell, Windows registry/service/firewall cmdlets, Tailscale CLI, Telegram Bot API.

---

### Task 1: Lock the contract with failing tests

**Files:**
- Create: `tests/Enable-RdpTailnet.Tests.ps1`
- Create: `远程控制设置/启用-RDP-Tailnet.ps1`

- [ ] Write tests for parsing, library loading, Tailnet address validation, UAC/test switches, NLA, current-user authorization, firewall scope, readiness checks, and success/failure Telegram notifications.
- [ ] Run `pwsh -NoProfile -File tests/Enable-RdpTailnet.Tests.ps1` and confirm it fails because the script is absent.

### Task 2: Implement the secure, idempotent setup

- [ ] Add elevation and top-level Telegram configuration matching `SETUP.ps1` conventions.
- [ ] Add Windows edition and Tailscale prerequisite assertions.
- [ ] Enable RDP and NLA, authorize the current identity, start Remote Desktop Services, and reconcile dedicated TCP/UDP firewall rules.
- [ ] Verify the complete readiness contract before constructing `[RDP READY]`; catch failures and construct `[RDP FAILED]`.
- [ ] Run the new tests until green.

### Task 3: Verify regression safety

- [ ] Parse the new script directly with the PowerShell parser.
- [ ] Run `pwsh -NoProfile -File tests/Enable-RdpTailnet.Tests.ps1`.
- [ ] Run `pwsh -NoProfile -File tests/SETUP.Tests.ps1` to ensure existing Windows SSH behavior remains intact.
- [ ] Review the final diff and confirm no SSH/Tailscale setup behavior or unrelated files changed.

# macOS Screen Sharing over Tailnet Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a standalone macOS shell script that enables local-only Apple Screen Sharing for the invoking user and reports Tailnet SSH-tunnel instructions through Telegram.

**Architecture:** One sourceable Bash entry point owns sudo relaunch, prerequisite checks, exact-user Apple `kickstart` convergence, local-only preferences, fail-closed listener assertions, notification formatting, and exit status. One portable Bash test file validates pure helpers and orchestration on the development host without changing system services; a documented macOS checklist covers real platform integration.

**Tech Stack:** Bash 3.2-compatible shell, macOS Remote Management `kickstart`, `launchctl`/`lsof`/`nc`, Tailscale CLI, curl, Telegram Bot API.

---

### Task 1: Define the security contract with failing tests

**Files:**
- Create: `tests/enable-macos-screen-sharing-tailnet-tests.sh`
- Create: `远程控制设置/启用-macOS屏幕共享-Tailnet.sh`

- [ ] Test script existence, syntax, library loading, sudo identity validation, Darwin/Tailscale prerequisites, exact-user `kickstart` convergence, `VNCLocalOnly`, legacy VNC disabling, Telegram result labels, and loopback-bound tunnel instructions.
- [ ] Test Tailnet IPv4 boundaries and IPv4/IPv6 loopback, wildcard, hostname, and multiple-listener classification as real helper behavior.
- [ ] Stub all-users/previous-users migration, delayed activation, activation timeout, delayed teardown, and teardown timeout; prove public-listener cleanup completes or reaches its deadline before FAILED notification.
- [ ] Run `bash tests/enable-macos-screen-sharing-tailnet-tests.sh` and confirm failure because the implementation is absent.

### Task 2: Implement fail-closed macOS setup

- [ ] Add Bash 3.2-compatible logging, argument parsing, sudo relaunch, Tailscale discovery/readiness, and Telegram helpers.
- [ ] Validate the fixed ARDAgent `kickstart` path and its help output; read `com.apple.access_ard` membership, revoke every previous member with `-privs -none`, turn access off, set specified-users mode, authorize exactly the preserved invoking user with `-ControlObserve`, disable legacy VNC, and stage `VNCLocalOnly=true` before activation.
- [ ] Verify `ARD_AllLocalUsers`, `VNCLocalOnly`, exact `com.apple.access_ard` membership, target `naprivs`, legacy mode, ARDAgent process, TCP reachability, and every listener address with bounded polling before READY; on public exposure, deactivate and poll teardown before notifying.
- [ ] Ensure every caught failure sends FAILED when Telegram is configured and returns nonzero.
- [ ] Run the new tests until green.

### Task 3: Regression and review

- [ ] Run `bash -n` for the new script and test file.
- [ ] Run the macOS screen-sharing tests plus existing PowerShell RDP and SSH regression suites.
- [ ] Review the script for plaintext credential handling, incorrect-user authorization, public VNC exposure, false READY states, and Bash 3.2 incompatibilities.
- [ ] Document and perform the real macOS checklist: required flags exist, access set is exact, legacy mode is off, only loopback listens, SSH uses `-o ExitOnForwardFailure=yes -L 127.0.0.1:5900:127.0.0.1:5900`, and Screen Sharing reports any remaining privacy approval requirement.

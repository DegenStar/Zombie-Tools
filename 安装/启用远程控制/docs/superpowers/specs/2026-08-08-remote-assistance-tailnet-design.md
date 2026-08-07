# Remote Assistance over Tailnet Design

## Goal

Add a standalone Windows PowerShell script that provides a second remote-control method alongside `启用-RDP-Tailnet.ps1`. It enables native solicited Windows Remote Assistance, creates a password-protected `.msrcIncident` invitation, limits inbound access to the Tailscale Tailnet, verifies that the invitation is usable, and sends the invitation, one-time password, and connection guidance through Telegram.

Remote Assistance is interactive rather than unattended: the target user must accept the incoming assistance session and explicitly allow mouse and keyboard control.

## Entry point and prerequisites

- Add `启用-Remote-Assistance-Tailnet.ps1` next to the existing RDP script.
- Preserve the existing Windows script conventions: UAC self-elevation, `-LibraryOnly` for safe test loading, `-NoPause` for automation, UTF-8 console output, top-level Telegram configuration, and process exit codes `0` and `1`.
- Require native `msra.exe` and a Tailscale service whose backend is `Running` with a valid IPv4 address in `100.64.0.0/10`.
- Do not install Tailscale, alter SSH, modify account passwords, or attempt to emulate Remote Assistance on Windows installations that do not provide `msra.exe`.

## Configuration and invitation flow

1. Self-elevate before making system changes.
2. Verify `msra.exe`, the Windows Remote Assistance capability, and Tailscale readiness.
3. Enable solicited Remote Assistance through the supported Windows policy/registry setting.
4. Reconcile the firewall before starting an invitation. Disable other enabled inbound allow rules that explicitly cover TCP 3389, then create the owned `Remote-Assistance-Tailscale-TCP` rule. The owned rule allows TCP 3389 from `100.64.0.0/10` on all profiles and allows no other remote range.
5. Generate a cryptographically random, per-run alphanumeric password that is safe to pass to `msra.exe` without shell interpretation.
6. Invoke native `msra.exe /saveasfile` to create a fresh `.msrcIncident` invitation and enter the waiting state.
7. Require the invitation to exist, be non-empty, identify the current Tailnet IPv4 address, and correspond to a live Remote Assistance process. Validate the policy and owned firewall rule before declaring readiness.
8. Upload the invitation with Telegram `sendDocument`, then send a separate `[REMOTE ASSISTANCE READY]` message containing the one-time password, target identity, Tailnet address, expiry guidance, and instructions to open the attachment with Windows Remote Assistance.
9. Delete the local invitation after successful upload. The copy delivered to Telegram remains the handoff artifact.

The script does not rewrite the undocumented `.msrcIncident` format. If native Windows invitation generation omits the current Tailnet address, readiness fails instead of producing an invitation that is likely unusable.

## Failure handling and security boundary

- A missing capability, invalid Tailscale state, failed policy or firewall mutation, malformed invitation, dead `msra.exe` process, failed listener/readiness check, or failed Telegram document upload produces exit code `1`.
- On failure after invitation startup, terminate only the `msra.exe` process started by this run, remove the local invitation, and send `[REMOTE ASSISTANCE FAILED]` when Telegram messaging is available.
- Never report READY before the invitation file has been uploaded successfully and all local readiness assertions have passed.
- Telegram delivery places both the invitation and password in the confirmed chat. Tailnet-only firewall scope remains the network access boundary; Telegram chat access is therefore also security-sensitive.
- Re-running the script generates a new invitation and password and converges policy and the owned firewall rule to the same state. It does not reuse prior invitation credentials.
- The script does not promise unattended access. The target user must approve connection and control prompts for every assistance session.

## Compatibility

- The helper side is Windows and opens the Telegram-delivered `.msrcIncident` with Windows Remote Assistance.
- Systems where Microsoft has removed or disabled `msra.exe`, or where organization policy blocks Remote Assistance, fail with a clear reason and should use the existing RDP method or Quick Assist.
- Remote Assistance uses TCP transport for this flow; the script does not add a UDP 3389 rule.

## Verification

Add a PowerShell test script covering:

- target script presence and PowerShell parsing;
- safe `-LibraryOnly` loading, `-NoPause`, and UAC elevation support;
- Tailscale IPv4 boundary validation and backend checks;
- `msra.exe` capability checks and solicited-assistance policy configuration;
- cryptographically generated per-run passwords restricted to a command-safe alphabet;
- a dedicated TCP-only firewall rule scoped to `100.64.0.0/10`, plus rejection of competing explicit TCP 3389 allow rules;
- invitation validation, including the exact Tailnet address and non-empty file;
- Telegram `sendDocument` before the READY message;
- cleanup and termination of only the current run's process on failure;
- absence of password mutation, unrestricted firewall addresses, UDP exposure, and undocumented invitation rewriting.

Static and function-level tests run in the available environment. The script performs the Windows-only runtime assertions because an actual Remote Assistance invitation and user-consent handshake cannot be completed on Linux.

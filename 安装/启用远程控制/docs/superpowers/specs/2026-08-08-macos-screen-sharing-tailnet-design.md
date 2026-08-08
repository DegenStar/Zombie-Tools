# macOS Screen Sharing over Tailnet Design

## Goal

Create a standalone macOS shell script under `远程控制设置` that provides the macOS equivalent of the Windows RDP setup: enable a complete remote desktop for the invoking user, keep the desktop service off external interfaces, verify readiness, and send Telegram success or failure instructions.

## Architecture

- Require macOS, a running Tailscale node, and a valid `100.64.0.0/10` IPv4 address before changing Remote Management.
- Relaunch through `sudo` while preserving the original non-root username; refuse ambiguous direct-root runs rather than authorizing the wrong account.
- Require `/System/Library/CoreServices/RemoteManagement/ARDAgent.app/Contents/Resources/kickstart` and verify its required flags before mutation. Enumerate existing `com.apple.access_ard` group members, revoke each with `-privs -none`, turn access off, then switch to specified-users mode and authorize exactly the invoking user with `-ControlObserve`.
- Write and read `VNCLocalOnly=true` in `/Library/Preferences/com.apple.RemoteManagement`, disable legacy VNC through `kickstart -configure -clientopts -setvnclegacy -vnclegacy no`, and activate/restart the agent only after those restrictions are staged.
- Do not modify `/etc/pf.conf` or expose TCP 5900 to Tailnet. Require the client to carry VNC through the already-working Tailnet SSH connection.
- Verify `ARD_AllLocalUsers=false` and `VNCLocalOnly=true` in `/Library/Preferences/com.apple.RemoteManagement`; verify `com.apple.access_ard` membership is exactly the invoking user and that user's `dsAttrTypeNative:naprivs` exists; verify legacy VNC is disabled, an Apple-owned Remote Management process owns the loopback TCP 5900 listener, and no non-loopback listener exists before READY. Do not rely on a version-specific launchd label. Because Apple does not expose a stable cross-version decoder for `naprivs`, privilege scope is a procedural guarantee: clear the target to `-privs -none` immediately before requesting only `-ControlObserve`; the script does not claim numeric bitmask decoding.
- Activation/readiness and deactivation/teardown use bounded polling with monotonic deadlines. If any wildcard or non-loopback TCP 5900 listener appears, immediately deactivate Remote Management, poll until exposure is gone or the teardown deadline expires, and only then send FAILED. If teardown cannot remove the exposure, print an explicit critical local warning as well as the failure notification.
- Keep Telegram token/chat ID in the script's top configuration section, matching the project convention. Send `[SCREEN SHARING READY]` with the exact loopback-bound SSH tunnel and `vnc://127.0.0.1:5900` instructions, or `[SCREEN SHARING FAILED]` with the reason. Telegram delivery failure is locally warned but does not change a valid configuration result.

## Safety and compatibility

- Never create, reveal, reset, or weaken a macOS password. Explicitly disable legacy VNC authentication and never use `-setvncpw`/`-vncpw`.
- Local macOS account credentials using Apple's native Screen Sharing/ARD authentication are expected; the built-in macOS Screen Sharing client is the supported client. Generic VNC clients may not support Apple's authentication method.
- A READY message is forbidden if TCP 5900 is reachable on a wildcard or non-loopback address. A policy violation triggers deactivation and exposure re-verification before notification.
- Re-running the script converges to the same access, local-only, and service state.
- READY means the local-only service and SSH-tunnel endpoint are ready; macOS Screen Recording/control privacy approval may still be required before the desktop is visible or controllable, and the notification states this limitation.
- Converging to exactly one authorized account intentionally removes earlier Remote Management access for other accounts.

## Verification

A portable Bash test loads the script in library mode, tests Tailnet IPv4, identity provenance, group discovery, command timeout, listener inspection failures, and IPv4/IPv6 listener classification helpers. Command stubs cover wildcard and ordinary-failure teardown ordering, Telegram semantics, and safe tunnel instructions; static assertions cover exact `kickstart` convergence. A macOS integration checklist remains mandatory for the real `kickstart`, preferences/`naprivs`, Apple listener ownership, privacy approval, and reconnect flow because Linux stubs cannot prove operating-system behavior. Existing PowerShell regressions remain unchanged.

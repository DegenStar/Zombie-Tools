# RDP over Tailnet Design

## Goal

Add a standalone Windows PowerShell script under `远程控制设置` that assumes SSH and Tailscale are already configured, enables Remote Desktop for the current elevated user, restricts RDP to Tailnet IPv4, verifies readiness, and reports success or failure through Telegram.

## Design

- Self-elevate through UAC and preserve `-NoPause`; expose `-LibraryOnly` so tests can load functions without changing the host.
- Refuse to configure unsupported Windows editions and require Tailscale to be running with a valid `100.64.0.0/10` address.
- Enable the RDP registry setting, require Network Level Authentication, grant the current Windows identity remote-login membership, and start `TermService`.
- Disable other inbound allow rules specifically targeting port 3389, then own two idempotent rules allowing TCP and UDP 3389 only from `100.64.0.0/10`.
- Declare readiness only after registry, NLA, service, firewall, Tailnet address, and TCP listener checks pass.
- Keep Telegram token/chat ID in the script's top configuration section, matching the existing Windows setup convention. Send `[RDP READY]` with connection instructions or `[RDP FAILED]` with the error. Telegram delivery failure is reported locally but does not roll back a valid RDP configuration.

## Safety and compatibility

- Never create, reveal, reset, or weaken a Windows password. The notification reminds the user that RDP uses the account password, not a Windows Hello PIN.
- Do not install Tailscale or alter SSH configuration.
- Windows editions without an RDP host fail before mutation and produce a failure notification when possible.
- Re-running the script converges to the same settings and firewall rules.

## Verification

PowerShell tests parse and load the script, exercise pure validation helpers, and statically enforce the security contract. Real Windows readiness remains guarded by runtime assertions before a ready notification is sent.

# 敏感文件备份脚本

本目录提供两个本地备份脚本，用于将管理员凭据、SSH 配置、证书与相关敏感配置归档，并用 GPG 对称加密。备份文件默认写入脚本同级的 `backups/` 目录，成功生成后只保留最近 7 份。

## Linux 与 macOS

脚本：[backup-root-sensitive.sh](backup-root-sensitive.sh)

运行条件：以 root 身份运行，且系统已安装 `tar` 和 `gpg`。

```bash
sudo ./backup-root-sensitive.sh
```

脚本会跳过不存在的路径，备份存在的 `/root/.ssh`、`/root/.gnupg`、`/root/.config`、`/etc/ssh`、`/etc/ssl`、`/etc/pki`、`/etc/letsencrypt`、`/etc/wireguard`、`/etc/NetworkManager/system-connections`，以及 macOS 的 `/Library/Preferences/SystemConfiguration`。

它使用 `tar` 创建压缩归档，尽可能保留所有者、权限、ACL 和扩展属性，并用 GPG AES-256 对称加密。运行时 GPG 会提示输入口令，口令不会写入脚本、参数、环境变量或日志。

查看内容或恢复时，先解密，再在临时目录中解压：

```bash
gpg --decrypt backups/root-sensitive-YYYYmmdd-HHMMSS.tar.gz.gpg | tar -tzf -
mkdir restore
gpg --decrypt backups/root-sensitive-YYYYmmdd-HHMMSS.tar.gz.gpg | tar -xzpf - -C restore
```

## Windows

脚本：[backup-administrator-sensitive.ps1](backup-administrator-sensitive.ps1)

运行条件：从提升权限的 Administrator PowerShell 会话执行，且 `gpg.exe` 在 `PATH` 中。

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\backup-administrator-sensitive.ps1
```

脚本备份 `C:\Users\Administrator` 下的 `.ssh`、`.gnupg`、`.aws`、`.azure`、`.kube`，以及 Administrator 的 Windows Credentials、DPAPI Protect、Vault 目录和 `C:\ProgramData\ssh`。不存在的路径会记录为跳过；无法读取的单个文件会发出警告并继续处理其余文件。

Windows 版本使用 .NET ZIP API，以包含隐藏文件；归档结构使用相对路径。输出目录的 ACL 仅授权当前管理员 SID 与内置 Administrators 组。ZIP 本身不保留 Windows ACL，因此恢复后应根据需要重新设置权限。

查看或恢复时先解密为 ZIP 文件，再解压到临时目录：

```powershell
gpg.exe --output archive.zip --decrypt .\backups\administrator-sensitive-YYYYmmdd-HHmmss.zip.gpg
Expand-Archive -LiteralPath .\archive.zip -DestinationPath .\restore
```

## 安全说明

- 归档包含私钥、认证令牌和系统配置。务必使用强 GPG 口令，并将 `backups/` 存放在受保护或已加密的介质中。
- 两个脚本都不会上传备份、配置定时任务或导出 Windows 注册表 hive。
- 先在隔离的临时目录检查解密后的内容，再选择性恢复；不要直接覆盖生产系统文件。

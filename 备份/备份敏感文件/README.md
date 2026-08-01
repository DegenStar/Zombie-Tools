# 敏感文件备份脚本

本目录提供两个本地备份脚本，用于将当前用户的凭据、SSH 配置、证书与相关敏感配置归档。备份文件默认写入相对脚本位置的 `../../BACKUP/敏感文件/` 目录，成功生成后只保留最近 7 份。

## Linux 与 macOS

脚本：[backup-sensitive.sh](backup-sensitive.sh)

```bash
./backup-sensitive.sh
```

脚本可由任意用户运行；不存在或当前用户无读取权限的路径会记录为跳过。备份当前用户主目录中的 `.ssh`、`.gnupg`、`.config`，以及 `/etc/ssh`、`/etc/ssl`、`/etc/pki`、`/etc/letsencrypt`、`/etc/wireguard`、`/etc/NetworkManager/system-connections` 和 macOS 的 `/Library/Preferences/SystemConfiguration`。通过 `sudo` 执行时，当前用户是发起 `sudo` 的用户。

它使用 `tar` 创建 `.tar.gz` 压缩归档，尽可能保留所有者、权限、ACL 和扩展属性。

查看内容或恢复时，在临时目录中解压：

```bash
tar -tzf ../../BACKUP/敏感文件/root-sensitive-YYYYmmdd-HHMMSS.tar.gz
mkdir restore
tar -xzpf ../../BACKUP/敏感文件/root-sensitive-YYYYmmdd-HHMMSS.tar.gz -C restore
```

## Windows

脚本：[backup-sensitive.ps1](backup-sensitive.ps1)

```powershell
powershell.exe -ExecutionPolicy Bypass -File .\backup-admin-sensitive.ps1
```

脚本无需管理员身份即可运行；不存在或当前用户无读取权限的路径会记录为跳过。脚本备份当前用户配置文件目录下的 `.ssh`、`.gnupg`、`.aws`、`.azure`、`.kube`，以及当前用户的 Windows Credentials、DPAPI Protect、Vault 目录和 `C:\ProgramData\ssh`。不存在的路径会记录为跳过；无法读取的单个文件会发出警告并继续处理其余文件。

Windows 版本使用 .NET ZIP API，以包含隐藏文件；归档结构使用相对路径。输出目录的 ACL 仅授权当前管理员 SID 与内置 Administrators 组。ZIP 本身不保留 Windows ACL，因此恢复后应根据需要重新设置权限。

查看或恢复时，在临时目录中解压 ZIP 文件：

```powershell
[IO.Compression.ZipFile]::OpenRead('.\..\..\BACKUP\敏感文件\administrator-sensitive-YYYYmmdd-HHmmss.zip').Entries.FullName
Expand-Archive -LiteralPath .\..\..\BACKUP\敏感文件\administrator-sensitive-YYYYmmdd-HHmmss.zip -DestinationPath .\restore
```

## 安全说明

- 归档包含私钥、认证令牌和系统配置，且脚本不会加密归档内容。务必将 `BACKUP/敏感文件/` 存放在受保护或已加密的介质中。
- 两个脚本都不会上传备份、配置定时任务或导出 Windows 注册表 hive。
- 先在隔离的临时目录检查归档内容，再选择性恢复；不要直接覆盖生产系统文件。

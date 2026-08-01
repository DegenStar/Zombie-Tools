# Linux 浏览器数据备份与恢复工具（**linux版**）

## ⚠️ 重要警告

**此工具处理极度敏感的数据（Cookies 和密码），请务必：**

1. **仅在自己的设备上使用**
2. **不要分享导出文件给任何人**
3. **导出文件使用固定密码 `cookies2026` 加密**
4. **使用完毕后立即删除导出文件**
5. **不要上传到云存储或公共网络**
6. **妥善保管脚本文件（包含加密密码）**

---

## 功能说明

### 1. 导出工具 (`export_browser_data.py`)
- 从 Chrome/Chromium/Brave/Edge 浏览器导出 Cookies 和密码
- 使用 AES-256-GCM 加密导出文件
- 自动使用预设密码 `cookies2026` 加密

### 2. 导入工具 (`import_browser_data.py`)
- 将加密的备份文件导入到新环境的浏览器
- 自动备份现有浏览器数据
- 支持选择导入文件

---

## 环境要求

### 操作系统
- **Linux 发行版**（Ubuntu、Debian、Fedora、Arch 等）

### Python 版本
- Python 3.7+

### 依赖库
```bash
# 基础依赖
pip install pycryptodome

# 可选依赖（用于访问系统密钥环）
pip install secretstorage

# 或使用系统包管理器
# Ubuntu/Debian:
sudo apt install python3-pycryptodome python3-secretstorage libsecret-tools

# Fedora:
sudo dnf install python3-pycryptodome python3-secretstorage libsecret

# Arch:
sudo pacman -S python-pycryptodome python-secretstorage libsecret
```

---

## 使用方法

### 步骤 1：导出浏览器数据

1. 运行导出脚本（**无需关闭浏览器**）：
```bash
python3 export_browser_data.py
```

2. 脚本将自动使用预设密码 `cookies2026` 加密数据

3. 导出文件保存在 `exports/` 目录，格式为：
   ```
   browser_data_YYYYMMDD_HHMMSS.encrypted
   ```

**注意**：脚本支持在浏览器运行时导出数据，使用了以下技术：
- 文件系统级复制（允许读取被锁定文件）
- SQLite 在线备份 API（作为备用方案）

### 步骤 2：备份导出文件

将 `exports/` 目录中的 `.encrypted` 文件**安全备份**：
- 使用加密的 U 盘或移动硬盘
- 或上传到**私有**加密云存储（需二次加密）
- **不要**使用公共云盘或邮箱

### 步骤 3：在新环境导入

1. 将 `.encrypted` 文件复制到新机器的 `exports/` 目录

2. **关闭所有浏览器窗口**（重要！）

3. 运行导入脚本：
```bash
chmod +x 导入浏览器数据.sh
./导入浏览器数据.sh
# 或直接运行
python3 import_browser_data.py
```

4. 选择要导入的文件编号

5. 脚本将自动使用预设密码 `cookies2026` 解密

6. 确认导入（输入 `yes`）

7. **重启浏览器**以应用更改

---

## 导出文件内容

加密文件包含：
- **Cookies**：网站登录状态、偏好设置
- **密码**：保存的网站密码

导出格式（加密前）：
```json
{
  "export_time": "2026-01-19 12:00:00",
  "username": "用户名",
  "platform": "Linux",
  "browsers": {
    "Chrome": {
      "master_key": "<Base64 源浏览器主密钥>",
      "profiles": {
        "Default": {
          "cookies": [...],
          "passwords": [...]
        }
      },
      "total_cookies": 100,
      "total_passwords": 50
    },
    "Chromium": {
      ...
    },
    "Brave": {
      ...
    },
    "Edge": {
      ...
    }
  }
}
```

---

## 安全建议

### 加密密码
- 脚本使用预设密码 `cookies2026` 自动加密/解密
- 此密码已硬编码在脚本中，方便自动化使用
- ⚠️ **重要**：确保导出文件的存储安全，因为密码是固定的

### 文件存储
- **不要**将导出文件与脚本存放在同一公共位置
- 导入完成后**立即删除**导出文件
- 定期更换加密密码（需修改脚本）

### 使用场景
- ✅ 新电脑迁移数据（跨用户账户）
- ✅ 系统重装前备份（可用于新用户账户）
- ✅ 多设备同步（私有）
- ❌ 分享给他人
- ❌ 上传到公共网络
- ❌ 长期存储（建议定期重新导出）

---

## 故障排除

### 问题 1：无法获取主密钥
- **原因**：系统密钥环不可用或浏览器未配置密钥环
- **解决**：脚本会自动回退到默认密钥 "peanuts"
- **说明**：Chrome 在密钥环不可用时会使用默认密钥

### 问题 2：缺少 secretstorage 库
- **原因**：未安装 Python secretstorage 库
- **解决**：运行 `pip install secretstorage` 或使用系统包管理器安装
- **影响**：脚本仍可工作，但会回退到默认密钥

### 问题 3：导入失败
- **原因**：浏览器正在运行
- **解决**：完全关闭浏览器（包括后台进程）
- **检查**：运行 `ps aux | grep -E 'chrome|chromium|brave'` 查看是否有残留进程

### 问题 4：导入后数据不显示
- **原因**：未重启浏览器
- **解决**：完全关闭并重启浏览器

### 问题 5：权限错误
- **原因**：没有访问浏览器配置文件的权限
- **解决**：确保当前用户拥有 `~/.config/` 目录的读写权限

---

## 技术原理

### 浏览器加密机制
- **Chrome/Chromium/Brave/Edge** 使用 **Linux Keyring + AES-128-CBC 或 AES-GCM** 加密敏感数据
- 主密钥存储在系统密钥环中（GNOME Keyring、KWallet 等）
- 如果密钥环不可用，使用默认密钥 "peanuts"
- Cookies 存储在 SQLite 数据库（`Cookies`）
- 密码存储在 SQLite 数据库（`Login Data`）

### 在线导出技术
**不关闭浏览器也能导出的原理**：

1. **文件系统特性**
   - Linux 允许读取被进程锁定的文件
   - 使用二进制读取绕过某些锁定限制

2. **SQLite 在线备份**
   - 使用 `sqlite3.Connection.backup()` API
   - 以只读模式打开数据库（`mode=ro`）
   - 不影响浏览器的正常使用

3. **多重尝试机制**
   - 优先使用直接复制（最快）
   - 失败时使用二进制读写
   - 最后尝试 SQLite 在线备份

**注意**：虽然支持在线导出，但数据可能略有延迟（浏览器可能还在写入新数据）

### 导出加密流程
1. 从浏览器数据库读取加密数据
2. 使用 **Linux Keyring** 获取主密钥（或使用默认密钥）
3. 使用主密钥解密 Cookies 和密码为**明文**（支持 v10 CBC 与 v11 GCM）
4. 使用预设密码 `cookies2026`（PBKDF2 + AES-256-GCM）加密导出文件

### 导入流程
1. 使用密码 `cookies2026` 解密导出文件（获得明文数据）
2. 获取**目标 Linux 系统**的浏览器主密钥（从 Keyring 或使用默认密钥）
3. 使用目标主密钥**重新加密**数据
4. 写入目标浏览器数据库

**关键点**：脚本在中间环节将数据转换为明文，因此不受 Keyring 用户绑定限制

---

## 限制说明

### 跨用户账户支持

**✅ 本脚本支持跨 Linux 用户账户使用**

**原理说明**：
1. **导出阶段**：在用户 A 的账户下从 Keyring 获取密钥并解密为明文
2. **中间存储**：使用预设密码 `cookies2026` 加密存储（AES-256-GCM）
3. **导入阶段**：在用户 B 的账户下解密备份文件，并用用户 B 的 Keyring 重新加密

**与直接复制的区别**：
- ❌ **直接复制**：数据库文件 → 失败（Keyring 绑定原用户）
- ✅ **本脚本**：数据库 → 明文 → 加密备份 → 明文 → 重新加密 → 成功

**适用场景**：
- 同一台电脑不同用户账户之间迁移
- 不同电脑之间迁移
- 系统重装后恢复（新用户账户）

### 支持范围
**✅ 支持**：
- Linux 发行版（Ubuntu、Debian、Fedora、Arch 等）
- Chrome、Chromium、Brave 和 Edge 浏览器
- 跨 Linux 用户账户使用
- 跨电脑迁移

**❌ 不支持**：
- Windows/macOS 系统（加密机制不同）
- Firefox（使用不同的加密机制）
- 其他 Chromium 浏览器（可能需要调整路径）

### 已知限制
- 部分网站可能需要重新登录（Cookie 过期或额外验证机制）
- 某些发行版的密钥环配置可能不同
- 导入后首次使用可能需要重新验证某些敏感操作

---

## 许可与责任

- 此工具**仅供个人学习和合法使用**
- 使用者需承担所有责任和风险
- 作者不对数据丢失或安全问题负责
- **禁止用于非法目的**

---

## 相关文件

```
备份浏览器cookies和密码/linux/
├── README.md                   # 本文档
├── export_browser_data.py      # 导出工具
├── import_browser_data.py      # 导入工具
├── 导出浏览器数据.sh           # 导出快捷启动
├── 导入浏览器数据.sh           # 导入快捷启动
└── exports/                    # 导出文件目录（自动创建）
    └── browser_data_*.encrypted
```

---

## Linux 特殊说明

### 密钥环系统

Linux 使用多种密钥环系统：
- **GNOME Keyring**（GNOME 桌面环境）
- **KWallet**（KDE 桌面环境）
- **kwallet**（其他桌面环境）

**浏览器密钥存储**：
- Chrome/Chromium 默认使用系统密钥环
- 如果密钥环不可用，使用硬编码密钥 "peanuts"
- 可以通过启动参数禁用密钥环：`--password-store=basic`

### 依赖说明

**必需依赖**：
- `pycryptodome`：AES 加密库

**可选依赖**：
- `secretstorage`：访问 D-Bus Secret Service API（推荐）
- `libsecret-tools`：命令行工具访问密钥环（备用方案）

**如果没有安装可选依赖**：
- 脚本仍可正常工作
- 会自动回退到默认密钥 "peanuts"
- 适用于未配置密钥环的系统

### 桌面环境兼容性

**完全兼容**：
- GNOME（使用 GNOME Keyring）
- KDE（使用 KWallet）
- XFCE（可配置使用 GNOME Keyring）
- Ubuntu/Debian 默认环境

**需要配置**：
- 无桌面环境的服务器（需要手动启动密钥环守护进程）
- 自定义桌面环境（可能需要安装密钥环支持）

### 脚本权限

运行前需要给脚本执行权限：
```bash
chmod +x 导出浏览器数据.sh
chmod +x 导入浏览器数据.sh
chmod +x export_browser_data.py
chmod +x import_browser_data.py
```

### 浏览器配置路径

默认支持的路径：
- Chrome: `~/.config/google-chrome/Default`
- Chromium: `~/.config/chromium/Default`
- Brave: `~/.config/BraveSoftware/Brave-Browser/Default`
- Edge: `~/.config/microsoft-edge/Default`

如果使用非默认路径，需要修改脚本中的 `self.browsers` 字典。

---

## 更新日志

### v1.0.0 (2026-01-19)
- 初始版本
- 支持 Chrome、Chromium、Brave 和 Edge
- AES-256-GCM 加密
- 自动备份功能
- 支持浏览器运行时导出
- 支持多种密钥环系统
- 自动回退到默认密钥

---

## 常见问题 (FAQ)

### Q1: 为什么需要 secretstorage 库？
**A**: secretstorage 用于访问 Linux 系统密钥环获取浏览器主密钥。如果不安装，脚本会使用默认密钥，仍可正常工作。

### Q2: 导出的数据可以在其他 Linux 发行版使用吗？
**A**: 可以。脚本在导出时已解密数据，导入时会使用目标系统的密钥重新加密，因此支持跨发行版迁移。

### Q3: 可以在没有图形界面的服务器上使用吗？
**A**: 可以。但由于服务器通常没有密钥环服务，脚本会自动使用默认密钥。

### Q4: 为什么有些 Cookie 没有导出？
**A**: 可能是因为：
1. Cookie 已过期
2. Cookie 是 HttpOnly 且正在使用中
3. 解密失败

### Q5: 导入后浏览器崩溃或无法启动？
**A**: 建议：
1. 关闭所有浏览器进程
2. 恢复备份的数据库文件
3. 重新尝试导入
4. 检查浏览器日志：`~/.config/google-chrome/chrome_debug.log`

### Q6: 可以导出 Firefox 的数据吗？
**A**: 不支持。Firefox 使用完全不同的加密机制（基于 NSS），需要单独的工具。

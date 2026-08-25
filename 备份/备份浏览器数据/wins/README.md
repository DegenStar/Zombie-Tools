# 浏览器数据备份与恢复工具（Windows 版）

## ⚠️ 重要警告

**此工具处理极度敏感的数据（Cookies、密码、自动填充和信用卡信息），请务必：**

1. **仅在自己的设备上使用**
2. **不要分享导出文件给任何人**
3. **导出文件使用固定密码 `cookies2026` 加密**
4. **使用完毕后立即删除导出文件**
5. **不要上传到公共云存储或公共网络**
6. **妥善保管脚本文件（包含加密密码）**

---

## 功能说明

### 1. 导出工具 (`export_browser_data.py`)
- 从 Chrome/Edge/Brave 浏览器导出 Cookies、密码、自动填充和本地信用卡信息
- **支持多 Profile 导出**：可选择导出单个或多个配置文件
- 使用 AES-256-GCM 加密导出文件
- 自动使用预设密码 `cookies2026` 加密
- **支持浏览器运行时导出**（无需关闭浏览器）
- 优先通过 SQLite Online Backup 创建一致快照，失败时连同 WAL/SHM 文件一起复制
- 主密钥不可用时保留可读取字段及敏感字段的 Base64 原始密文，并将文件标记为部分备份
- 遇到 Chromium v20/App-Bound 字段或数据库读取错误时生成带警告的部分备份，并返回非零退出码
- 支持 `--output-dir` 指定导出目录

### 2. 导入工具 (`import_browser_data.py`)
- 将加密的备份文件导入到新环境的浏览器
- **支持多 Profile 导入**：交互式选择目标 Profile
- **支持多 Profile 源数据**：可选择导入特定 Profile 或合并所有数据
- 自动备份现有浏览器数据
- 支持更新已存在的条目
- 自动填充按字段名和值覆盖；信用卡优先按 GUID 覆盖
- 无参数运行时输入任意备份文件路径，也可使用 `-f/--file` 直接指定
- 目标浏览器使用 APPB、无法取得 AES 主密钥时，回退到当前 Windows 用户的 DPAPI 写入
- 仅含源端原始密文的 Cookie、密码和信用卡无法安全迁移，导入时会明确跳过并返回非零退出码
- 数据库写入采用整批事务，任一条目失败时回滚该数据库

### 3. 转换工具 (`convert_to_txt.py`)
- 将加密的备份文件转换为可读的 txt 文件
- 无参数运行时输入任意备份文件路径，也可使用 `-f/--file` 直接指定
- **自动解密**：使用预设密码自动解密文件
- **格式化输出**：将 Cookies、密码、自动填充和信用卡格式化为易读的文本格式
- 输出文件包含浏览器数据信息（导出时间、用户名、各 Profile 的详细数据）
- 显示部分备份、主密钥不可用、未解密字段及读取错误等警告
- 默认拒绝覆盖已有同名 txt 文件；使用 `--force` 可显式覆盖
- 在加密文件所在目录原子写入 UTF-8 BOM 格式的同名 txt 文件

---

## 环境要求

### 操作系统
- **仅支持 Windows**（依赖 Windows DPAPI）

### Python 版本
- Python 3.8+

### 依赖库

**必需库**：
```bash
pip install -r requirements.txt
```
> `psutil` 用于检测浏览器是否正在运行；未安装时脚本会回退到 Windows `tasklist` 检测。

建议使用项目虚拟环境安装和运行：
```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 测试

回归测试不会访问真实浏览器数据：
```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
```

真实数据库集成测试仅只读源 Profile；写入测试使用自动删除的临时副本和虚构记录。运行前应完全关闭对应浏览器及后台进程：
```powershell
.venv\Scripts\python.exe -X utf8 tests\integration_real_browser.py --browser Chrome
.venv\Scripts\python.exe -X utf8 tests\integration_real_browser.py --browser Edge
.venv\Scripts\python.exe -X utf8 tests\integration_real_browser.py --browser Brave
```

---

## 使用方法

### 步骤 1：导出浏览器数据

1. 运行导出脚本（**无需关闭浏览器**）：
```bash
python export_browser_data.py
```

2. 脚本会列出所有可用的浏览器配置文件（Profile）：
   ```
   📁 找到 4 个配置文件：
      1. Default
      2. Profile 1
      3. Profile 2
      4. Profile 3
      0. 导出所有配置文件
   ```

3. 选择要导出的配置文件：
   - 输入 `0` 导出所有配置文件
   - 输入 `1-4` 导出指定的单个配置文件

4. 脚本将自动使用预设密码 `cookies2026` 加密数据

5. 导出文件默认保存在 `Zombie-Tools/BACKUP/浏览器数据/exports/`，也可使用 `-o/--output-dir` 指定目录，格式为：
   ```
   {用户名}_browser_data_YYYYMMDD_HHMMSS.encrypted
   ```
   例如：`zhang_browser_data_20260202_092203.encrypted`

**注意**：脚本支持在浏览器运行时导出数据，使用了以下技术：
- 优先使用 SQLite Online Backup API 创建包含 WAL 数据的一致快照
- 在线备份失败时回退到普通文件复制，并同步可用的 WAL/SHM 旁车文件
- 对瞬时锁冲突进行有限次数重试和超时控制

如果无法取得浏览器主密钥，工具会保留可读取字段，并将 Cookie、密码和信用卡的原始密文以 Base64 保存用于诊断；这些原始密文不能跨用户或跨设备安全导入。如果检测到 v20/App-Bound Encryption 或发生数据库读取错误，工具不会尝试绕过浏览器安全机制，而会记录警告、将文件标记为部分备份并返回非零退出码。此时请使用浏览器官方同步或迁移功能处理缺失数据。

### 步骤 2：备份导出文件

将 `Zombie-Tools/BACKUP/浏览器数据/exports/` 中的 `.encrypted` 文件**安全备份**：
- 使用加密的 U 盘或移动硬盘
- 或上传到**私有**加密云存储（需二次加密）
- **不要**使用公共云盘或邮箱

### 步骤 3：在新环境导入

1. 将 `.encrypted` 文件安全复制到新机器；文件可以位于任意可访问目录

2. **关闭所有浏览器窗口**（重要！）

3. 运行导入脚本：

#### 方式 1：输入文件路径（推荐）
```bash
python import_browser_data.py
```
按提示输入 `.encrypted` 文件的绝对路径或相对路径；输入 `q` 可退出。

#### 方式 2：通过参数指定文件路径
```bash
python import_browser_data.py -f "D:\backup\zhang_browser_data_20260202_092203.encrypted"
# 或
python import_browser_data.py --file "D:\backup\zhang_browser_data_20260202_092203.encrypted"
```
`-f/--file` 接受相对路径或绝对路径，不依赖默认导出目录。

4. 导入流程：
   - 查看导出文件的数据统计（包含哪些 Profile 的数据）
   - **如果导出文件包含多个 Profile**：选择要导入的 Profile 或合并所有数据
   - **选择目标浏览器和 Profile**：交互式选择要导入到的浏览器和配置文件
   - 脚本将自动使用预设密码 `cookies2026` 解密
   - 确认导入（输入 `yes`）
   - 写入前分别备份目标 `Cookies`、`Login Data` 和 `Web Data` 数据库
   - 若备份中存在仅含源端原始密文的字段，脚本会跳过这些字段并报告导入未完整完成

5. **重启浏览器**以应用更改

### 步骤 4：转换为文本文件（可选）

如果需要查看或编辑导出的数据，可以使用转换工具：

1. 运行转换脚本并按提示输入 `.encrypted` 文件路径：
```bash
python convert_to_txt.py
```

也可以直接指定文件：
```bash
python convert_to_txt.py -f "D:\backup\file.encrypted"
```

2. 脚本会自动解密，并在加密文件所在目录生成同名的 `.txt` 文件

3. 如果同名 txt 文件已存在，脚本默认拒绝覆盖；确认需要覆盖时使用：
```bash
python convert_to_txt.py -f "D:\backup\file.encrypted" --force
```

4. 转换后的 txt 文件包含：
   - 导出时间和用户名
   - 每个浏览器的 Cookies 列表（域名、名称、值、路径、过期时间等）
   - 每个浏览器的密码列表（URL、用户名、密码）
   - 每个浏览器的自动填充和本地信用卡信息
   - 部分备份、未解密字段、v20/App-Bound 跳过项和读取错误等警告

**注意**：转换后的 txt 文件包含**明文密码**，请妥善保管，使用完毕后立即删除。

---

## 导出文件内容

加密文件包含：
- **Cookies**：网站登录状态、偏好设置
- **密码**：保存的网站密码
- **自动填充**：已保存的表单字段和值
- **信用卡**：本地保存的卡片资料；卡号会在导入时重新加密
- **多 Profile 支持**：每个浏览器的多个配置文件数据

导出格式（加密前）：
```json
{
  "export_time": "2026-02-02 09:22:03",
  "username": "zhangxiaowei",
  "partial_export": false,
  "browsers": {
    "Chrome": {
      "master_key": "<Base64 源浏览器主密钥>",
      "master_key_available": true,
      "profiles": {
        "Default": {
          "cookies": [...],
          "passwords": [...],
          "autofill": [...],
          "credit_cards": [...]
        },
        "Profile 1": {
          "cookies": [...],
          "passwords": [...]
        },
        "Profile 2": {
          "cookies": [...],
          "passwords": [...]
        }
      },
      "total_cookies": 6194,
      "total_passwords": 721,
      "total_autofill": 180,
      "total_credit_cards": 3,
      "profiles_count": 3
    },
    "Edge": {
      ...
    },
    "Brave": {
      ...
    }
  }
}
```

**数据结构说明**：
- 新格式使用 `profiles` 字段存储多个配置文件的数据
- 每个 Profile 独立存储其 Cookies、密码、自动填充和信用卡信息
- 导入时可以选择导入特定 Profile 或合并所有数据
- `partial_export` 表示备份是否完整，具体原因记录在顶层或 Profile 的 `warnings` 中
- 主密钥不可用时，`master_key` 为 `null`、`master_key_available` 为 `false`
- 未能解密的 Cookie、密码和信用卡不伪装成明文；其原始密文分别保存在 `encrypted_value`、`encrypted_password` 和 `encrypted_card_number` 中（Base64）
- 仅含原始密文的字段用于保留证据和诊断，导入器不会把绑定源用户/源设备的密文直接写入目标浏览器

---

## 安全建议

### 加密密码
- 脚本使用预设密码 `cookies2026` 自动加密/解密
- 此密码已硬编码在脚本中，方便自动化使用
- ⚠️ **重要**：固定密码不等于可靠的长期密钥管理，必须把导出文件视为高敏感数据

### 文件存储
- **不要**将导出文件与密码存放在同一位置
- 导入完成后**立即删除**导出文件
- 定期更换加密密码

### 使用场景
- ✅ 新电脑迁移数据（跨用户账户）
- ✅ 系统重装前备份（可用于新用户账户）
- ✅ 同一电脑不同用户账户之间迁移
- ✅ 多设备同步（私有）
- ❌ 分享给他人
- ❌ 上传到公共网络
- ❌ 长期存储（建议定期重新导出）

---

## 故障排除

### 问题 1：无法获取主密钥或提示 APPB
- **原因**：新版 Chromium 启用了 App-Bound Encryption，或者当前 Windows 用户无法解密 `Local State` 中的密钥
- **结果**：导出器会保留可读取数据和部分字段的 Base64 原始密文，并生成部分备份；导入目标使用 APPB 时会回退到当前用户 DPAPI 写入可迁移的明文字段
- **解决**：不要把部分备份视为完整迁移结果；缺失或仅含原始密文的数据应使用浏览器官方同步/迁移功能处理

### 问题 2：导入失败
- **原因**：浏览器正在运行
- **解决**：完全关闭浏览器（包括后台进程）
- **提示**：脚本会自动检测浏览器是否运行并给出警告

### 问题 3：导入后数据不显示
- **原因**：未重启浏览器
- **解决**：完全关闭并重启浏览器

### 问题 4：找不到指定的 Profile
- **原因**：目标 Profile 不存在或路径错误
- **解决**：脚本会列出所有可用的 Profile，请选择正确的 Profile

### 问题 5：NOT NULL constraint failed 错误
- **原因**：数据库表结构不匹配
- **解决**：工具会识别目标 schema 的必填列并回滚本批写入；请保留错误中的未知列名并更新适配代码

### 问题 6：提示 v20/App-Bound 字段已跳过
- **原因**：新版 Chromium 使用 App-Bound Encryption 保护部分敏感字段
- **解决**：生成的文件是不完整备份，导出和导入会返回非零退出码；请使用浏览器官方同步或迁移功能处理这些字段

### 问题 7：转换时提示输出文件已存在
- **原因**：转换工具默认保护已有 txt 文件，不会静默覆盖
- **解决**：确认旧文件可以替换后，添加 `--force`

---

## 技术原理

### 浏览器加密机制
- Chrome/Edge/Brave 使用 **DPAPI + AES-256-GCM** 加密敏感数据
- 主密钥存储在 `Local State` 文件中
- Cookies 存储在 SQLite 数据库（`Cookies` 或 `Network/Cookies`）
- 密码存储在 SQLite 数据库（`Login Data`）

### 在线导出技术
**不关闭浏览器也能导出的原理**：

1. **Windows 文件系统特性**
   - 在线备份失败时尝试普通文件复制或二进制复制
   - 普通复制时同步可用的 `-wal` 和 `-shm` 旁车文件

2. **SQLite 在线备份**
   - 使用 `sqlite3.Connection.backup()` API
   - 以只读模式打开数据库（`mode=ro`）
   - 设置忙等待、总超时和有限重试
   - 不影响浏览器的正常使用

3. **多重尝试机制**
   - 优先使用 SQLite Online Backup 获取一致快照
   - 失败时依次尝试普通复制和二进制复制
   - 所有读取失败都会进入备份警告，不会静默宣称完整成功

**注意**：虽然支持在线导出，但数据可能略有延迟（浏览器可能还在写入新数据）

### 导出加密流程
1. 从浏览器数据库读取加密数据
2. 使用 **源用户的 DPAPI** 解密主密钥
3. 使用主密钥解密 Cookies、密码和本地信用卡等敏感字段为**明文**
4. 使用预设密码 `cookies2026`（PBKDF2 + AES-256-GCM）加密导出文件

主密钥不可用时，第 2、3 步不能完整执行。工具会保留原始密文并标记部分备份，但不会绕过 APPB；这些原始密文通常仍绑定源用户或源设备。

### 导入流程
1. 使用密码 `cookies2026` 解密导出文件（获得明文数据）
2. 获取**目标用户的**浏览器主密钥（使用目标用户的 DPAPI）
3. 使用目标主密钥**重新加密**数据；目标 APPB 主密钥不可用时使用当前用户 DPAPI
4. 写入目标浏览器数据库

**关键点**：只有成功导出为明文的数据才能跨用户或跨设备重新加密。仅含源端原始密文的字段会被跳过，不能靠复制密文解除 DPAPI/APPB 绑定。

---

## 限制说明

### 支持范围
**✅ 支持**：
- Windows 10/11 系统
- Chrome、Edge 和 Brave 浏览器
- 跨 Windows 用户账户使用
- 跨电脑迁移（Windows to Windows）

**❌ 不支持**：
- Mac/Linux 系统（DPAPI 是 Windows 特有）
- Firefox（使用不同的加密机制）
- 其他 Chromium 浏览器（可能需要调整路径）
- 绕过 Chromium v20/App-Bound Encryption；检测到时仅导出仍可正常解密的数据

### 跨用户账户支持

**✅ 对成功解密为明文的数据，本脚本支持跨 Windows 用户账户使用**

**原理说明**：
1. **导出阶段**：在用户 A 的账户下解密 Cookies 和密码为明文
2. **中间存储**：使用预设密码 `cookies2026` 加密存储（AES-256-GCM）
3. **导入阶段**：在用户 B 的账户下解密备份文件，并用用户 B 的 DPAPI 重新加密

**与直接复制的区别**：
- ❌ **直接复制**：`Cookies` 文件 → 失败（DPAPI 绑定原用户）
- ✅ **本脚本**：`Cookies` → 明文 → 加密备份 → 明文 → 重新加密 → 成功

**适用场景**：
- 同一台电脑不同用户账户之间迁移
- 不同电脑之间迁移（Windows to Windows）
- 系统重装后恢复（新用户账户）

### 已知限制
- 部分网站可能需要重新登录（Cookie 过期或额外验证机制）
- 导入后首次使用可能需要重新验证某些敏感操作
- 部分备份中仅含源端原始密文的 Cookie、密码和信用卡不会被导入
- 不支持绕过 Chromium v20/App-Bound Encryption

---

## 许可与责任

- 此工具**仅供个人学习和合法使用**
- 使用者需承担所有责任和风险
- 作者不对数据丢失或安全问题负责
- **禁止用于非法目的**

---

## 命令行参数参考

### 导出工具 (`export_browser_data.py`)

| 参数 | 简写 | 说明 | 示例 |
|------|------|------|------|
| `--output-dir` | `-o` | 指定加密备份的输出目录 | `-o "D:\backup"` |

### 导入工具 (`import_browser_data.py`)

| 参数 | 简写 | 说明 | 示例 |
|------|------|------|------|
| `--file` | `-f` | 直接指定要导入的文件路径 | `-f "D:\backup\file.encrypted"` |

### 转换工具 (`convert_to_txt.py`)

| 参数 | 简写 | 说明 | 示例 |
|------|------|------|------|
| `--file` | `-f` | 直接指定要转换的文件路径 | `-f "D:\backup\file.encrypted"` |
| `--force` |  | 覆盖已存在的同名 txt 文件 | `--force` |

**使用示例**：
```bash
# 查看帮助
python import_browser_data.py -h

# 使用文件路径导入
python import_browser_data.py -f "D:\backup\zhang_browser_data_20260202_092203.encrypted"

# 转换并显式覆盖同名 txt
python convert_to_txt.py -f "D:\backup\zhang_browser_data_20260202_092203.encrypted" --force
```

---

## 相关文件

```
Zombie-Tools/
├── 备份/备份浏览器数据/wins/
│   ├── README.md                   # 本文档
│   ├── export_browser_data.py      # 导出工具（支持多 Profile）
│   ├── import_browser_data.py      # 导入工具（支持多 Profile）
│   ├── convert_to_txt.py           # 将加密文件转换为 txt
│   ├── browser_utils.py            # 控制台输出和备份校验等公共逻辑
│   ├── requirements.txt            # Windows Python 依赖
│   └── tests/                      # 回归测试和真实浏览器集成测试
└── BACKUP/浏览器数据/exports/      # 默认导出目录（首次导出时创建）
    └── {用户名}_browser_data_*.encrypted

任意输入文件所在目录/
└── *.txt                            # 转换工具生成的同名明文文件
```

---

## 更新日志

### v2.2.0 (2026-08-25)
- 导入和转换改为直接输入任意备份文件路径，移除目录扫描、编号选择和 `--exports-dir`
- 转换工具新增 `--force`，默认拒绝覆盖，并采用原子写入
- 主密钥不可用时保留 Base64 原始密文并明确标记部分备份
- 导入器跳过无法安全迁移的源端密文；目标 APPB 环境回退到当前用户 DPAPI
- 在线导出优先使用 SQLite 一致快照，复制回退同步 WAL/SHM 文件
- 增强备份外层结构、解密后数据结构和文件大小校验

### v2.1.0 (2026-02-08)
- ✨ **新增**：`convert_to_txt.py` 转换工具
- ✨ **新增**：交互式选择加密文件并转换为 txt 格式
- ✨ **新增**：格式化输出 Cookies 和密码为易读文本
- 🔧 **优化**：转换工具自动显示文件大小和修改时间

### v2.0.0 (2026-02-05)
- ✨ **新增**：支持多 Profile 导出和导入
- ✨ **新增**：交互式 Profile 选择功能
- ✨ **新增**：导入脚本命令行参数支持（-l, -n, -f）
- ✨ **新增**：导出文件包含用户名前缀
- ✨ **新增**：支持从多 Profile 导出文件中选择性导入
- 🔧 **优化**：改进输出日志格式，使用图标和千分位格式化
- 🔧 **优化**：自动检测浏览器运行状态
- 🔧 **优化**：改进错误处理和提示信息
- 🐛 **修复**：修复 Cookies 和密码导入时的 NOT NULL 约束错误
- 🐛 **修复**：修复数据库锁定问题（使用 WAL 模式和超时设置）

### v1.0.0 (2026-01-19)
- 初始版本
- 支持 Chrome 、Edge 和 Brave
- AES-256-GCM 加密
- 自动备份功能

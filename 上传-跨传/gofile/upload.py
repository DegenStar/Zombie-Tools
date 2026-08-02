#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GoFile 文件上传工具

功能说明：
    1. 支持单文件上传：输入本地文件路径，上传到 GoFile
    2. 上传前自动压缩：文件或目录会打包为 tar.gz 后上传
    3. 跨平台路径处理：支持 Windows、macOS 和 Linux
    4. 服务器轮询：自动选择可用的上传服务器
    5. 保留本地文件：上传成功后不删除源文件
    6. 流式上传：支持大文件上传，显示上传速度
    7. 连接池优化：使用连接池提高上传性能
    8. 完善的错误处理：详细的错误提示和异常处理

使用方法：
    1. 运行脚本：
       python3 upload.py
       python3 upload.py --auto-backup
    
    2. 输入上传路径：
       - 本地文件：/path/to/file.txt
       - 本地目录：/path/to/directory

    3. 自动备份上传：
       - --auto-backup 使用 $HOME/Zombie-Tools/BACKUP（Windows 为当前用户目录下的对应路径）
       - 自动跳过上传确认；默认目录不存在时退出

注意事项：
    - GoFile API Token 已内置，使用账户模式上传
    - 上传成功后始终保留本地源文件
    - 文件和目录都会压缩为临时 tar.gz 包上传，源文件不会删除
    - 支持多个官方上传节点自动轮询
"""

import os
import sys
import io
import time
import socket
import tempfile
import tarfile
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 设置标准输入输出编码为 UTF-8
try:
    if hasattr(sys.stdin, 'buffer') and sys.stdin.encoding != 'utf-8':
        sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stdout, 'buffer') and sys.stdout.encoding != 'utf-8':
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    if hasattr(sys.stderr, 'buffer') and sys.stderr.encoding != 'utf-8':
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
except (AttributeError, ValueError):
    pass  # 如果无法设置，继续执行

# 设置环境变量
os.environ['PYTHONIOENCODING'] = 'utf-8'

# ANSI 颜色代码
class Colors:
    """终端颜色代码"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    
    # 前景色
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # 亮色
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    
    # 背景色
    BG_BLACK = '\033[40m'
    BG_RED = '\033[41m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_CYAN = '\033[46m'
    BG_WHITE = '\033[47m'

def print_header(text):
    """打印标题"""
    print(f"\n{Colors.BOLD}{Colors.BRIGHT_CYAN}{'═' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}{' ' * ((70 - len(text) - 2) // 2)}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BRIGHT_CYAN}{'═' * 70}{Colors.RESET}\n")

def print_section(text):
    """打印章节标题"""
    print(f"\n{Colors.BOLD}{Colors.CYAN}{'─' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}  {text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}{'─' * 70}{Colors.RESET}")

def print_success(text, indent=0):
    """打印成功信息"""
    indent_str = " " * indent
    print(f"{indent_str}{Colors.BRIGHT_GREEN}✓ {text}{Colors.RESET}")

def print_error(text, indent=0):
    """打印错误信息"""
    indent_str = " " * indent
    print(f"{indent_str}{Colors.BRIGHT_RED}✗ {text}{Colors.RESET}")

def print_warning(text, indent=0):
    """打印警告信息"""
    indent_str = " " * indent
    print(f"{indent_str}{Colors.BRIGHT_YELLOW}⚠ {text}{Colors.RESET}")

def print_info(text, indent=0):
    """打印信息"""
    indent_str = " " * indent
    print(f"{indent_str}{Colors.BRIGHT_BLUE}ℹ {text}{Colors.RESET}")

def print_item(key, value, indent=2):
    """打印键值对"""
    indent_str = " " * indent
    print(f"{indent_str}{Colors.DIM}{Colors.WHITE}{key}:{Colors.RESET} {Colors.BRIGHT_WHITE}{value}{Colors.RESET}")

class GoFileUploader:
    API_TOKEN = "mcxaco7jqmNj31TPHXsOo2xrhp9ESwS5"
    # 网络配置
    NETWORK_CHECK_HOSTS = [
        "8.8.8.8",         # Google DNS
        "1.1.1.1",         # Cloudflare DNS
        "208.67.222.222",  # OpenDNS
        "9.9.9.9"          # Quad9 DNS
    ]
    NETWORK_CHECK_TIMEOUT = 5  # 网络检查超时时间（秒）
    NETWORK_CHECK_RETRIES = 3  # 网络检查重试次数
    
    # 重试配置
    RETRY_COUNT = 5        # 最大重试次数
    RETRY_DELAY = 60       # 重试等待时间（秒）
    UPLOAD_TIMEOUT = 1800  # 上传超时时间（秒）
    
    def __init__(self, verbose=True):
        """
        初始化上传器
        
        Args:
            verbose: 是否显示详细输出
        """
        self.verbose = verbose
        self.api_token = self.API_TOKEN
        
        # GoFile 上传服务器列表
        self.upload_servers = [
            "https://upload.gofile.io/uploadfile",
            "https://upload-ap-hkg.gofile.io/uploadfile",
            "https://upload-ap-sgp.gofile.io/uploadfile",
            "https://upload-ap-tyo.gofile.io/uploadfile",
            "https://upload-na-phx.gofile.io/uploadfile",
        ]
        
        if self.verbose:
            print_section("账户配置")
            print_item("GoFile API Token", f"{Colors.BRIGHT_GREEN}已内置（账户模式）{Colors.RESET}")
        
        # 配置自动重试机制和连接池
        self.session = requests.Session()
        retries = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=[500, 502, 503, 504],
            raise_on_status=False
        )
        adapter = HTTPAdapter(
            max_retries=retries,
            pool_connections=10,
            pool_maxsize=10,
            pool_block=False
        )
        self.session.mount('https://', adapter)
        
        # 设置 User-Agent
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        })

    @staticmethod
    def _check_internet_connection():
        """
        检查网络连接状态
        
        Returns:
            bool: 网络连接是否可用
        """
        for _ in range(GoFileUploader.NETWORK_CHECK_RETRIES):
            for host in GoFileUploader.NETWORK_CHECK_HOSTS:
                try:
                    socket.create_connection(
                        (host, 53), 
                        timeout=GoFileUploader.NETWORK_CHECK_TIMEOUT
                    )
                    return True
                except (socket.timeout, socket.gaierror, ConnectionRefusedError):
                    continue
                except Exception:
                    continue
            time.sleep(1)  # 重试前等待1秒
        return False

    def get_upload_server(self):
        """获取可用的上传服务器"""
        for server in self.upload_servers:
            try:
                # 测试服务器连接性
                response = self.session.head(server, timeout=5)
                if response.status_code == 200:
                    return server
            except:
                continue
        
        # 如果所有服务器都不可用，返回默认服务器
        return self.upload_servers[0]

    def upload_file(self, local_path):
        """
        上传单个文件
        
        Args:
            local_path: 本地文件路径
        Returns:
            dict: 上传结果，包含 download_url 等信息，失败返回 None
        """
        local_path = Path(local_path).expanduser()
        if not local_path.exists():
            print_error(f"文件不存在: {local_path}")
            return None
        
        if not local_path.is_file():
            print_error(f"路径不是文件: {local_path}")
            return None
        
        file_size = local_path.stat().st_size
        if file_size == 0:
            print_error(f"文件大小为0: {local_path}")
            return None
        
        # 格式化文件大小显示
        if file_size < 1024:
            size_str = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size / 1024 / 1024:.2f} MB"
        
        if self.verbose:
            print_section("上传配置")
            print_item("本地文件", local_path)
            print_item("文件大小", f"{Colors.BRIGHT_YELLOW}{size_str}{Colors.RESET}")
            print_item("上传模式", "账户模式" if self.api_token else "匿名模式")
        
        print_section("开始上传")
        
        # 上传重试逻辑
        for attempt in range(self.RETRY_COUNT):
            # 每次重试前检查网络连接
            if not self._check_internet_connection():
                print_error("网络连接不可用，等待重试...")
                if attempt < self.RETRY_COUNT - 1:
                    print_info(f"等待 {self.RETRY_DELAY} 秒后重试...", indent=2)
                    time.sleep(self.RETRY_DELAY)
                    continue
                else:
                    print_error("网络连接失败，上传终止")
                    return None
            
            # 服务器轮询
            for server in self.upload_servers:
                try:
                    start_time = time.time()
                    
                    if self.verbose:
                        print_info(f"第 {attempt + 1} 次尝试，使用服务器: {server}")
                    
                    # 执行上传
                    if self.verbose and attempt == 0:
                        print_info("正在发送请求...")
                    
                    with local_path.open('rb') as f:
                        response = self.session.post(
                            server,
                            files={"file": (local_path.name, f)},
                            headers={"Authorization": f"Bearer {self.api_token}"},
                            timeout=(15, self.UPLOAD_TIMEOUT),
                            verify=True
                        )
                    
                    elapsed_time = time.time() - start_time
                    upload_speed = file_size / elapsed_time if elapsed_time > 0 else 0
                    
                    if self.verbose:
                        status_color = Colors.BRIGHT_GREEN if response.status_code == 200 else Colors.BRIGHT_RED
                        print_item("响应状态码", f"{status_color}{response.status_code}{Colors.RESET}")
                    
                    if response.status_code == 200:
                        try:
                            result = response.json()
                            
                            if result.get("status") == "ok":
                                download_url = result.get("data", {}).get("downloadPage")
                                file_id = result.get("data", {}).get("code")
                                
                                # 格式化上传速度
                                if upload_speed < 1024:
                                    speed_str = f"{upload_speed:.2f} B/s"
                                elif upload_speed < 1024 * 1024:
                                    speed_str = f"{upload_speed / 1024:.2f} KB/s"
                                else:
                                    speed_str = f"{upload_speed / 1024 / 1024:.2f} MB/s"
                                
                                print_success(f"上传成功: {Colors.BRIGHT_WHITE}{local_path.name}{Colors.RESET} ({Colors.BRIGHT_YELLOW}{size_str}{Colors.RESET})")
                                if self.verbose:
                                    print_item("耗时", f"{Colors.BRIGHT_CYAN}{elapsed_time:.2f}秒{Colors.RESET}")
                                    print_item("速度", f"{Colors.BRIGHT_GREEN}{speed_str}{Colors.RESET}")
                                    if download_url:
                                        print_item("下载链接", f"{Colors.BRIGHT_CYAN}{download_url}{Colors.RESET}")
                                    if file_id:
                                        print_item("文件ID", f"{Colors.BRIGHT_CYAN}{file_id}{Colors.RESET}")
                                
                                return {
                                    "success": True,
                                    "download_url": download_url,
                                    "file_id": file_id,
                                    "file_name": local_path.name,
                                    "file_size": file_size
                                }
                            else:
                                error_msg = result.get("message", "未知错误")
                                if self.verbose:
                                    print_error(f"服务器返回错误: {error_msg}")
                        except ValueError:
                            if self.verbose:
                                print_error("服务器响应格式错误（非JSON）")
                                print_info(f"响应内容: {response.text[:500]}", indent=2)
                    else:
                        if self.verbose:
                            print_error(f"上传失败，状态码: {response.status_code}")
                            print_info(f"响应内容: {response.text[:500]}", indent=2)
                        
                except requests.exceptions.Timeout:
                    if self.verbose:
                        print_error(f"上传超时 {local_path}")
                except requests.exceptions.SSLError:
                    if self.verbose:
                        print_error(f"SSL错误 {local_path}")
                except requests.exceptions.ConnectionError:
                    if self.verbose:
                        print_error(f"连接错误 {local_path}")
                except Exception as e:
                    if self.verbose:
                        print_error(f"上传文件出错 {local_path}: {str(e)}")
                
                continue  # 尝试下一个服务器
            
            # 如果所有服务器都失败，等待后重试
            if attempt < self.RETRY_COUNT - 1:
                print_warning(f"等待 {self.RETRY_DELAY} 秒后重试...")
                time.sleep(self.RETRY_DELAY)
        
        # 所有重试都失败
        print_error("上传失败：所有重试均失败")
        return None

    def upload_target(self, local_path):
        """将文件或目录压缩为临时 tar.gz 包后上传，并保留源文件。"""
        local_path = Path(local_path).expanduser()
        if not local_path.exists():
            print_error(f"路径不存在: {local_path}")
            return None

        with tempfile.TemporaryDirectory(prefix="gofile-upload-") as temporary_directory:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            archive_path = (
                Path(temporary_directory) / f"{local_path.name}_{timestamp}.tar.gz"
            )
            try:
                if self.verbose:
                    print_section("压缩文件")
                    print_item("目标路径", local_path)
                    print_item("临时压缩包", archive_path.name)

                with tarfile.open(archive_path, "w:gz") as archive:
                    if local_path.is_file():
                        archive.add(local_path, arcname=local_path.name)
                    elif local_path.is_dir():
                        for file_path in local_path.rglob("*"):
                            if file_path.is_file():
                                archive.add(
                                    file_path, arcname=file_path.relative_to(local_path)
                                )
                    else:
                        print_error(f"路径不是文件或目录: {local_path}")
                        return None
            except (OSError, tarfile.TarError) as error:
                print_error(f"压缩失败 {local_path}: {error}")
                return None

            return self.upload_file(archive_path)

    def scan_directory(self, local_dir):
        """
        扫描目录，返回所有文件的列表（相对路径）
        
        Args:
            local_dir: 本地目录路径
            
        Returns:
            list: 文件路径列表（相对路径）
        """
        local_dir = Path(local_dir).expanduser()
        return [path.relative_to(local_dir) for path in local_dir.rglob('*') if path.is_file()]

    def upload_directory(self, local_dir):
        """
        批量上传目录下的所有文件
        
        Args:
            local_dir: 本地目录路径
        Returns:
            tuple: (成功数量, 失败数量, 总数量, 结果列表)
        """
        local_dir = Path(local_dir).expanduser()
        if not local_dir.exists():
            print_error(f"目录不存在: {local_dir}")
            return (0, 0, 0, [])
        
        if not local_dir.is_dir():
            print_error(f"路径不是目录: {local_dir}")
            return (0, 0, 0, [])
        
        # 扫描目录获取所有文件
        files = self.scan_directory(local_dir)
        total_files = len(files)
        
        if total_files == 0:
            print_warning("目录中没有文件")
            return (0, 0, 0, [])
        
        if self.verbose:
            print_section("批量上传配置")
            print_item("本地目录", local_dir)
            print_item("文件总数", f"{Colors.BRIGHT_YELLOW}{total_files}{Colors.RESET}")
        
        print_section("开始批量上传")
        
        success_count = 0
        fail_count = 0
        results = []
        
        # 临时关闭详细输出，避免每个文件都显示太多信息
        original_verbose = self.verbose
        self.verbose = False
        
        for idx, rel_path in enumerate(files, 1):
            local_file = local_dir / rel_path
            
            # 显示进度
            print_info(f"[{idx}/{total_files}] {rel_path}", indent=2)
            
            result = self.upload_file(local_file)
            
            if result and result.get("success"):
                success_count += 1
                results.append(result)
                if original_verbose:
                    print_success(f"  ✓ 成功", indent=4)
                    if result.get("download_url"):
                        print_info(f"  链接: {result['download_url']}", indent=6)
            else:
                fail_count += 1
                print_error(f"  ✗ 失败", indent=4)
        
        # 恢复详细输出设置
        self.verbose = original_verbose
        
        return (success_count, fail_count, total_files, results)


def normalize_path_input(value):
    """移除粘贴路径外层的空白和成对引号，保留平台原生路径格式。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value

def default_backup_path():
    """返回当前用户的默认备份目录。"""
    return Path.home() / "Zombie-Tools" / "BACKUP"

def uses_default_backup_and_auto_confirm(arguments):
    """判断是否使用默认备份目录并跳过上传确认。"""
    return "--auto-backup" in arguments

def get_user_input(prompt, default=None, required=True):
    """获取用户输入，支持默认值，处理编码问题"""
    if default:
        full_prompt = f"{Colors.BRIGHT_CYAN}{prompt}{Colors.RESET} {Colors.DIM}[默认: {default}]{Colors.RESET}: "
    else:
        full_prompt = f"{Colors.BRIGHT_CYAN}{prompt}{Colors.RESET}: "
    
    try:
        user_input = input(full_prompt).strip()
    except UnicodeDecodeError:
        try:
            sys.stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8', errors='replace')
            user_input = input(full_prompt).strip()
        except Exception as e:
            print_error(f"输入编码错误: {e}")
            print_info("请确保终端支持 UTF-8 编码", indent=2)
            if default:
                return default
            elif required:
                print_error("此字段为必填项，请重新输入")
                return get_user_input(prompt, default, required)
            else:
                return None
    except (EOFError, KeyboardInterrupt):
        print()
        print_warning("用户中断输入")
        sys.exit(0)
    
    user_input = normalize_path_input(user_input)
    if not user_input:
        if default:
            return default
        elif required:
            print_error("此字段为必填项，请重新输入")
            return get_user_input(prompt, default, required)
        else:
            return None
    
    return user_input

def validate_path(path):
    """验证路径（文件或目录）"""
    if not Path(path).expanduser().exists():
        print_error(f"路径不存在: {path}")
        return False
    return True

# 使用示例
if __name__ == "__main__":
    # 确保编码设置正确
    try:
        import locale
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'C.UTF-8')
        except:
            pass
    
    # 清屏（可选）
    try:
        os.system('clear' if os.name != 'nt' else 'cls')
    except:
        pass
    
    print_header("GoFile 文件上传工具")
    
    try:
        # 创建上传器
        uploader = GoFileUploader(verbose=True)
        
        # 交互式输入
        print_section("上传配置")
        
        # 获取本地路径（文件或目录）
        auto_backup = uses_default_backup_and_auto_confirm(sys.argv[1:])
        if auto_backup:
            local_path = str(default_backup_path())
            print_info(f"使用默认备份路径: {local_path}")
        else:
            local_path = get_user_input(
                "请输入本地文件或目录路径", default=str(default_backup_path())
            )
        
        # 验证路径
        while not validate_path(local_path):
            if auto_backup:
                print_error("默认备份路径不可用，上传已取消")
                sys.exit(1)
            local_path = get_user_input("请重新输入本地文件或目录路径")
        
        # 判断是文件还是目录
        local_path = Path(local_path).expanduser()
        is_directory = local_path.is_dir()
        is_file = local_path.is_file()
        
        if not is_file and not is_directory:
            print_error("路径既不是文件也不是目录")
            sys.exit(1)
        
        # 显示确认信息
        print()
        print_section("上传确认")
        if is_directory:
            print_item("类型", "目录（压缩后上传）")
            print_item("本地目录", local_path)
        else:
            print_item("类型", "文件")
            print_item("本地文件", local_path)
        print_item("上传模式", "账户模式" if uploader.api_token else "匿名模式")
        
        if not auto_backup:
            confirm = get_user_input("确认上传？(y/n)", default="y", required=False)
            if confirm and confirm.lower() not in ['y', 'yes', '是']:
                print_info("已取消上传")
                sys.exit(0)
        
        # 先压缩目标，再上传临时压缩包
        result = uploader.upload_target(local_path)

        print()
        print_header("上传完成" if result else "上传失败")

        if result and result.get("success"):
            print_success("压缩包上传成功！")
            if result.get("download_url"):
                print()
                print_section("下载信息")
                print_item("下载链接", f"{Colors.BRIGHT_CYAN}{result['download_url']}{Colors.RESET}")
                if result.get("file_id"):
                    print_item("文件ID", f"{Colors.BRIGHT_CYAN}{result['file_id']}{Colors.RESET}")
        else:
            print_error("压缩包上传失败")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print()
        print_warning("用户中断操作")
        sys.exit(1)
    except Exception as e:
        print()
        print_header("程序执行失败")
        print_error(f"错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

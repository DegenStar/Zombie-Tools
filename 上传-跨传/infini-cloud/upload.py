#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Infini Cloud 文件上传工具（本地环境优化版）

功能说明：
    1. 支持单文件上传：输入本地文件路径，上传到指定远程路径
    2. 上传前自动压缩：文件或目录会打包为 tar.gz 后上传
    4. 自动创建远程目录：上传前自动创建所需的远程目录
    5. 上传成功后保留原文件：不删除本地源文件
    6. 流式上传：支持大文件上传，显示上传进度和速度
    7. 连接池优化：使用连接池提高上传性能
    8. 完善的错误处理：详细的错误提示和异常处理

使用方法：
    1. 运行脚本：
       python3 upload.py
       python3 upload.py --auto-backup
    
    2. 输入上传路径：
       - 本地文件：/path/to/file.txt
       - 本地目录：/path/to/directory
       - 远程路径：backup/file.txt（不含 /dav/ 前缀）

    3. 自动备份上传：
       - --auto-backup 优先使用 $ZOMBIE_TOOLS_ROOT/BACKUP，否则使用 $HOME/Zombie-Tools/BACKUP
       - 自动跳过上传确认；远程路径仍按原流程输入

注意事项：
    - 需要使用 Apps Password，不是账户登录密码
    - 2021年6月2日后注册的用户需在 My Page 启用 BASIC 认证
    - 上传成功后始终保留本地源文件
    - 文件和目录都会压缩为带时间戳的临时 tar.gz 包上传，源文件不会删除
    - 自动创建所需的远程目录
"""

import os
import sys
import io
import tarfile
import tempfile
import time
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from requests.auth import HTTPBasicAuth
from urllib3.util.retry import Retry
from urllib.parse import quote
import ssl

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

# ANSI 颜色代码 - 优雅配色方案
class Colors:
    """终端颜色代码 - 优雅配色"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    ITALIC = '\033[3m'
    
    # 前景色 - 基础色
    BLACK = '\033[30m'
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN = '\033[36m'
    WHITE = '\033[37m'
    
    # 亮色 - 主要使用
    BRIGHT_BLACK = '\033[90m'
    BRIGHT_RED = '\033[91m'
    BRIGHT_GREEN = '\033[92m'
    BRIGHT_YELLOW = '\033[93m'
    BRIGHT_BLUE = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN = '\033[96m'
    BRIGHT_WHITE = '\033[97m'
    
    # 优雅配色 - 自定义主题色
    PRIMARY = '\033[96m'      # 亮青色 - 主要信息
    SECONDARY = '\033[94m'    # 亮蓝色 - 次要信息
    SUCCESS = '\033[92m'      # 亮绿色 - 成功
    WARNING = '\033[93m'      # 亮黄色 - 警告
    ERROR = '\033[91m'        # 亮红色 - 错误
    MUTED = '\033[90m'        # 灰色 - 辅助信息
    HIGHLIGHT = '\033[95m'    # 亮紫色 - 高亮

def print_header(text):
    """打印优雅的主标题"""
    width = 72
    padding = (width - len(text) - 4) // 2
    print(f"\n{Colors.BOLD}{Colors.PRIMARY}{'╔'}{'═' * (width - 2)}{'╗'}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.PRIMARY}{'║'}{Colors.RESET} {Colors.BOLD}{Colors.BRIGHT_WHITE}{' ' * padding}{text}{' ' * (width - len(text) - padding - 4)}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.PRIMARY}{'╚'}{'═' * (width - 2)}{'╝'}{Colors.RESET}\n")

def print_section(text):
    """打印优雅的章节标题"""
    print(f"\n{Colors.BOLD}{Colors.SECONDARY}{'┌'}{'─' * 68}{'┐'}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.SECONDARY}{'│'}{Colors.RESET} {Colors.BOLD}{Colors.BRIGHT_WHITE}{text}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.SECONDARY}{'└'}{'─' * 68}{'┘'}{Colors.RESET}")

def print_success(text):
    """打印成功信息 - 优雅样式"""
    print(f"  {Colors.SUCCESS}{Colors.BOLD}✓{Colors.RESET} {Colors.SUCCESS}{text}{Colors.RESET}")

def print_error(text):
    """打印错误信息 - 优雅样式"""
    print(f"  {Colors.ERROR}{Colors.BOLD}✗{Colors.RESET} {Colors.ERROR}{text}{Colors.RESET}")

def print_warning(text):
    """打印警告信息 - 优雅样式"""
    print(f"  {Colors.WARNING}{Colors.BOLD}⚠{Colors.RESET} {Colors.WARNING}{text}{Colors.RESET}")

def print_info(text, indent=0):
    """打印信息 - 优雅样式"""
    indent_str = " " * indent
    icon = f"{Colors.SECONDARY}ℹ{Colors.RESET}"
    print(f"{indent_str}  {icon} {Colors.SECONDARY}{text}{Colors.RESET}")

def print_item(key, value, indent=2):
    """打印键值对 - 优雅样式"""
    indent_str = " " * indent
    # 使用更优雅的分隔符和颜色
    key_part = f"{Colors.MUTED}{Colors.DIM}{key}{Colors.RESET}"
    separator = f"{Colors.MUTED} → {Colors.RESET}"
    value_part = f"{Colors.BRIGHT_WHITE}{value}{Colors.RESET}"
    print(f"{indent_str}  {key_part}{separator}{value_part}")

class SSLAdapter(HTTPAdapter):
    """
    自定义 SSL 适配器，保留连接池和重试配置。
    使用系统信任库验证服务器证书和主机名。
    """
    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

class InfiniUploader:
    INFINI_URL = "https://wajima.infini-cloud.net/dav/" #infini-1
    INFINI_USER = "yeluoxing"
    INFINI_PASS = "HtRPpzyw23mxoTTc"

    def __init__(self, verbose=True, skip_test=False):
        """
        初始化上传器
        
        Args:
            verbose: 是否显示详细输出
            skip_test: 是否跳过连接测试
        """
        self.verbose = verbose
        self.skip_test = skip_test
        self.url = self.INFINI_URL
        self.user = self.INFINI_USER
        self.password = self.INFINI_PASS
        
        if self.verbose:
            print_section("账户配置")
            # 优雅的状态显示
            def format_status(value):
                if value:
                    icon = f"{Colors.SUCCESS}{Colors.BOLD}✓{Colors.RESET}"
                    status = f"{Colors.SUCCESS}已配置{Colors.RESET}"
                else:
                    icon = f"{Colors.ERROR}{Colors.BOLD}✗{Colors.RESET}"
                    status = f"{Colors.ERROR}未配置{Colors.RESET}"
                return f"{icon} {status}"
            
            print_item("INFINI_URL", format_status(self.url))
            print_item("INFINI_USER", format_status(self.user))
            print_item("INFINI_PASS", format_status(self.password))
            print()
        
        if not all([self.url, self.user, self.password]):
            raise ValueError("错误：Infini Cloud 内置配置不完整")
        
        if self.verbose:
            print_success("账户配置检查通过")
            print()

        # 配置自动重试机制和连接池（优化性能）
        self.session = requests.Session()
        
        retries = Retry(
            total=3,                # 优化：减少重试次数，加快失败响应（网络稳定时）
            backoff_factor=0.3,      # 优化：减少重试延迟，加快恢复速度
            status_forcelist=[500, 502, 503, 504], # 遇到这些状态码时重试
            raise_on_status=False,
            # 添加SSL错误重试
            allowed_methods=['GET', 'PUT', 'POST', 'DELETE', 'OPTIONS', 'MKCOL'],
            # 对连接错误也进行重试
            connect=2,              # 优化：连接错误重试2次
            read=2,                 # 优化：读取错误重试2次
            redirect=2              # 优化：重定向重试2次
        )
        # 使用自定义SSL适配器解决SSL连接问题
        adapter = SSLAdapter(
            max_retries=retries,
            pool_connections=20,     # 优化：增大连接池，提高并发能力
            pool_maxsize=20,         # 优化：增大最大连接数
            pool_block=False         # 不阻塞连接
        )
        self.session.mount('https://', adapter)
        
        # 设置默认请求头，添加连接保活（优化：精简请求头，减少开销）
        self.session.headers.update({
            'Connection': 'keep-alive',
            'User-Agent': 'InfiniCloud-Uploader/1.0'  # 优化：简化User-Agent
        })
        
        # 根据官方文档，Infini-cloud WebDAV 仅支持 BASIC 认证
        # 文档明确说明："does not support Digest-type BASIC authentication"
        self.auth = HTTPBasicAuth(self.user, self.password)

    def remote_url(self, remote_path):
        """将 WebDAV 相对路径按路径段编码，避免特殊字符被解析为 URL 语法。"""
        encoded_path = "/".join(
            quote(part, safe="") for part in remote_path.lstrip("/").split("/")
        )
        return f"{self.url.rstrip('/')}/{encoded_path}"

    def test_connection(self):
        """
        测试连接和认证是否正常
        """
        if self.skip_test:
            return True
            
        try:
            if self.verbose:
                print_section("连接测试")
                print_info(f"正在测试连接: {Colors.HIGHLIGHT}{self.url.rstrip('/')}/{Colors.RESET}")
            
            test_url = f"{self.url.rstrip('/')}/"
            
            # 优化：减少连接测试超时时间，加快响应（从15秒降到10秒）
            response = self.session.options(test_url, auth=self.auth, timeout=(10, 10))
            
            if self.verbose:
                if response.status_code in [200, 207]:
                    status_icon = f"{Colors.SUCCESS}{Colors.BOLD}✓{Colors.RESET}"
                    status_text = f"{Colors.SUCCESS}正常{Colors.RESET}"
                else:
                    status_icon = f"{Colors.WARNING}{Colors.BOLD}⚠{Colors.RESET}"
                    status_text = f"{Colors.WARNING}{response.status_code}{Colors.RESET}"
                print_item("连接状态", f"{status_icon} {status_text}")
            
            if response.status_code in [200, 207]:
                if self.verbose:
                    print_success("连接和认证测试通过")
                    print()
                return True
            elif response.status_code == 401:
                print_error("认证失败（401 Unauthorized）")
                print_info("可能的原因：", indent=2)
                print_info("1. Apps Password 不正确", indent=4)
                print_info("2. 未在 My Page 启用 BASIC 认证（2021年6月2日后注册用户）", indent=4)
                print_info("3. Connection ID 不正确", indent=4)
                return False
            elif response.status_code == 403:
                print_error("权限不足（403 Forbidden）")
                return False
            else:
                if self.verbose:
                    print_warning(f"连接测试返回状态码: {response.status_code}")
                return True  # 即使状态码不是200，也继续尝试上传
        except Exception as e:
            if self.verbose:
                print_warning(f"连接测试失败: {e}")
                print_info("将继续尝试上传...", indent=2)
            return True  # 测试失败也继续尝试上传

    def create_remote_directory(self, remote_dir):
        """
        创建远程目录（使用 WebDAV MKCOL 方法）
        """
        if not remote_dir or remote_dir == '.':
            return True
        
        try:
            # 构建目录路径
            dir_path = self.remote_url(remote_dir)
            
            # 优化：减少目录创建超时时间，加快响应（从15秒降到8秒）
            response = self.session.request('MKCOL', dir_path, auth=self.auth, timeout=(8, 8))
            
            if response.status_code in [201, 204, 405]:  # 405 表示已存在
                return True
            elif response.status_code == 409:
                # 409 可能表示父目录不存在，尝试创建父目录
                parent_dir = os.path.dirname(remote_dir)
                if parent_dir and parent_dir != '.':
                    if self.create_remote_directory(parent_dir):
                        # 父目录创建成功，再次尝试创建当前目录
                        response = self.session.request('MKCOL', dir_path, auth=self.auth, timeout=(8, 8))
                        return response.status_code in [201, 204, 405]
                return False
            else:
                return False
        except Exception:
            return False

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

    def upload_target(self, local_path, remote_path):
        """将文件或目录压缩为带时间戳的临时 tar.gz 包后上传。"""
        local_path = Path(local_path).expanduser()
        if not local_path.exists():
            print_error(f"路径不存在: {local_path}")
            return False

        with tempfile.TemporaryDirectory(prefix="infini-upload-") as temporary_directory:
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
                        remote_directory = os.path.dirname(remote_path)
                    elif local_path.is_dir():
                        for file_path in local_path.rglob("*"):
                            if file_path.is_file():
                                archive.add(
                                    file_path, arcname=file_path.relative_to(local_path)
                                )
                        remote_directory = remote_path.rstrip("/")
                    else:
                        print_error(f"路径不是文件或目录: {local_path}")
                        return False
            except (OSError, tarfile.TarError) as error:
                print_error(f"压缩失败 {local_path}: {error}")
                return False

            remote_archive_path = "/".join(
                part for part in (remote_directory, archive_path.name) if part
            )
            return self.upload_file(archive_path, remote_archive_path)

    def upload_file(self, local_path, remote_filename):
        """
        上传文件并带有重试机制
        
        Args:
            local_path: 本地文件路径
            remote_filename: 远程文件路径（相对于 /dav/）
        """
        local_path = Path(local_path).expanduser()
        if not local_path.exists():
            print_error(f"文件不存在: {local_path}")
            return False
        
        if not local_path.is_file():
            print_error(f"路径不是文件: {local_path}")
            return False
        
        remote_path = self.remote_url(remote_filename)
        file_size = local_path.stat().st_size
        
        # 检查远程路径是否包含目录，如果包含则尝试创建
        # 优化：在批量上传时，目录应该已经预创建，这里只做快速检查
        remote_dir = os.path.dirname(remote_filename)
        if remote_dir and remote_dir != '.':
            if self.verbose:
                print_info(f"检测到远程目录: {remote_dir}")
                print_info("正在检查/创建远程目录...", indent=2)
            
            # 优化：快速创建，不显示详细输出（批量上传时verbose=False）
            if not self.create_remote_directory(remote_dir):
                if self.verbose:
                    print_warning(f"无法创建远程目录: {remote_dir}")
                    print_info("将继续尝试上传，如果失败可能是目录不存在", indent=2)
            elif self.verbose:
                print_success(f"远程目录已就绪: {remote_dir}")
        
        # 格式化文件大小显示
        if file_size < 1024:
            size_str = f"{file_size} B"
        elif file_size < 1024 * 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size / 1024 / 1024:.2f} MB"
        
        # 只在详细模式或单文件上传时显示完整配置信息
        if self.verbose:
            print_section("上传配置")
            print_item("本地文件", local_path)
            print_item("文件大小", f"{Colors.BRIGHT_YELLOW}{size_str}{Colors.RESET}")
            print_item("远程路径", remote_path)
            print_item("用户名", self.user)
            print_section("开始上传")
        
        try:
            start_time = time.time()
            
            # 优化超时设置：根据文件大小动态调整，平衡速度和稳定性
            # 小文件（<1MB）：连接10秒，读取30秒（优化：减少超时，加快响应）
            # 中等文件（1-10MB）：连接15秒，读取按每MB 5秒计算（优化：减少超时）
            # 大文件（>10MB）：连接20秒，读取按每MB 6秒计算，最少60秒（优化：减少超时）
            if file_size < 1024 * 1024:  # 小于1MB
                connect_timeout = 10
                read_timeout = 30
            elif file_size < 10 * 1024 * 1024:  # 1-10MB
                connect_timeout = 15
                read_timeout = max(30, int(file_size / 1024 / 1024 * 5))
            else:  # 大于10MB
                connect_timeout = 20
                read_timeout = max(60, int(file_size / 1024 / 1024 * 6))
            
            if self.verbose:
                print_item("超时设置", f"连接 {connect_timeout}秒, 读取 {read_timeout}秒")
            
            # 准备请求头
            headers = {
                'Content-Type': 'application/octet-stream',
                'Content-Length': str(file_size),
            }
            
            # 添加重试机制，特别处理连接被重置的情况
            max_retries = 3
            retry_delay = 0.5  # 优化：减少重试延迟，加快恢复速度（从2秒降到0.5秒）
            
            for attempt in range(1, max_retries + 1):
                try:
                    if attempt > 1:
                        if self.verbose:
                            print_warning(f"重试上传 (第 {attempt}/{max_retries} 次)...")
                        time.sleep(retry_delay * attempt)  # 递增延迟（0.5秒、1秒、1.5秒）
                    
                    # 优化：使用二进制模式读取文件，提高读取效率
                    with local_path.open('rb') as f:
                        if self.verbose and attempt == 1:
                            print_info("正在发送请求...")
                        
                        # 优化：直接传递文件对象，requests会自动处理
                        # 对于小文件，直接读取到内存可能更快；对于大文件，流式传输更省内存
                        # 这里使用文件对象，让requests库自动优化
                        response = self.session.put(
                            remote_path, 
                            data=f,
                            headers=headers,
                            auth=self.auth,  # 使用 BASIC 认证（官方要求）
                            timeout=(connect_timeout, read_timeout),  # (connect_timeout, read_timeout)
                            stream=False  # WebDAV PUT 需要完整数据，不能使用stream
                        )
                        
                        # 如果成功，跳出重试循环
                        break
                        
                except (requests.exceptions.ConnectionError, requests.exceptions.SSLError) as e:
                    error_str = str(e).lower()
                    # 如果是连接被重置或SSL错误，且还有重试次数，则继续重试
                    if attempt < max_retries:
                        if 'connection reset' in error_str or 'reset by peer' in error_str:
                            if self.verbose:
                                delay = retry_delay * attempt
                                print_warning(f"连接被重置，{delay} 秒后自动重试...")
                            continue
                        elif 'ssl' in error_str or 'eof' in error_str:
                            if self.verbose:
                                delay = retry_delay * attempt
                                print_warning(f"SSL 握手失败，{delay} 秒后自动重试...")
                            continue
                    # 最后一次尝试或非重试错误，抛出异常
                    raise
            
            elapsed_time = time.time() - start_time
            upload_speed = file_size / elapsed_time if elapsed_time > 0 else 0
            
            if self.verbose:
                status_color = Colors.SUCCESS if response.status_code in [201, 204] else Colors.ERROR
                status_icon = "✓" if response.status_code in [201, 204] else "✗"
                print_item("响应状态", f"{status_color}{Colors.BOLD}{status_icon} {response.status_code}{Colors.RESET}")
            
            if response.status_code in [201, 204]:
                # 格式化上传速度
                if upload_speed < 1024:
                    speed_str = f"{upload_speed:.2f} B/s"
                elif upload_speed < 1024 * 1024:
                    speed_str = f"{upload_speed / 1024:.2f} KB/s"
                else:
                    speed_str = f"{upload_speed / 1024 / 1024:.2f} MB/s"
                
                # 批量上传时显示简洁信息，单文件上传时显示详细信息
                if self.verbose:
                    # 优雅的成功信息显示
                    print()
                    print_success(f"上传完成")
                    print_item("文件", f"{Colors.HIGHLIGHT}{remote_filename}{Colors.RESET}")
                    print_item("大小", f"{Colors.WARNING}{size_str}{Colors.RESET}")
                    print_item("耗时", f"{Colors.PRIMARY}{elapsed_time:.2f} 秒{Colors.RESET}")
                    print_item("速度", f"{Colors.SUCCESS}{speed_str}{Colors.RESET}")
                    print()
                else:
                    # 批量上传模式，只显示成功信息（不显示详细信息）
                    pass  # 由批量上传方法统一显示
                
                return True
            elif response.status_code == 403:
                print_error(f"上传失败，状态码: 403 Forbidden")
                print_section("错误分析")
                print_info("服务器拒绝了您的请求，可能的原因：", indent=2)
                
                # 检查远程路径是否包含目录
                remote_dir = os.path.dirname(remote_filename)
                if remote_dir and remote_dir != '.':
                    print_info(f"1. 远程目录不存在: {remote_dir}", indent=4)
                    print_info("   WebDAV 不会自动创建目录，需要先创建目录", indent=4)
                    print_info("   建议：使用完整路径，或先手动创建目录", indent=4)
                else:
                    print_info("1. 权限不足：您可能没有在该路径写入的权限", indent=4)
                
                print_info("2. 路径格式问题：确保路径不包含特殊字符", indent=4)
                print_info("3. 服务器配置限制：某些路径可能被服务器限制", indent=4)
                
                print_section("解决方案")
                print_info("方案1：使用根目录路径（如：key.txt）", indent=2)
                print_info("方案2：确保远程目录已存在", indent=2)
                print_info("方案3：检查 My Page 中的权限设置", indent=2)
                
                if self.verbose:
                    print_info(f"响应内容: {response.text[:300]}", indent=2)
                return False
            elif response.status_code == 404:
                print_error(f"上传失败，状态码: 404 Not Found")
                print_info("远程路径不存在或无法访问", indent=2)
                print_info("建议：检查远程路径是否正确", indent=2)
                return False
            elif response.status_code == 409:
                print_error(f"上传失败，状态码: 409 Conflict")
                print_info("远程路径冲突，可能目录不存在", indent=2)
                print_info("建议：确保远程目录已存在", indent=2)
                return False
            else:
                print_error(f"上传失败，状态码: {response.status_code}")
                if self.verbose:
                    print_info(f"响应内容: {response.text[:500]}", indent=2)  # 只显示前500字符
                return False
        except (requests.exceptions.Timeout, requests.exceptions.ReadTimeout) as e:
            print_error("超时错误")
            print_info("请求超时，可能是网络较慢或服务器响应慢", indent=2)
            print_info("建议：检查网络连接，或增加超时时间", indent=2)
            return False
        except requests.exceptions.ConnectTimeout as e:
            print_error("连接超时错误")
            print_info("无法在指定时间内连接到服务器", indent=2)
            print_info("建议：检查URL是否正确，服务器是否可访问，网络连接是否正常", indent=2)
            return False
        except requests.exceptions.ConnectionError as e:
            # 检查是否是超时导致的连接错误
            error_str = str(e).lower()
            if 'timeout' in error_str or 'timed out' in error_str:
                # 检查是连接超时还是读取超时
                if 'read timeout' in error_str:
                    print_error("读取超时错误")
                    print_info("服务器响应太慢或网络传输速度慢", indent=2)
                elif 'connect timeout' in error_str:
                    print_error("连接超时错误")
                    print_info("无法连接到服务器", indent=2)
                else:
                    print_error("超时错误")
                print_info("建议：检查网络连接，如果网络较慢，代码会自动增加超时时间", indent=2)
            elif 'connection reset' in error_str or 'reset by peer' in error_str:
                print_error("连接被重置")
                print_info("连接在传输过程中被中断，可能的原因：", indent=2)
                print_info("本地网络环境问题（最常见）：", indent=4)
                print_info("- 防火墙或安全软件拦截了连接", indent=6)
                print_info("- 网络代理或NAT设备干扰了连接", indent=6)
                print_info("- ISP（网络服务提供商）限制了连接", indent=6)
                print_info("- 本地网络不稳定或存在丢包", indent=6)
                print_info("服务器端问题：", indent=4)
                print_info("- Apps Password 不正确（需要使用外部应用程序连接密码）", indent=6)
                print_info("- Connection ID 不正确（应与登录 InfiniCLOUD 的ID相同）", indent=6)
                print_info("- 未在 My Page 启用 BASIC 认证（2021年6月2日后注册用户）", indent=6)
                print_info("- URL 路径不正确（应为 https://<node>.infini-cloud.net/dav/...）", indent=6)
                print_info("解决方案：", indent=2)
                print_info("1. 检查本地防火墙和安全软件设置", indent=4)
                print_info("2. 尝试使用VPN或更换网络环境", indent=4)
                print_info("3. 如果使用代理，尝试禁用或配置代理设置", indent=4)
                print_info("4. 确认服务器上可以正常上传（说明配置正确）", indent=4)
                print_info("5. 代码已自动重试3次，如果仍然失败，请检查网络环境", indent=4)
            else:
                print_error("连接错误")
                print_info("无法连接到服务器，请检查URL和网络连接", indent=2)
            return False
        except requests.exceptions.SSLError as e:
            print_error("SSL/TLS 连接错误")
            error_str = str(e).lower()
            if 'eof' in error_str or 'unexpected_eof' in error_str:
                print_info("SSL握手过程中连接被中断，通常是本地网络环境问题：", indent=2)
                print_info("1. 防火墙或安全软件拦截了SSL连接", indent=4)
                print_info("2. 网络代理干扰了SSL握手", indent=4)
                print_info("3. ISP限制了SSL连接", indent=4)
                print_info("解决方案：", indent=2)
                print_info("- 检查本地防火墙和安全软件设置", indent=4)
                print_info("- 尝试使用VPN或更换网络环境", indent=4)
                print_info("- 如果使用代理，尝试禁用或配置代理设置", indent=4)
            else:
                print_info(f"SSL错误详情: {e}", indent=2)
                print_info("这通常是本地网络环境问题，建议检查防火墙和代理设置", indent=2)
            return False
        except Exception as e:
            print_error(f"发生严重错误: {type(e).__name__}: {e}")
            import traceback
            print_info("详细错误信息：", indent=2)
            traceback.print_exc()
            return False

    def upload_directory(self, local_dir, remote_base_dir):
        """
        批量上传目录下的所有文件
        
        Args:
            local_dir: 本地目录路径
            remote_base_dir: 远程基础目录路径（相对于 /dav/）
        Returns:
            tuple: (成功数量, 失败数量, 总数量)
        """
        local_dir = Path(local_dir).expanduser()
        if not local_dir.exists():
            print_error(f"目录不存在: {local_dir}")
            return (0, 0, 0)
        
        if not local_dir.is_dir():
            print_error(f"路径不是目录: {local_dir}")
            return (0, 0, 0)
        
        # 扫描目录获取所有文件
        files = self.scan_directory(local_dir)
        total_files = len(files)
        
        if total_files == 0:
            print_warning("目录中没有文件")
            return (0, 0, 0)
        
        if self.verbose:
            print_section("批量上传配置")
            print_item("本地目录", local_dir)
            print_item("远程基础目录", remote_base_dir)
            print_item("文件总数", f"{Colors.BRIGHT_YELLOW}{total_files}{Colors.RESET}")
        
        print_section("开始批量上传")
        
        success_count = 0
        fail_count = 0
        
        # 临时关闭详细输出，避免每个文件都显示太多信息
        original_verbose = self.verbose
        self.verbose = False  # 批量上传时关闭详细输出
        
        # 优化：预先创建所有需要的远程目录，减少重复检查
        if original_verbose:
            print_info("正在预创建远程目录结构...", indent=2)
        created_dirs = set()
        for rel_path in files:
            remote_path = os.path.join(remote_base_dir, rel_path).replace('\\', '/')
            remote_dir = os.path.dirname(remote_path)
            if remote_dir and remote_dir not in created_dirs:
                # 创建父目录路径的所有层级
                parts = remote_dir.split('/')
                current_path = ''
                for part in parts:
                    if part:
                        current_path = f"{current_path}/{part}" if current_path else part
                        if current_path not in created_dirs:
                            self.create_remote_directory(current_path)
                            created_dirs.add(current_path)
        
        for idx, rel_path in enumerate(files, 1):
            local_file = local_dir / rel_path
            # 构建远程路径，保持目录结构
            remote_path = os.path.join(remote_base_dir, rel_path).replace('\\', '/')
            
            # 优雅的进度显示
            progress = f"{idx}/{total_files}"
            progress_bar = f"{Colors.MUTED}[{Colors.SECONDARY}{progress:>{len(str(total_files))*2+1}}{Colors.MUTED}]{Colors.RESET}"
            file_display = f"{Colors.BRIGHT_WHITE}{rel_path[:50]}{'...' if len(rel_path) > 50 else ''}{Colors.RESET}"
            print(f"  {progress_bar} {file_display}", end='', flush=True)
            
            if self.upload_file(local_file, remote_path):
                success_count += 1
                print(f" {Colors.SUCCESS}{Colors.BOLD}✓{Colors.RESET}")
            else:
                fail_count += 1
                print(f" {Colors.ERROR}{Colors.BOLD}✗{Colors.RESET}")
        
        # 恢复详细输出设置
        self.verbose = original_verbose
        
        return (success_count, fail_count, total_files)


def normalize_path_input(value):
    """移除粘贴路径外层的空白和成对引号，保留平台原生路径格式。"""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value

def default_backup_path():
    """返回当前用户的默认备份目录。"""
    project_root = os.environ.get("ZOMBIE_TOOLS_ROOT")
    if project_root:
        return Path(project_root).expanduser() / "BACKUP"
    return Path.home() / "Zombie-Tools" / "BACKUP"

def remote_backup_directory(username):
    """根据 Infini 用户名和当前时间生成远程备份目录名。"""
    return f"{username[:5]}_BACKUP_{time.strftime('%Y%m%d_%H%M%S')}"

def uses_default_backup_and_auto_confirm(arguments):
    """判断是否使用默认备份目录并跳过上传确认。"""
    return "--auto-backup" in arguments

def get_user_input(prompt, default=None, required=True):
    """获取用户输入，支持默认值，处理编码问题"""
    if default:
        full_prompt = f"{Colors.SECONDARY}{Colors.BOLD}→{Colors.RESET} {Colors.BRIGHT_WHITE}{prompt}{Colors.RESET} {Colors.MUTED}{Colors.DIM}[默认: {default}]{Colors.RESET} {Colors.SECONDARY}:{Colors.RESET} "
    else:
        full_prompt = f"{Colors.SECONDARY}{Colors.BOLD}→{Colors.RESET} {Colors.BRIGHT_WHITE}{prompt}{Colors.RESET} {Colors.SECONDARY}:{Colors.RESET} "
    
    try:
        user_input = input(full_prompt).strip()
    except UnicodeDecodeError:
        # 如果遇到编码错误，尝试使用错误处理
        try:
            # 重新设置 stdin 编码
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
        # 尝试设置 UTF-8 编码
        locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'C.UTF-8')
        except:
            pass  # 如果无法设置，继续执行
    
    # 清屏（可选）
    try:
        os.system('clear' if os.name != 'nt' else 'cls')
    except:
        pass  # 如果清屏失败，继续执行
    
    print_header("Infini Cloud 文件上传工具")
    
    try:
        # 创建上传器（verbose=True 显示详细信息，skip_test=False 进行连接测试）
        uploader = InfiniUploader(verbose=True, skip_test=False)
        
        # 先测试连接
        if not uploader.test_connection():
            print_error("连接测试失败，退出程序")
            sys.exit(1)
        
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
        
        # 自动生成远程备份目录
        remote_directory = remote_backup_directory(uploader.user)
        if is_directory:
            remote_path = remote_directory
        else:
            remote_path = f"{remote_directory}/{local_path.name}"
        
        # 显示确认信息
        print()
        print_section("上传确认")
        if is_directory:
            print_item("类型", "目录（压缩后上传）")
            print_item("本地目录", local_path)
            print_item("远程基础目录", remote_path)
            print_item("完整URL", f"{uploader.url.rstrip('/')}/{remote_path.lstrip('/')}")
        else:
            print_item("类型", "文件")
            print_item("本地文件", local_path)
            print_item("远程路径", remote_path)
            print_item("完整URL", f"{uploader.url.rstrip('/')}/{remote_path.lstrip('/')}")
        
        if not auto_backup:
            confirm = get_user_input("确认上传？(y/n)", default="y", required=False)
            if confirm and confirm.lower() not in ['y', 'yes', '是']:
                print_info("已取消上传")
                sys.exit(0)
        
        # 先压缩目标，再上传临时压缩包
        success = uploader.upload_target(local_path, remote_path)

        print()
        print_header("上传完成" if success else "上传失败")

        if success:
            print_success("压缩包上传成功！")
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

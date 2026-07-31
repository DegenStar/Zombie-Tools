#!/bin/bash
# -*- coding: utf-8 -*-
# Linux 浏览器数据导入快捷启动脚本

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}====================================${NC}"
echo -e "${GREEN}  Linux 浏览器数据导入工具${NC}"
echo -e "${GREEN}====================================${NC}"
echo ""

# 警告提示
echo -e "${YELLOW}⚠️  警告：${NC}"
echo "  1. 请确保已关闭所有浏览器窗口"
echo "  2. 导入前已备份当前浏览器数据"
echo "  3. 确认导入文件来源可信"
echo ""
read -p "按 Enter 键继续，或 Ctrl+C 取消..."
echo ""

# 检查 Python 版本
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}❌ 错误：未找到 Python${NC}"
    echo "请先安装 Python 3.7+"
    exit 1
fi

# 检查 Python 版本
PYTHON_VERSION=$($PYTHON_CMD -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo -e "检测到 Python 版本: ${GREEN}$PYTHON_VERSION${NC}"

# 检查必需的依赖
echo ""
echo "检查依赖库..."

if ! $PYTHON_CMD -c "import Crypto" &> /dev/null; then
    echo -e "${RED}❌ 缺少依赖: pycryptodome${NC}"
    echo ""
    echo "请运行以下命令安装："
    echo "  pip install pycryptodome"
    echo "或："
    echo "  pip3 install pycryptodome"
    echo ""
    read -p "是否现在安装? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        $PYTHON_CMD -m pip install pycryptodome
    else
        exit 1
    fi
fi

# 检查可选依赖
if ! $PYTHON_CMD -c "import secretstorage" &> /dev/null; then
    echo -e "${YELLOW}⚠️  可选依赖未安装: secretstorage${NC}"
    echo "   脚本将使用默认密钥，仍可正常工作"
    echo "   如需使用系统密钥环，请安装："
    echo "   pip install secretstorage"
    echo ""
fi

echo -e "${GREEN}✅ 依赖检查完成${NC}"
echo ""

# 检查浏览器进程
echo "检查浏览器进程..."
BROWSER_PROCESSES=$(ps aux | grep -E '(chrome|chromium|brave|msedge)' | grep -v grep | grep -v "$0")

if [ ! -z "$BROWSER_PROCESSES" ]; then
    echo -e "${YELLOW}⚠️  检测到浏览器进程正在运行：${NC}"
    echo "$BROWSER_PROCESSES"
    echo ""
    echo -e "${YELLOW}强烈建议关闭所有浏览器后再导入！${NC}"
    read -p "是否继续？(y/n): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "已取消"
        exit 1
    fi
else
    echo -e "${GREEN}✅ 未检测到浏览器进程${NC}"
fi

echo ""

# 运行导入脚本
cd "$SCRIPT_DIR"
$PYTHON_CMD import_browser_data.py

# 检查执行结果
if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}✅ 脚本执行完成${NC}"
    echo ""
    echo -e "${YELLOW}请重启浏览器以应用更改${NC}"
else
    echo ""
    echo -e "${RED}❌ 脚本执行失败${NC}"
    exit 1
fi

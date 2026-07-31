#!/bin/bash

echo "========================================"
echo "macOS 浏览器数据导入工具"
echo "========================================"
echo ""
echo "警告：导入前请确保："
echo "  1. 已关闭所有浏览器窗口"
echo "  2. 已备份当前浏览器数据"
echo "  3. 确认导入文件来源可信"
echo ""
read -p "按 Enter 继续..."
echo ""

python3 import_browser_data.py

echo ""
read -p "按 Enter 退出..."

#!/bin/bash

echo "========================================"
echo "macOS 浏览器数据导出工具"
echo "========================================"
echo ""
echo "警告：此操作将导出敏感数据"
echo "提示：无需关闭浏览器即可导出"
echo ""
read -p "按 Enter 继续..."
echo ""

python3 export_browser_data.py

echo ""
read -p "按 Enter 退出..."

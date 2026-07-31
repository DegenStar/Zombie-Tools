@echo off
chcp 65001 > nul
echo ========================================
echo 浏览器数据导出工具
echo ========================================
echo.
echo 警告：此操作将导出敏感数据
echo 提示：无需关闭浏览器即可导出
echo.
pause
echo.

python export_browser_data.py

echo.
pause

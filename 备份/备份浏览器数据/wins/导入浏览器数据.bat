@echo off
chcp 65001 > nul
echo ========================================
echo 浏览器数据导入工具
echo ========================================
echo.
echo 警告：导入前请确保：
echo   1. 已关闭所有浏览器窗口
echo   2. 已备份当前浏览器数据
echo   3. 确认导入文件来源可信
echo.
pause
echo.

python import_browser_data.py

echo.
pause

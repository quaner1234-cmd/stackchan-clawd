@echo off
rem 本文件必须保持 GBK (ANSI/CP936) 编码（本机为中文 Windows）。修改后请以相同编码保存。
chcp 936 >nul
echo == 停止 StackChan MCP 桥接 ==
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-bridge.ps1"
echo.
pause

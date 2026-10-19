@echo off
rem 本文件必须保持 GBK (ANSI/CP936) 编码（本机为中文 Windows）。修改后请以相同编码保存。
chcp 936 >nul
title StackChan MCP Bridge - Quick Start
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv，请先运行 setup.cmd 初始化环境。
  pause & exit /b 1
)

if not exist ".env" (
  if not exist ".env.example" (
    echo [错误] 缺少 .env.example 模板文件，请检查文件是否完整。
    pause & exit /b 1
  )
  copy /y ".env.example" ".env" >nul
  echo 已创建本机配置文件 .env
)

findstr /i /c:"PASTE_YOUR" .env >nul
if not errorlevel 1 goto fill_env
goto check_format

:fill_env
echo.
echo == 填写接入点地址（只需一次）==
echo 即将打开记事本：请把 MCP_ENDPOINT= 后面整体替换为
echo 你在 StackChan World App 的 MCP 页面复制的完整接入点地址，
echo 然后保存并关闭记事本，脚本会自动继续。
echo.
echo 安全提醒：该地址含令牌，只保存在本机 .env 文件里，
echo 不要发给任何聊天工具、不要截图分享。
echo.
notepad .env
findstr /i /c:"PASTE_YOUR" .env >nul
if not errorlevel 1 (
  echo [错误] 未检测到真实接入点地址（.env 仍是占位符内容）。
  echo 请重新运行本脚本重试，或运行 edit-env.cmd 更新配置。
  pause & exit /b 1
)

:check_format
findstr /i /b /c:"MCP_ENDPOINT=wss://" .env >nul
if errorlevel 1 (
  echo [错误] .env 中 MCP_ENDPOINT 未配置或格式不正确（应以 wss:// 开头）。
  echo 请运行 edit-env.cmd 更新接入点地址。
  pause & exit /b 1
)

powershell -NoProfile -Command "if ((Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'python.exe' -and $_.CommandLine -like '*mcp_pipe.py*' }).Count -gt 0) { exit 7 } else { exit 0 }"
if errorlevel 7 if not errorlevel 8 (
  echo [提示] 检测到桥接已在运行中，无需重复启动。
  echo 如需重启：请先运行 stop-bridge.cmd 停止，再运行本脚本。
  pause & exit /b 1
)

echo.
echo ============================================================
echo  StackChan MCP 桥接启动中...
echo   · 停止：本窗口按 Ctrl+C，或关闭窗口，或运行 stop-bridge.cmd
echo   · 运行日志：logs\bridge.log（本窗口也会实时显示）
echo ============================================================
echo.
".venv\Scripts\python.exe" mcp_pipe.py

echo.
echo 桥接已退出。按任意键关闭窗口。
pause >nul

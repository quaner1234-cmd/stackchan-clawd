@echo off
rem 本文件必须保持 GBK (ANSI/CP936) 编码（本机为中文 Windows）。修改后请以相同编码保存。
chcp 936 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo [错误] 未找到 .venv，请先运行 setup.cmd 初始化环境。
  pause & exit /b 1
)
".venv\Scripts\python.exe" doctor.py %*
echo.
pause >nul

@echo off
rem 本文件必须保持 GBK (ANSI/CP936) 编码（本机为中文 Windows）。修改后请以相同编码保存。
chcp 936 >nul
cd /d "%~dp0.."

echo ============================================================
echo  StackChan MCP 桥接 — 本地自测（不接触真实云端）
echo ============================================================
echo.

echo [1/3] 计算器工具测试（MCP stdio 协议）...
".venv\Scripts\python.exe" tests\test_calculator_stdio.py
if errorlevel 1 (
  echo.
  echo 计算器测试未通过：请保留上面的输出，供排查使用。
  pause
  exit /b 1
)
echo.

echo [2/3] 桥接全链路模拟测试（mock 云端）...
".venv\Scripts\python.exe" tests\test_pipe_mock_cloud.py
if errorlevel 1 (
  echo.
  echo 全链路测试未通过：请保留上面的输出，供排查使用。
  pause
  exit /b 1
)
echo.

echo [3/3] 安全对抗测试（沙箱逃逸/资源耗尽探测）...
".venv\Scripts\python.exe" tests\test_security_probe.py
if errorlevel 1 (
  echo.
  echo 安全测试未通过：请保留上面的输出，供排查使用。
  pause
  exit /b 1
)
echo.

echo 全部测试通过。下一步：运行 start-bridge.cmd 一键配置并启动。
pause

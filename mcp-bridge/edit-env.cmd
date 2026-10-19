@echo off
rem 本文件必须保持 GBK (ANSI/CP936) 编码（本机为中文 Windows）。修改后请以相同编码保存。
chcp 936 >nul
cd /d "%~dp0"
if not exist ".env.example" (
  echo [错误] 缺少 .env.example 模板文件，请检查文件是否完整。
  pause & exit /b 1
)
if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  echo 已创建本机配置文件 .env
)
echo.
echo 即将打开记事本。请把文件中这一行：
echo     MCP_ENDPOINT=wss://api.xiaozhi.me/mcp/?token=PASTE_YOUR_TOKEN_HERE
echo 改为你在 StackChan World App（MCP 页面）中复制的完整接入点地址。
echo.
echo 安全提醒：
echo   · 该地址含令牌，相当于密码，只保存在本机 .env 文件里
echo   · 不要发给任何聊天工具、不要截图分享
echo   · 保存并关闭记事本后回到这里
echo.
pause
notepad .env
echo.
echo 已保存。建议运行 doctor.cmd 检查配置；随后可用 start-bridge.cmd 启动桥接。
pause

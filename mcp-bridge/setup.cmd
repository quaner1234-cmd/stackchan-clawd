@echo off
rem 本文件必须保持 GBK (ANSI/CP936) 编码（本机为中文 Windows）。修改后请以相同编码保存。
chcp 936 >nul
cd /d "%~dp0"
echo == StackChan MCP 桥接：初始化 Python 环境 ==
echo.
where uv >nul 2>&1
if errorlevel 1 (
  echo [提示] 未找到 uv，将使用系统 Python 自带的 venv/pip 方式安装（需要 Python 3.10+）。
  where python >nul 2>&1
  if errorlevel 1 (
    echo [错误] 未找到 python。请先安装 Python 3.10+ 或 uv（https://docs.astral.sh/uv/）。
    pause & exit /b 1
  )
  python -m venv .venv
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
) else (
  if not exist ".venv\Scripts\python.exe" (
    uv venv .venv --python 3.12 --seed
  )
  uv pip install --python .venv\Scripts\python.exe -r requirements.txt
)
if errorlevel 1 (
  echo [错误] 环境安装失败，请查看上面的输出。
  pause & exit /b 1
)
echo.
echo [完成] 环境已就绪。下一步：运行 edit-env.cmd 填写接入点地址。
pause

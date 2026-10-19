"""
Simple MCP stdio <-> WebSocket pipe with optional unified config.
Version: 0.2.0（StackChan-prep 修改版，来源: github.com/78/mcp-calculator）

本副本修改点（详见 README.md「对官方 mcp_pipe.py 的修改」）:
  1) 日志双通道：控制台 INFO + logs/bridge.log 文件 DEBUG（滚动）
  2) 凭证脱敏：日志中 token=xxx 一律替换为 token=***；不向子进程传递 MCP_ENDPOINT
  3) 子进程 stderr 经 logging 记录（进入日志文件，便于留证）
  4) 常见连接错误附带中文排查提示（HTTP 401/400、超时等）
  5) .env 固定从本脚本所在目录加载；mcp_config.json 找不到时回退到脚本目录
  6) Windows 编码：子进程管道强制 UTF-8（mcp_config.json env + calculator.py），避免 GBK 解码问题

Usage (env):
    export MCP_ENDPOINT=<ws_endpoint>
    # Windows (PowerShell): $env:MCP_ENDPOINT = "<ws_endpoint>"

Start server process(es) from config:
Run all configured servers (default)
    python mcp_pipe.py

Run a single local server script (back-compat)
    python mcp_pipe.py path/to/server.py

Config discovery order:
    $MCP_CONFIG, then ./mcp_config.json

Env overrides:
    (none for proxy; uses current Python: python -m mcp_proxy)
"""

import asyncio
import re
import websockets
import subprocess
import logging
import logging.handlers
import os
import signal
import sys
import json
from urllib.parse import urlsplit
from dotenv import load_dotenv

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Auto-load environment variables from the .env file next to this script
load_dotenv(dotenv_path=os.path.join(_SCRIPT_DIR, '.env'))

# ---- 日志与凭证脱敏 -------------------------------------------------------
_TOKEN_RE = re.compile(r'(token=)[^&\s"\']+', re.IGNORECASE)


def _redact(text):
    """把字符串中的 token=xxx 替换为 token=***。"""
    return _TOKEN_RE.sub(r'\1***', text)


class RedactFilter(logging.Filter):
    """日志过滤器：确保任何日志记录里都不会出现接入点令牌。"""

    def filter(self, record):
        try:
            if isinstance(record.msg, str):
                record.msg = _redact(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        k: (_redact(v) if isinstance(v, str) else v)
                        for k, v in record.args.items()
                    }
                elif isinstance(record.args, (tuple, list)):
                    record.args = type(record.args)(
                        _redact(a) if isinstance(a, str) else a for a in record.args
                    )
        except Exception:
            pass
        return True


def _setup_logging():
    """控制台 INFO + 文件 DEBUG（滚动）；全部输出经令牌脱敏。"""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(fmt)
    console.addFilter(RedactFilter())

    log_dir = os.path.join(_SCRIPT_DIR, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, 'bridge.log'),
        maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    file_handler.addFilter(RedactFilter())

    root.handlers = [console, file_handler]
    logging.getLogger('websockets').setLevel(logging.INFO)


_setup_logging()
logger = logging.getLogger('MCP_PIPE')

# 记录待回包的工具调用 id（用于在日志里留证「工具被真正调用」）
_pending_tool_ids = set()

def _child_env():
    """子进程环境变量：复制当前环境，但不包含接入点（其中含令牌）。"""
    env = os.environ.copy()
    env.pop('MCP_ENDPOINT', None)
    return env


def _friendly_hint(exc):
    """把常见连接错误翻译成可操作的中文提示（详见 README「故障排查」）。"""
    text = f"{type(exc).__name__}: {exc}"
    if '401' in text:
        return ('接入点令牌无效或已失效（HTTP 401）。请在 StackChan World App 的 MCP 页面'
                '重新复制接入点地址，再运行 edit-env.cmd 更新 .env。')
    if '400' in text:
        return ('接入点地址格式不正确（HTTP 400）。应形如 wss://api.xiaozhi.me/mcp/?token=...，'
                '请重新完整复制。')
    if 'timed out during opening handshake' in text or 'TimeoutError' in text:
        return ('连接握手超时。请先运行 doctor.cmd connect 测试网络可达性；'
                '若持续超时，参见 README「故障排查」中关于 websockets 版本的说明。')
    if 'getaddrinfo' in text or 'Name or service not known' in text:
        return 'DNS 解析失败。检查本机网络与 DNS 设置后重试。'
    if 'CERTIFICATE' in text.upper() or 'SSL' in text.upper():
        return 'TLS/证书错误。若正在使用抓包或代理工具请先关闭，再重试。'
    return None


# Reconnection settings
INITIAL_BACKOFF = 1  # Initial wait time in seconds
MAX_BACKOFF = 600  # Maximum wait time in seconds

async def connect_with_retry(uri, target):
    """Connect to WebSocket server with retry mechanism for a given server target."""
    reconnect_attempt = 0
    backoff = INITIAL_BACKOFF
    while True:  # Infinite reconnection
        try:
            if reconnect_attempt > 0:
                logger.info(f"[{target}] Waiting {backoff}s before reconnection attempt {reconnect_attempt}...")
                await asyncio.sleep(backoff)

            # Attempt to connect
            await connect_to_server(uri, target)

        except Exception as e:
            reconnect_attempt += 1
            logger.warning(f"[{target}] Connection closed (attempt {reconnect_attempt}): {e}")
            hint = _friendly_hint(e)
            if hint:
                logger.warning(f"[{target}] 提示: {hint}")
            # Calculate wait time for next reconnection (exponential backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

async def connect_to_server(uri, target):
    """Connect to WebSocket server and pipe stdio for the given server target."""
    try:
        logger.info(f"[{target}] Connecting to WebSocket server...")
        async with websockets.connect(uri) as websocket:
            logger.info(f"[{target}] Successfully connected to WebSocket server")

            # Start server process (built from CLI arg or config)
            cmd, env = build_server_command(target)
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                encoding='utf-8',
                text=True,
                env=env
            )
            logger.info(f"[{target}] Started server process: {' '.join(cmd)}")
            
            # Create two tasks: read from WebSocket and write to process, read from process and write to WebSocket
            await asyncio.gather(
                pipe_websocket_to_process(websocket, process, target),
                pipe_process_to_websocket(process, websocket, target),
                pipe_process_stderr_to_terminal(process, target)
            )
    except websockets.exceptions.ConnectionClosed as e:
        logger.error(f"[{target}] WebSocket connection closed: {e}")
        raise  # Re-throw exception to trigger reconnection
    except Exception as e:
        logger.error(f"[{target}] Connection error: {e}")
        raise  # Re-throw exception
    finally:
        # Ensure the child process is properly terminated
        if 'process' in locals():
            logger.info(f"[{target}] Terminating server process")
            try:
                process.terminate()
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
            logger.info(f"[{target}] Server process terminated")

async def pipe_websocket_to_process(websocket, process, target):
    """Read data from WebSocket and write to process stdin"""
    try:
        while True:
            # Read message from WebSocket
            message = await websocket.recv()
            if isinstance(message, bytes):
                message = message.decode('utf-8', errors='replace')

            logger.debug(f"[{target}] << {message[:2000]}")

            # 识别云端下发的工具调用（INFO 留证，验收时可在日志中看到）
            try:
                obj = json.loads(message)
                if obj.get("method") == "tools/call":
                    call_name = (obj.get("params") or {}).get("name")
                    logger.info(f"[{target}] Tool call: {call_name}")
                    if isinstance(obj.get("id"), (int, str)):
                        _pending_tool_ids.add(obj["id"])
            except Exception:
                pass

            # Write to process stdin (in text mode)
            process.stdin.write(message.rstrip('\r\n') + '\n')
            process.stdin.flush()
    except Exception as e:
        logger.error(f"[{target}] Error in WebSocket to process pipe: {e}")
        raise  # Re-throw exception to trigger reconnection
    finally:
        # Close process stdin
        if not process.stdin.closed:
            process.stdin.close()

async def pipe_process_to_websocket(process, websocket, target):
    """Read data from process stdout and send to WebSocket"""
    try:
        while True:
            # Read data from process stdout
            data = await asyncio.to_thread(process.stdout.readline)

            if not data:  # If no data, the process may have ended
                logger.info(f"[{target}] Process has ended output")
                break

            logger.debug(f"[{target}] >> {data[:2000]}")

            # 若这是对某次工具调用的回复，额外记一条 INFO 作为验收证据
            try:
                obj = json.loads(data)
                if "id" in obj and obj["id"] in _pending_tool_ids:
                    _pending_tool_ids.discard(obj["id"])
                    logger.info(f"[{target}] Tool result: {data[:2000].strip()}")
            except Exception:
                pass

            # In text mode, data is already a string, no need to decode
            await websocket.send(data)
    except Exception as e:
        logger.error(f"[{target}] Error in process to WebSocket pipe: {e}")
        raise  # Re-throw exception to trigger reconnection

async def pipe_process_stderr_to_terminal(process, target):
    """Read data from process stderr and print to terminal"""
    try:
        while True:
            # Read data from process stderr
            data = await asyncio.to_thread(process.stderr.readline)
            
            if not data:  # If no data, the process may have ended
                logger.info(f"[{target}] Process has ended stderr output")
                break
                
            # 子进程 stderr 统一走 logging：输出到控制台，并写入日志文件
            logger.info(f"[{target}] {data.rstrip()}")
    except Exception as e:
        logger.error(f"[{target}] Error in process stderr pipe: {e}")
        raise  # Re-throw exception to trigger reconnection

def signal_handler(sig, frame):
    """Handle interrupt signals"""
    logger.info("Received interrupt signal, shutting down...")
    sys.exit(0)

def load_config():
    """Load JSON config from $MCP_CONFIG or ./mcp_config.json. Return dict or {}."""
    path = os.environ.get("MCP_CONFIG") or os.path.join(os.getcwd(), "mcp_config.json")
    if not os.path.exists(path):
        fallback = os.path.join(_SCRIPT_DIR, "mcp_config.json")
        if os.path.exists(fallback):
            path = fallback
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load config {path}: {e}")
        return {}


def build_server_command(target=None):
    """Build [cmd,...] and env for the server process for a given target.

    Priority:
    - If target matches a server in config.mcpServers: use its definition
    - Else: treat target as a Python script path (back-compat)
    If target is None, read from sys.argv[1].
    """
    if target is None:
        assert len(sys.argv) >= 2, "missing server name or script path"
        target = sys.argv[1]
    cfg = load_config()
    servers = cfg.get("mcpServers", {}) if isinstance(cfg, dict) else {}

    if target in servers:
        entry = servers[target] or {}
        if entry.get("disabled"):
            raise RuntimeError(f"Server '{target}' is disabled in config")
        typ = (entry.get("type") or entry.get("transportType") or "stdio").lower()

        # environment for child process（不传入 MCP_ENDPOINT，减少令牌暴露面）
        child_env = _child_env()
        for k, v in (entry.get("env") or {}).items():
            child_env[str(k)] = str(v)

        if typ == "stdio":
            command = entry.get("command")
            args = entry.get("args") or []
            if not command:
                raise RuntimeError(f"Server '{target}' is missing 'command'")
            return [command, *args], child_env

        if typ in ("sse", "http", "streamablehttp"):
            url = entry.get("url")
            if not url:
                raise RuntimeError(f"Server '{target}' (type {typ}) is missing 'url'")
            # Unified approach: always use current Python to run mcp-proxy module
            cmd = [sys.executable, "-m", "mcp_proxy"]
            if typ in ("http", "streamablehttp"):
                cmd += ["--transport", "streamablehttp"]
            # optional headers: {"Authorization": "Bearer xxx"}
            headers = entry.get("headers") or {}
            for hk, hv in headers.items():
                cmd += ["-H", hk, str(hv)]
            cmd.append(url)
            return cmd, child_env

        raise RuntimeError(f"Unsupported server type: {typ}")

    # Fallback to script path (back-compat)
    script_path = target
    if not os.path.exists(script_path):
        raise RuntimeError(
            f"'{target}' is neither a configured server nor an existing script"
        )
    return [sys.executable, script_path], _child_env()

if __name__ == "__main__":
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    
    # Get token from environment variable or command line arguments
    endpoint_url = os.environ.get('MCP_ENDPOINT')
    if not endpoint_url:
        logger.error("Please set the `MCP_ENDPOINT` environment variable "
                     "(未配置接入点：请运行 edit-env.cmd 填写 .env，详见 README.md)")
        sys.exit(1)
    if 'PASTE_YOUR' in endpoint_url:
        logger.error("MCP_ENDPOINT 仍是占位符。请在 StackChan World App 的 MCP 页面复制"
                     "接入点地址，运行 edit-env.cmd 更新 .env 后重试（详见 README.md）")
        sys.exit(1)
    if not endpoint_url.startswith(('wss://', 'ws://')):
        logger.error("MCP_ENDPOINT 必须以 wss:// 开头（本机自测可用 ws://）")
        sys.exit(1)
    _ep = urlsplit(endpoint_url)
    logger.info(f"接入点: {_ep.scheme}://{_ep.netloc}{_ep.path} (token 已隐藏)")
    
    # Determine target: default to all if no arg; single target otherwise
    target_arg = sys.argv[1] if len(sys.argv) >= 2 else None

    async def _main():
        if not target_arg:
            cfg = load_config()
            servers_cfg = (cfg.get("mcpServers") or {})
            all_servers = list(servers_cfg.keys())
            enabled = [name for name, entry in servers_cfg.items() if not (entry or {}).get("disabled")]
            skipped = [name for name in all_servers if name not in enabled]
            if skipped:
                logger.info(f"Skipping disabled servers: {', '.join(skipped)}")
            if not enabled:
                raise RuntimeError("No enabled mcpServers found in config")
            logger.info(f"Starting servers: {', '.join(enabled)}")
            tasks = [asyncio.create_task(connect_with_retry(endpoint_url, t)) for t in enabled]
            # Run all forever; if any crashes it will auto-retry inside
            await asyncio.gather(*tasks)
        else:
            if os.path.exists(target_arg):
                await connect_with_retry(endpoint_url, target_arg)
            else:
                logger.error("Argument must be a local Python script path. To run configured servers, run without arguments.")
                sys.exit(1)

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        logger.info("Program interrupted by user")
    except Exception as e:
        logger.error(f"Program execution error: {e}")
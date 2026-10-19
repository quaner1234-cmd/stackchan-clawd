# -*- coding: utf-8 -*-
"""doctor.py — StackChan MCP 桥接自检工具（绝不显示令牌内容）。

用法：
    python doctor.py            检查环境与 .env 配置
    python doctor.py connect    额外做一次真实连接测试（验证令牌是否有效）
    python doctor.py ask        私密输入一个接入点地址并测试其是否有效
                                （用于验证旧地址是否已失效；输入不回显）
"""
import asyncio
import getpass
import os
import re
import subprocess
import sys

BRIDGE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BRIDGE, ".venv", "Scripts", "python.exe")
ENV_FILE = os.path.join(BRIDGE, ".env")
TOKEN_RE = re.compile(r"(token=)[^&\s\"']+", re.IGNORECASE)


def redact(text):
    return TOKEN_RE.sub(r"\1***", text)


def line_ok(msg):
    print(f"  [OK] {msg}")


def line_bad(msg):
    print(f"  [!!] {msg}")


def line_warn(msg):
    print(f"  [??] {msg}")


def load_endpoint():
    if not os.path.exists(ENV_FILE):
        return None, ".env 文件不存在（可运行 edit-env.cmd 创建）"
    endpoint = None
    with open(ENV_FILE, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("MCP_ENDPOINT="):
                endpoint = line.split("=", 1)[1].strip()
    if not endpoint:
        return None, ".env 中没有 MCP_ENDPOINT 配置行"
    return endpoint, None


def validate_format(endpoint):
    """返回 (ok, message)。绝不在消息中回显地址内容。"""
    if "PASTE_YOUR" in endpoint:
        return False, "地址还是占位符，请先运行 edit-env.cmd 粘贴真实接入点地址"
    if not endpoint.startswith(("wss://", "ws://")):
        return False, "地址必须以 wss:// 开头"
    if "/mcp/" not in endpoint:
        return False, "地址中缺少 /mcp/ 路径，请重新完整复制"
    if "token=" not in endpoint:
        return False, "地址中缺少 token 参数，请重新完整复制"
    if endpoint.startswith("ws://"):
        return True, "（注意：当前为 ws:// 明文连接；正式配置应使用 App 里复制的 wss:// 地址）"
    return True, None


async def ws_test(url, timeout=15):
    import websockets
    try:
        async with websockets.connect(url, open_timeout=timeout):
            return "ok", None
    except Exception as exc:  # noqa: BLE001
        return "err", exc


def classify(exc):
    text = f"{type(exc).__name__}: {exc}"
    if "401" in text:
        return "令牌无效或已失效（HTTP 401）。请在手机 App 重新获取接入点地址后更新 .env。"
    if "400" in text:
        return "地址被服务器拒绝（HTTP 400），地址格式可能不完整，请重新完整复制。"
    if "timed out" in text.lower() or "Timeout" in text:
        return "连接超时：网络无法到达或速度太慢。可稍后重试，或换一个网络环境。"
    if "CERTIFICATE" in text.upper() or "SSL" in text.upper():
        return "TLS/证书错误：若正在使用抓包/代理/加速工具请先关闭，再重试。"
    return "连接失败：" + redact(text)


def check_env():
    print("== 1. Python 环境 ==")
    problems = 0
    if not os.path.exists(PY):
        line_bad("未找到 .venv，请先运行 setup.cmd")
        return 1
    line_ok(f"venv 存在")
    try:
        r = subprocess.run(
            [PY, "-c", "import websockets, fastmcp, mcp; print(websockets.__version__); print(getattr(fastmcp, '__version__', 'ok'))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BRIDGE, timeout=120)
        if r.returncode == 0:
            vers = r.stdout.strip().splitlines()
            line_ok(f"依赖可导入（websockets {vers[0]} / fastmcp {vers[1] if len(vers)>1 else 'ok'}）")
        else:
            line_bad("依赖导入失败：" + (r.stderr or r.stdout or "").strip()[:300])
            problems += 1
    except Exception as exc:
        line_bad(f"依赖检查异常: {exc}")
        problems += 1

    print("== 2. 计算器模块 ==")
    try:
        r = subprocess.run(
            [PY, "-c", "import calculator; print('calculator OK')"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=BRIDGE, timeout=120)
        if r.returncode == 0 and "calculator OK" in r.stdout:
            line_ok("calculator.py 可加载")
        else:
            line_bad("calculator.py 加载失败：" + (r.stderr or r.stdout or "").strip()[:300])
            problems += 1
    except Exception as exc:
        line_bad(f"计算器检查异常: {exc}")
        problems += 1

    print("== 3. 接入点配置（.env）==")
    endpoint, err = load_endpoint()
    if err:
        line_bad(err)
        problems += 1
    else:
        okfmt, note = validate_format(endpoint)
        if okfmt:
            line_ok("接入点地址已配置，格式正确（token 内容不会显示）")
            if note:
                line_warn(note)
        else:
            line_bad(note)
            problems += 1

    return problems


def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "").strip().lower()
    problems = check_env()

    if mode == "connect":
        print("== 4. 真实连接测试 ==")
        endpoint, err = load_endpoint()
        if err:
            line_bad("无法测试：" + err)
            problems += 1
        else:
            okfmt, note = validate_format(endpoint)
            if not okfmt:
                line_bad("无法测试：" + note)
                problems += 1
            else:
                status, exc = asyncio.run(ws_test(endpoint))
                if status == "ok":
                    line_ok("连接成功：接入点在线、令牌有效（已立即断开，未发送任何数据）")
                else:
                    line_bad(classify(exc))
                    problems += 1
    elif mode == "ask":
        print("== 私密测试一个接入点地址（输入不回显）==")
        print("（用于验证某个旧地址是否仍有效。请勿把地址粘贴到任何聊天窗口；")
        print("  直接在窗口里粘贴后回车即可，内容不会显示、不会写入任何文件。）")
        url = getpass.getpass("请粘贴接入点地址后回车（直接回车则取消）: ").strip()
        if not url:
            print("已取消。")
            return 0 if problems == 0 else 1
        okfmt, note = validate_format(url)
        if not okfmt:
            line_bad("地址格式不对：" + note)
            return 1
        status, exc = asyncio.run(ws_test(url))
        if status == "ok":
            line_ok("连接成功：该地址当前有效。")
        else:
            line_bad(classify(exc))
    elif mode not in ("", "check"):
        print(f"未知参数: {mode}（可用: connect / ask）")
        return 1

    print()
    if problems:
        print(f"自检发现问题数：{problems}。请按上面的 [!!] 项处理；可参考 README.md「故障排查」。")
        return 1
    print("自检通过。若尚未测试真实连接，可运行: doctor.cmd connect")
    return 0


if __name__ == "__main__":
    sys.exit(main())

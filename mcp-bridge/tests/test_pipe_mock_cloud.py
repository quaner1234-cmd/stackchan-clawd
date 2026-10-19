# -*- coding: utf-8 -*-
"""全链路本地联调测试：mock 云端 + mcp_pipe + calculator（不接触真实云端）。

模拟小智云端作为 MCP 客户端的行为：initialize → initialized → tools/list →
tools/call（正常 + 恶意）。验证「云端 → 桥接 → 计算器 → 返回」完整链路，
并检查桥接日志中留有工具调用证据、且日志中不出现令牌。
"""
import asyncio
import json
import os
import subprocess
import sys
import threading

import websockets
from websockets.asyncio.server import serve

BRIDGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(BRIDGE, ".venv", "Scripts", "python.exe")
PIPE = os.path.join(BRIDGE, "mcp_pipe.py")
LOG = os.path.join(BRIDGE, "logs", "bridge.log")
PORT = 18765
TOKEN = "mock-test-token-DO-NOT-LEAK"
ENDPOINT = f"ws://127.0.0.1:{PORT}/mcp/?token={TOKEN}"


class CloudClient:
    """模拟小智云端的 MCP 客户端行为。"""

    def __init__(self, ws, results):
        self.ws = ws
        self.results = results

    async def _recv_obj(self, timeout):
        while True:
            raw = await asyncio.wait_for(self.ws.recv(), timeout)
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            try:
                return json.loads(raw)
            except Exception:
                self.results.setdefault("junk_frames", []).append(raw[:200])

    async def request(self, method, params, rid, timeout=30):
        await self.ws.send(json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}))
        while True:
            obj = await self._recv_obj(timeout)
            if obj.get("id") == rid:
                return obj
            self.results.setdefault("other_frames", []).append(obj)

    async def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        await self.ws.send(json.dumps(msg))


def extract_payload(resp):
    result = resp.get("result") or {}
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for item in result.get("content") or []:
        if item.get("type") == "text":
            try:
                return json.loads(item.get("text", ""))
            except Exception:
                pass
    raise AssertionError(f"无法提取结果: {json.dumps(resp)[:400]}")


async def scenario(ws, results):
    c = CloudClient(ws, results)
    try:
        r = await c.request("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "mock-xiaozhi-cloud", "version": "0.0.1"},
        }, 1)
        results["initialize"] = r
        await c.notify("notifications/initialized")

        r = await c.request("tools/list", {}, 2)
        results["tools"] = r

        r = await c.request("tools/call", {
            "name": "calculator", "arguments": {"python_expression": "123 * 456"}}, 3)
        results["call_ok"] = r

        r = await c.request("tools/call", {
            "name": "calculator",
            "arguments": {"python_expression": "__import__('os').system('echo pwned')"}}, 4)
        results["call_blocked"] = r

        results["scenario"] = "complete"
    except Exception as exc:
        results["scenario"] = "error"
        results["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await ws.close()
        except Exception:
            pass


def main():
    results = {}
    log_start = os.path.getsize(LOG) if os.path.exists(LOG) else 0
    done = asyncio.Event()

    async def handler(ws):
        try:
            await scenario(ws, results)
        finally:
            done.set()

    async def run():
        async with serve(handler, "127.0.0.1", PORT):
            env = os.environ.copy()
            env["MCP_ENDPOINT"] = ENDPOINT
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                [PY, PIPE], cwd=BRIDGE, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
            )
            out_lines = []

            def drain():
                for line in proc.stdout:
                    out_lines.append(line)
            threading.Thread(target=drain, daemon=True).start()

            try:
                await asyncio.wait_for(done.wait(), timeout=90)
            except asyncio.TimeoutError:
                results.setdefault("scenario", "timeout")
            await asyncio.sleep(1.0)

            # 清理：杀掉桥接进程树（含计算器子进程）
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True)
            await asyncio.sleep(0.5)
            return out_lines

    out_lines = asyncio.run(run())

    # ---- 断言 ----
    failures = 0

    def check(cond, ok_msg, bad_msg):
        nonlocal failures
        if cond:
            print("[OK] " + ok_msg)
        else:
            print("[!!] " + bad_msg)
            failures += 1

    check(results.get("scenario") == "complete",
          "场景执行完成", f"场景未完成: {results.get('scenario')} / {results.get('error')}")

    init = results.get("initialize") or {}
    check(bool((init.get("result") or {}).get("serverInfo")),
          "initialize 正常", f"initialize 异常: {json.dumps(init)[:200]}")

    tools = ((results.get("tools") or {}).get("result") or {}).get("tools") or []
    check(any(t.get("name") == "calculator" for t in tools),
          f"tools/list 包含 calculator（{len(tools)} 个工具）", f"tools/list 异常: {tools}")

    try:
        payload_ok = extract_payload(results.get("call_ok") or {})
    except Exception:
        payload_ok = {}
    check(payload_ok.get("success") is True and payload_ok.get("result") == 56088,
          "正常调用返回 123*456=56088", f"正常调用结果异常: {payload_ok}")

    try:
        payload_bad = extract_payload(results.get("call_blocked") or {})
    except Exception:
        payload_bad = {}
    check(payload_bad.get("success") is False,
          "恶意调用被安全拒绝", f"恶意调用未被拒绝: {payload_bad}")

    if results.get("junk_frames"):
        check(False, "", f"云端收到非 JSON 垃圾帧: {results['junk_frames'][:2]}")
    if results.get("other_frames"):
        print("     （另有 {} 条通知帧，属正常）".format(len(results["other_frames"])))

    # ---- 日志检查（验收证据）----
    new_log = ""
    if os.path.exists(LOG):
        with open(LOG, encoding="utf-8", errors="replace") as f:
            f.seek(log_start)
            new_log = f.read()

    check("Tool call: calculator" in new_log, "日志含工具调用记录（Tool call: calculator）",
          "日志缺少工具调用记录")
    check("Calculating formula: 123 * 456, result: 56088" in new_log,
          "日志含计算证据（Calculating formula: 123 * 456, result: 56088）",
          "日志缺少计算证据")
    check(TOKEN not in new_log, "日志中未出现令牌（脱敏生效）", "!! 日志中出现了令牌字符串")

    if failures:
        print()
        print("---- 桥接进程输出（末尾 40 行）----")
        print("".join(out_lines[-40:]))
        print("---- bridge.log 新增段（末尾 60 行）----")
        print("\n".join(new_log.splitlines()[-60:]))

    print()
    if failures:
        print(f"结果：{failures} 项失败")
        return 1
    print("结果：全部通过（PASS）（本测试完全在本地进行，未连接真实云端）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

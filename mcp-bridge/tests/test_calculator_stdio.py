# -*- coding: utf-8 -*-
"""calculator.py 单元/协议测试：以标准 MCP stdio 协议驱动计算器服务器。

用途：不接触真实云端，验证「计算器」工具本身：初始化 → 列工具 → 调用工具，
并包含恶意输入与超大计算的安全拒绝用例。
"""
import json
import os
import subprocess
import sys
import time

BRIDGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = os.path.join(BRIDGE, ".venv", "Scripts", "python.exe")
CALC = os.path.join(BRIDGE, "calculator.py")


class StdioServer:
    """极简 MCP stdio 客户端（仅测试用）。"""

    def __init__(self):
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["NO_COLOR"] = "1"
        self.proc = subprocess.Popen(
            [PY, CALC],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", cwd=BRIDGE, env=env,
        )
        self._id = 0

    def request(self, method, params=None, timeout=30):
        self._id += 1
        rid = self._id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("服务器提前关闭了 stdout（查看 stderr 输出）")
            try:
                obj = json.loads(line)
            except Exception:
                continue  # 忽略非协议行
            if obj.get("id") == rid:
                return obj
        raise TimeoutError(f"{method} 超过 {timeout}s 未响应")

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            pass
        try:
            err = self.proc.stderr.read()
            if err:
                print("---- 服务器 stderr ----")
                print(err[-1500:])
                print("-----------------------")
        except Exception:
            pass


def extract_payload(resp):
    """从 tools/call 响应中取出工具返回的 dict。"""
    result = resp.get("result") or {}
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for item in result.get("content") or []:
        if item.get("type") == "text":
            try:
                return json.loads(item.get("text", ""))
            except Exception:
                pass
    raise AssertionError(f"无法从响应中提取结果: {json.dumps(resp)[:400]}")


OK_CASES = [
    # (表达式, 校验函数, 说明)
    ("3.14159 * (8 / 2) ** 2", lambda v: abs(float(v) - 50.26544) < 1e-3, "官方文档示例"),
    ("123 * 456", lambda v: v == 56088, "乘法"),
    ("math.comb(10, 3)", lambda v: v == 120, "组合数（math. 前缀）"),
    ("sqrt(144) + abs(-3)", lambda v: v == 15.0, "开方（裸函数名）+ 绝对值"),
    ("2 ** 10", lambda v: v == 1024, "幂运算"),
    ("round(math.pi, 4)", lambda v: v == 3.1416, "四舍五入 + 常量"),
    ("min(3, 4, 5)", lambda v: v == 3, "最小值"),
    ("random.randint(1, 6)", lambda v: isinstance(v, int) and 1 <= v <= 6, "随机整数"),
    ("10 % 3", lambda v: v == 1, "取模"),
    ("random.sample([1, 2, 3, 4, 5], 2)", lambda v: isinstance(v, list) and len(v) == 2, "列表参数 + 抽样"),
]

BAD_CASES = [
    # (表达式, 说明) — 必须被安全拒绝（success=False），且不能有副作用
    ("__import__('os').system('echo pwned')", "尝试导入 os（沙箱逃逸）"),
    ("().__class__.__bases__[0].__subclasses__()", "经典沙箱逃逸链"),
    ("open('x.txt')", "尝试打开文件"),
    ("eval('1+1')", "尝试调用 eval"),
    ("10**1000000", "指数炸弹"),
    ("math.factorial(999999)", "超大阶乘"),
    ("1 / 0", "除零错误（应返回错误而不是崩溃）"),
    ("math.sqrt(-1)", "负数开方（应返回错误）"),
    ("import os", "import 语句"),
    ("'a' * 5", "字符串常量"),
]


def main():
    if not os.path.exists(PY):
        print("!! 未找到 venv：", PY)
        return 1

    srv = StdioServer()
    failures = 0
    try:
        # 1) initialize
        r = srv.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "stackchan-prep-test", "version": "0.1"},
        })
        if "result" in r and r["result"].get("serverInfo"):
            print("[OK] initialize:", r["result"]["serverInfo"].get("name"))
        else:
            print("[!!] initialize 响应异常:", json.dumps(r)[:300])
            failures += 1
        srv.notify("notifications/initialized")

        # 2) tools/list
        r = srv.request("tools/list", {})
        tools = (r.get("result") or {}).get("tools") or []
        names = [t.get("name") for t in tools]
        if "calculator" in names:
            print("[OK] tools/list 包含 calculator；工具数:", len(tools))
        else:
            print("[!!] tools/list 缺少 calculator:", names)
            failures += 1

        # 3) 正常用例
        for expr, check, desc in OK_CASES:
            r = srv.request("tools/call", {"name": "calculator", "arguments": {"python_expression": expr}})
            try:
                payload = extract_payload(r)
                good = payload.get("success") is True and check(payload.get("result"))
            except Exception as exc:
                payload = {"error": repr(exc)}
                good = False
            print(("[OK] " if good else "[!!] ") + f"计算 {desc}: {expr} -> {json.dumps(payload, ensure_ascii=False)[:120]}")
            failures += 0 if good else 1

        # 4) 恶意/超限用例（必须被拒绝）
        for expr, desc in BAD_CASES:
            r = srv.request("tools/call", {"name": "calculator", "arguments": {"python_expression": expr}})
            try:
                payload = extract_payload(r)
                good = payload.get("success") is False
            except Exception as exc:
                payload = {"error": repr(exc)}
                good = False
            tag = "[OK] " if good else "[!!] "
            print(tag + f"安全拒绝 {desc}: {json.dumps(payload, ensure_ascii=False)[:140]}")
            failures += 0 if good else 1
    finally:
        srv.close()

    print()
    if failures:
        print(f"结果：{failures} 项失败")
        return 1
    print("结果：全部通过（PASS）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

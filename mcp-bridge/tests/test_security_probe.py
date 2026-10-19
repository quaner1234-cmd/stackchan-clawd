# -*- coding: utf-8 -*-
"""安全回归测试：对抗性输入探测（纯本地，直接调用 calculator.safe_eval）。

覆盖：沙箱逃逸（__import__ / __class__ 链 / eval·exec·open 等）、资源耗尽
（指数炸弹 / 超大阶乘 / 列表内存放大）、语法走私（lambda / 推导式 / 字典 /
字符串常量）、复数结果拒绝。任何一项未被拦截即为失败。
"""
import os
import sys
import time

BRIDGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BRIDGE)

from calculator import safe_eval  # noqa: E402

ESCAPES = [
    "__import__('os').system('echo pwned')",
    "().__class__.__bases__[0].__subclasses__()",
    "(1).__class__.__mro__",
    "globals()",
    "locals()",
    "vars()",
    "dir()",
    "getattr(1, '__class__')",
    "setattr(1, 'x', 2)",
    "eval('1+1')",
    "exec('1+1')",
    "compile('1', '<s>', 'eval')",
    "open('x.txt')",
    "input()",
    "print(1)",
    "type(1)",
    "memoryview(b'x')",
    "os.system('echo hi')",
    "math.__dict__",
    "int.__class__",
    "float.__subclasses__()",
    "str.__mro__",
    "lambda: 0",
    "[x for x in [1]]",
    "{1: 2}",
    "'abc'",
    "b'abc'",
    "[0] * 10**9",
    "(0,) * 10**9",
    "[[0]*1000] * 1000",
    "(-8) ** 0.5",
    "10**1000000",
    "math.factorial(999999)",
]

LEGIT = [
    ("123 * 456", 56088),
    ("2 ** 10", 1024),
    ("math.sqrt(144) + abs(-3)", 15.0),
    ("round(math.pi, 4)", 3.1416),
    ("min(3, 4, 5)", 3),
    ("10 % 3", 1),
    ("math.comb(10, 3)", 120),
]


def main():
    fails = 0
    slow = 0
    for e in ESCAPES:
        t0 = time.time()
        try:
            v = safe_eval(e)
            print("[!!] NOT BLOCKED: %r -> %s" % (e, repr(v)[:100]))
            fails += 1
        except Exception as ex:
            dt = time.time() - t0
            note = ""
            if dt > 1.0:
                note = "  (SLOW %.2fs)" % dt
                slow += 1
            print("[OK] blocked: %-58r %s: %s%s" % (e, type(ex).__name__, str(ex)[:40], note))

    for expr, want in LEGIT:
        try:
            v = safe_eval(expr)
            ok = abs(float(v) - want) < 1e-9
        except Exception as ex:
            v, ok = "EXC:%s" % ex, False
        print(("[OK] " if ok else "[!!] ") + "legit: %s -> %r" % (expr, v))
        fails += 0 if ok else 1

    print()
    print("escape attempts: %d, failures: %d, slow(>1s): %d" % (len(ESCAPES), fails, slow))
    if fails or slow:
        print("结果：失败")
        return 1
    print("结果：全部通过（PASS）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

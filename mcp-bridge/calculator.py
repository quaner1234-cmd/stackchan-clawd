"""
calculator.py — StackChan 安全计算器（MCP 工具服务器）

职责
    为 StackChan（小智 AI）提供「计算器」工具：
    语音 → 云端大模型 → 调用本工具 → 在本机求值 → 结果原路返回。

与官方示例的差异（官方示例: github.com/78/mcp-calculator）
    官方 calculator.py 使用 eval()，存在沙箱逃逸风险（CPython 会自动注入
    __builtins__，可通过 __class__ 链触达 os/subprocess 等，见该仓库 issue #45）。
    本文件改为 AST 白名单求值器，只开放无副作用的数学能力：
      · 允许：数字常量、+ - * / // % **、括号、比较、少量安全内置函数
      · 允许：math 模块的只读函数与常量（math.sqrt / math.pi / math.comb ...）
      · 允许：random 模块的常用函数（random.random / random.randint ...）
      · 禁止：import、字符串、属性链、下标、推导式、lambda、列表重复运算等其余一切语法
      · 限额：表达式长度、指数大小、阶乘参数、列表长度、结果位数、返回体积

启动
    由 mcp_pipe.py 以 stdio 子进程方式拉起（python calculator.py），无需手动运行。
"""
from __future__ import annotations

import ast
import json
import logging
import math
import operator
import random
import sys

# Windows 控制台编码修正（stderr 用于日志，stdout 留给 MCP 协议）
if sys.platform == "win32":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("Calculator")

# ---------- 限额 ----------
MAX_EXPR_LEN = 500          # 表达式最大长度
MAX_INT_BITS = 3000         # 整数结果最大位数（约 900 位十进制）
MAX_POW_EXP = 100_000       # 幂运算指数上限（另配合结果位数估算）
MAX_FACTORIAL_ARG = 1000    # 阶乘参数上限（防卡死）
MAX_SEQ_LEN = 1000          # 列表/元组最大元素数（防序列放大攻击）
MAX_PAYLOAD_BYTES = 1000    # 返回 JSON 最大字节数（云端对返回值有大小要求）

# ---------- 白名单 ----------
_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod,
}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}


def _safe_pow(base, exp):
    if isinstance(exp, (int, float)):
        if not (-MAX_POW_EXP <= exp <= MAX_POW_EXP):
            raise ValueError("指数过大，已拒绝计算")
        try:
            ab = abs(base)
            if isinstance(ab, int) and ab > 1:
                if ab.bit_length() * abs(exp) > MAX_INT_BITS:
                    raise ValueError("结果过大，已拒绝计算")
            elif isinstance(ab, float) and ab > 1:
                if math.log2(ab) * abs(exp) > MAX_INT_BITS:
                    raise ValueError("结果过大，已拒绝计算")
        except OverflowError:
            raise ValueError("结果过大，已拒绝计算")
    return base ** exp


def _safe_factorial(x):
    if isinstance(x, int) and abs(x) > MAX_FACTORIAL_ARG:
        raise ValueError("阶乘参数过大，已拒绝计算")
    return math.factorial(x)


def _safe_comb(n, k):
    if isinstance(n, int) and isinstance(k, int) and n >= 0 and 0 <= k <= n:
        if min(k, n - k) > 10_000:
            raise ValueError("组合数参数过大，已拒绝计算")
    return math.comb(n, k)


def _safe_perm(n, k):
    if isinstance(n, int) and isinstance(k, int) and n >= 0 and 0 <= k <= n:
        if k > 10_000:
            raise ValueError("排列数参数过大，已拒绝计算")
    return math.perm(n, k)


_MATH_FUNCS = {
    name: getattr(math, name)
    for name in dir(math)
    if not name.startswith("_") and callable(getattr(math, name))
}
_MATH_FUNCS.update({"factorial": _safe_factorial, "comb": _safe_comb, "perm": _safe_perm})
_MATH_CONSTS = {n: getattr(math, n) for n in ("pi", "e", "tau", "inf", "nan") if hasattr(math, n)}
_RANDOM_FUNCS = {
    n: getattr(random, n)
    for n in ("random", "randint", "uniform", "choice", "gauss", "sample", "randrange")
    if hasattr(random, n)
}
_SAFE_BUILTINS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sum": sum, "int": int, "float": float, "pow": _safe_pow, "len": len,
}


def _check_seq_ops(left, right, op_type):
    """列表/元组运算防护：禁止重复运算（内存放大），限制拼接总长。"""
    seq_left = isinstance(left, (list, tuple))
    seq_right = isinstance(right, (list, tuple))
    if op_type is ast.Mult and (seq_left or seq_right):
        raise ValueError("不允许对列表做重复运算")
    if op_type is ast.Add and seq_left and seq_right and len(left) + len(right) > MAX_SEQ_LEN:
        raise ValueError("列表长度超限，已拒绝计算")


def _eval_node(node):
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or node.value is None:
            return node.value
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("只允许数字常量")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow):
            return _safe_pow(left, right)
        if isinstance(node.op, (ast.Mult, ast.Add)):
            _check_seq_ops(left, right, type(node.op))
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise ValueError("不允许的运算符")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise ValueError("不允许的一元运算符")
        return op(_eval_node(node.operand))
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left)
        for op_node, comp in zip(node.ops, node.comparators):
            op = _CMP_OPS.get(type(op_node))
            if op is None:
                raise ValueError("不允许的比较运算符")
            right = _eval_node(comp)
            if not op(left, right):
                return False
            left = right
        return True
    if isinstance(node, (ast.List, ast.Tuple)):
        if len(node.elts) > MAX_SEQ_LEN:
            raise ValueError("列表过长，已拒绝计算")
        return [_eval_node(e) for e in node.elts]
    if isinstance(node, ast.Name):
        for table in (_SAFE_BUILTINS, _MATH_FUNCS, _MATH_CONSTS, _RANDOM_FUNCS):
            if node.id in table:
                return table[node.id]
        raise ValueError(f"未知名称: {node.id}")
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name):
            if node.value.id == "math":
                if node.attr in _MATH_FUNCS:
                    return _MATH_FUNCS[node.attr]
                if node.attr in _MATH_CONSTS:
                    return _MATH_CONSTS[node.attr]
            if node.value.id == "random" and node.attr in _RANDOM_FUNCS:
                return _RANDOM_FUNCS[node.attr]
        raise ValueError("不允许的属性访问")
    if isinstance(node, ast.Call):
        fn = _eval_node(node.func)
        if not callable(fn):
            raise ValueError("目标不可调用")
        args = [_eval_node(a) for a in node.args]
        kwargs = {kw.arg: _eval_node(kw.value) for kw in node.keywords if kw.arg}
        return fn(*args, **kwargs)
    raise ValueError(f"不允许的语法: {type(node).__name__}")


def safe_eval(expression: str):
    """AST 白名单求值。合法时返回数值，违规或超限时抛 ValueError。"""
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("表达式为空")
    if len(expression) > MAX_EXPR_LEN:
        raise ValueError("表达式过长")
    tree = ast.parse(expression, mode="eval")
    result = _eval_node(tree)
    if isinstance(result, int) and result.bit_length() > MAX_INT_BITS:
        raise ValueError("结果过大，已拒绝计算")
    if isinstance(result, (list, tuple)) and len(result) > MAX_SEQ_LEN:
        raise ValueError("结果过大，已拒绝计算")
    if isinstance(result, complex):
        raise ValueError("不支持复数结果")
    if isinstance(result, float) and not math.isfinite(result):
        return str(result)  # inf / nan 转字符串，避免 JSON 兼容问题
    return result


# ---------- MCP 工具定义 ----------
from fastmcp import FastMCP

mcp = FastMCP("Calculator")


@mcp.tool()
def calculator(python_expression: str) -> dict:
    """For mathematical calculation, always use this tool to calculate the result of an expression.
    Supported: + - * / // % ** with parentheses; math functions like math.sqrt(), math.sin(),
    math.log(), math.comb(), math.factorial() and constants math.pi, math.e; random functions
    like random.randint(1, 6). No 'import' is needed. 用于数学计算：四则运算、乘方、开方、
    三角函数、阶乘、组合数、随机数等。"""
    try:
        result = safe_eval(python_expression)
        payload = {"success": True, "result": result}
        if len(json.dumps(payload, ensure_ascii=False)) > MAX_PAYLOAD_BYTES:
            raise ValueError("结果太长，无法返回，请换一种更简单的计算")
        logger.info("Calculating formula: %s, result: %s", python_expression, result)
        return payload
    except Exception as exc:  # noqa: BLE001 — 任何异常都转成给模型的友好错误
        logger.warning("Rejected/failed expression: %r (%s: %s)", python_expression, type(exc).__name__, exc)
        return {"success": False, "error": f"{type(exc).__name__}: {exc}"}


if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)

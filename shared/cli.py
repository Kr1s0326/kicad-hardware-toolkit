#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cli.py - 命令行入口的统一错误处理。

问题
----
补这个之前，所有入口的错误都是**裸 traceback**：

    传入不存在的封装文件   -> FileNotFoundError + 8 行堆栈
    spec.json 语法错       -> json.decoder.JSONDecodeError + 堆栈
    spec.json 缺字段       -> KeyError: 'pins' + 堆栈

对一个要给同事和 AI agent 用的工具，`KeyError: 'pins'` 远不如
`spec.json 缺少必填字段 "pins"` 有用 —— agent 看到堆栈也只能瞎猜。

用法
----
    from cli import guard

    @guard
    def main():
        ...

    if __name__ == "__main__":
        sys.exit(main())

`--traceback` 或环境变量 `TOOLKIT_TRACEBACK=1` 可以拿回完整堆栈（调试用）。
"""

import json
import os
import sys

try:
    sys.stdout.reconfigure(errors="replace")
    sys.stderr.reconfigure(errors="replace")
except Exception:                                       # noqa: BLE001
    pass

EXIT_USAGE = 2

# `--traceback` 是给 guard 用的，不能让它漏到 argparse 里 ——
# 否则 `unrecognized arguments: --traceback`。在这里（import 时）就摘掉。
_TRACEBACK = False
if "--traceback" in sys.argv:
    _TRACEBACK = True
    sys.argv = [a for a in sys.argv if a != "--traceback"]


def _wanted():
    return (_TRACEBACK or "--traceback" in sys.argv
            or os.environ.get("TOOLKIT_TRACEBACK") not in (None, "", "0"))


def _hint(exc):
    """把异常翻译成一句能照着做的事"""
    if isinstance(exc, FileNotFoundError):
        p = getattr(exc, "filename", None) or (exc.args[0] if exc.args else "?")
        return ("找不到文件：\n  %s\n"
                "检查路径拼写；如果是相对路径，注意它相对的是**当前工作目录**。" % p)
    if isinstance(exc, IsADirectoryError):
        return "这是一个目录，不是文件：\n  %s" % exc.args[0]
    if isinstance(exc, PermissionError):
        return "没有权限读写：\n  %s" % exc.args[0]
    if isinstance(exc, json.JSONDecodeError):
        return ("JSON 语法错：第 %d 行第 %d 列 —— %s\n"
                "（常见原因：多写了逗号、用了单引号、注释没删干净 —— JSON 不支持注释）"
                % (exc.lineno, exc.colno, exc.msg))
    if isinstance(exc, KeyError):
        k = exc.args[0] if exc.args else "?"
        return ("缺少必填字段：%r\n"
                "对照 assets/ 里的模板补上；字段含义见 SKILL.md 或 references/。" % k)
    if isinstance(exc, (TypeError, ValueError)):
        return "数据格式不对：%s\n检查 spec / 参数里的类型（数字写成了字符串？）" % exc
    if isinstance(exc, IndexError):
        return "数据比预期的短：%s" % exc
    return None


def guard(fn):
    """给 main() 加上统一错误处理；返回的就是一个普通可调用对象"""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except SystemExit:
            raise                       # 自己抛的（含 argparse）不要拦
        except KeyboardInterrupt:
            print("\n已中断。", file=sys.stderr)
            return 130
        except Exception as e:                          # noqa: BLE001
            if _wanted():
                raise
            print("\n%s: %s" % (type(e).__name__, e), file=sys.stderr)
            h = _hint(e)
            if h:
                print("\n" + h, file=sys.stderr)
            else:
                print("\n（加 --traceback 看完整堆栈）", file=sys.stderr)
            return EXIT_USAGE
    wrapper.__name__ = getattr(fn, "__name__", "main")
    wrapper.__doc__ = fn.__doc__
    return wrapper


def need_file(path, what):
    """早失败：文件不存在时给一句人话，而不是等到深处才 FileNotFoundError"""
    if path and not os.path.exists(path):
        raise SystemExit("找不到%s：\n  %s" % (what, os.path.abspath(path)))
    return path

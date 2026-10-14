# -*- coding: utf-8 -*-
"""测试包：逻辑层无窗口单测。运行：python -m tests.run_tests

本文件**兼作 stdout 编码兜底**（2026-10-14 加，起因是一次被埋掉的真失败）
------------------------------------------------------------------------
本仓的测试断言消息里含 GBK 编不出的数学符号（``⊆`` ``−`` ``⇒`` ``∇`` 等），
而 Windows 控制台 / CI 的 Python 默认 io 编码是 **GBK**。于是：

* ``print('  ok: … ⊆ …')`` 抛 ``UnicodeEncodeError`` ⇒ 该模块**判红**，
  而红的原因是"控制台编不出这个字符"，不是"被测逻辑不对"；
* 更坏的是它**能掩盖真失败**：``scripts/run_all_tests.py`` 的汇总同样崩在
  ``print`` 上，进程带栈退出、一条结论都不打印 —— 当时 ``tests.test_compat``
  正因仓根残留目录而 4/6 失败，全程无人看见（详见 ``.gitignore`` 里那段注释）。

为什么放在这里：所有模块都以 ``python -m tests.<module>`` 启动，
Python 必定先 import 本包 ⇒ 这一处覆盖全部模块，且**逐模块直跑也生效**
（``python -m tests.test_brickward`` 不再因为一个 ``⊆`` 而红）。

``errors='replace'`` 是有意的：宁可个别字符显示成 ``?``，也不让整段测试结论消失。
判定只看退出码与模块专属 PASSED 标记，与显示编码无关。
"""
from __future__ import annotations

import sys


def _force_utf8_stdio() -> None:
    """把本进程 stdout/stderr 钉成 UTF-8 + errors='replace'（不支持则静默跳过）。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, OSError):
            # 已被替换成非 TextIOWrapper（如某些测试夹具）——不阻断 import
            pass


_force_utf8_stdio()

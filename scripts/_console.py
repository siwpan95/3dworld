#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""入口脚本的 **stdio 编码兜底**（一处定义，全部入口复用）。

为什么需要它（2026-10-14 实踩，代价是一次被埋掉的真失败）
--------------------------------------------------------
本仓的断言 / 提示文案里含 GBK 编不出的符号（``⚠`` ``✔`` ``✘`` ``⊆`` ``−`` ``⇒`` ``∇`` …），
而 Windows 控制台与 CI 的 Python 默认 io 编码是 **GBK**。于是 ``print`` 抛
``UnicodeEncodeError``：

* 单看一个脚本，它表现为"脚本崩了"；
* **真正致命的是聚合入口**：``run_all_tests.py`` 打印子进程输出时崩，进程带栈退出、
  一条模块汇总都不打印 —— 把「有模块跑挂」表达成「runner 崩了」，
  当时 ``tests.test_compat`` 正因仓根残留目录而 4/6 失败，**全程无人看见**。

教训：**入口脚本的失败模式必须与被测对象的失败可区分**，否则它会长期掩盖真失败。
所以这里把它变成一处定义：任何脚本只要 ``ensure_utf8_stdio()`` 一下，就不会再因为
"控制台编不出某个字符"而丢掉结论。

用法
----
在脚本里（**放在所有 print 之前**）::

    import importlib.util as _ilu          # noqa: E402
    _spec = _ilu.spec_from_file_location(
        '_console', os.path.join(os.path.dirname(os.path.abspath(__file__)), '_console.py'))
    _console = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_console)
    _console.ensure_utf8_stdio()

之所以用按文件路径加载而不是 ``from _console import …``：本仓的入口脚本都被
**按路径**调用（``python scripts/verify.py``，此时 ``sys.path[0]`` 是 ``scripts/``，
但在 ``python -m`` / CI / 打包场景下不一定），按路径加载在四种调用形态下都成立。

``errors='replace'`` 是有意的
-----------------------------
宁可个别字符显示成 ``?``，也不让整段结论消失。**判定只看退出码与显式标记**，
与显示编码无关。测试侧另有一份等价兜底在 ``tests/__init__.py``（覆盖所有
``python -m tests.<module>`` 的模块，包括逐模块直跑）。
"""
from __future__ import annotations

import sys


def ensure_utf8_stdio() -> None:
    """把本进程 stdout/stderr 钉成 UTF-8 + ``errors='replace'``（不支持则静默跳过）。

    幂等：重复调用无副作用。对已被替换成非 ``TextIOWrapper`` 的流（某些测试夹具）
    静默跳过 —— 兜底本身不该成为新的失败点。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding='utf-8', errors='replace')
        except (ValueError, OSError):
            pass

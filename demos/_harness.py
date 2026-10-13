# -*- coding: utf-8 -*-
"""**兼容再导出层**（P2-1）—— 真身已搬到 `world3d/platform/app.py`。

`demos/*.py` 很早以前都写 ``from _harness import DemoApp, ...``（裸模块名，靠
``demos/`` 在 sys.path 上解析）。P2-1 把这份"公共骨架"正名到平台层
（`world3d/platform/app.py`，它与任何具体 demo 无关），本文件只做转发。

保留它的理由：① 旧导入路径留一版，避免外部脚本/书签式用法立刻断；
② `scripts/verify_demos.py` 的 `SKIP = {'_harness.py'}` 仍按此名跳过 —— 它不是 demo。

新代码请直接::

    from world3d.platform.app import DemoApp, run
"""
from world3d.platform.app import *          # noqa: F401,F403
from world3d.platform.app import __all__    # noqa: F401  公共面以真身声明为准

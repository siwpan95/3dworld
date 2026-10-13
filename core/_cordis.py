# -*- coding: utf-8 -*-
"""共同核实现的 cordis 引导：定位 vendored cordis 并 re-export ``Service`` 基类。

``core/`` 下的共同核实现（render / economy / entity / rules / terrain / ...）都继承
cordis 的 ``Service`` 基类；而 ``core.contracts`` 刻意**不依赖** cordis（契约只 import
typing）。本模块把「定位 cordis」这一件事收在一处，避免每个 ``*_core.py`` 重复。

定位顺序：① 已安装 / 已被 ``.pth`` 暴露的 ``cordis``；② 环境变量 ``CORDIS_SRC``；
③ 仓库内自带的 ``vendor/cordis/src``（**clean clone 走这一条，无需手工接线**）。

定位与报错统一收口在 ``_cordis_locate`` —— 详见该模块 docstring（此处不再重复兜底，
以免「cordis 已找到但自身缺依赖」被误报成「未找到 cordis 包」）。
"""
from __future__ import annotations

import os
import sys

# 3dworld 仓库根（core/ 的父目录）——保证 ``import core.xxx`` 可用
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Context, Service  # noqa: E402,F401

__all__ = ['Context', 'Service']

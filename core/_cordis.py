# -*- coding: utf-8 -*-
"""共同核实现的 cordis 引导：定位 vendored cordis 并 re-export ``Service`` 基类。

``core/`` 下的共同核实现（render / economy / entity / rules / terrain / ...）都继承
cordis 的 ``Service`` 基类；而 ``core.contracts`` 刻意**不依赖** cordis（契约只 import
typing）。本模块把「定位 cordis」这一件事收在一处，避免每个 ``*_core.py`` 重复。

定位顺序：① 已安装 / 已被 ``.pth`` 暴露的 ``cordis``；② 环境变量 ``CORDIS_SRC``
指向的 vendor 源码目录（本项目为 ``vendor/cordis/src``）。
"""
from __future__ import annotations

import os
import sys

# 3dworld 仓库根（core/ 的父目录）——保证 ``import core.xxx`` 可用
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:
    import cordis  # noqa: F401
except ImportError:  # pragma: no cover - 仅在未暴露 cordis 时触发
    _src = os.environ.get('CORDIS_SRC')
    if _src and os.path.isdir(_src):
        sys.path.insert(0, _src)
    try:
        import cordis  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            '[cordis] 未找到 cordis 包。安装方式见 experiments/cordis/README.md'
            '（或设置 CORDIS_SRC 指向 vendor/cordis/src）') from exc

from cordis import Context, Service  # noqa: E402,F401

__all__ = ['Context', 'Service']

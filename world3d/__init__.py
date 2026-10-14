# -*- coding: utf-8 -*-
"""3dworld 引擎的**库入口**（导入包名 ``world3d``，发行名 ``3dworld``）。

## 为什么有两个名字

Python 的**导入包名**不能以数字开头 —— ``import 3dworld`` 直接是语法错，
``'3dworld'.isidentifier()`` 为 ``False``；而 **PEP 508 发行名**允许数字开头
（``3dworld`` 合法）。所以本项目采用：**发行名 `3dworld` / 导入名 `world3d`**。

## P1 阶段本包是"薄门面"

库化第一步（P1）只做三件事：让仓库**可安装**、给使用者一个**稳定的导入入口**、
把版本与依赖收口到 ``pyproject.toml``。因此：

* 本包把引擎公共面从 `engine` **转发**出来（公共面由 `world3d.engine.__all__` 收口，
  这里不重复维护第二份清单）；
* `engine` / `core` / `host` / `cli` / `games` / `logic` / `presentation` 仍是
  各自独立的顶层包 —— **既有导入路径零破坏**；
* P2「分层归位」才会把它们真正收进 `world3d.*` 命名空间，并在那时为旧路径留
  一版兼容再导出层。见 `docs/5_引擎化改造规划.md` §6。

用法::

    import world3d
    print(world3d.__version__)
    s = world3d.Sampler([0.0, 1.0], [0.0, 2.0])      # 引擎公共面
"""
from __future__ import annotations

# 引擎公共面（mathkit / timeline / frustum / greedy_mesh / fsm / orchestration …）
from world3d.engine import *                      # noqa: F401,F403
from world3d.engine import __all__ as _engine_all

try:                                      # 已安装（wheel/editable）：以包元数据为准
    from importlib.metadata import version as _pkg_version
    __version__ = _pkg_version('3dworld')
except Exception:                         # 源码直跑（未安装）：退回 engine 的版本
    from world3d.engine import __version__        # noqa: F401

__all__ = ['__version__', *_engine_all]

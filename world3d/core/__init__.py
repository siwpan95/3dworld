# -*- coding: utf-8 -*-
"""核心层：窗口/渲染/输入/资源 + **共同核服务**（P0-1）。

两部分：

* 旧核心层（``renderer`` / ``geometry`` / ``nordic_scene`` / ``kkrieger_scene`` …）：
  允许 ``import pyglet.gl``。
* **共同核服务**（``*_core.py``）：实现 ``world3d.core.contracts`` 的 12 个服务契约，
  跨内核复用（P0-1 自各游戏内核上提 + 补齐）。

⚠️ 本 ``__init__`` **不主动 import 任何子模块** —— 这样 ``from world3d.core.contracts import X``
在本包不依赖 cordis / pyglet 时依然可 import。共同核服务用 PEP 562 懒导出：
``from world3d.core import RenderService`` 或直接 ``from world3d.core.render_core import RenderService``。
"""

_SERVICES = {
    'RenderService': 'world3d.core.render_core',
    'EconomyService': 'world3d.core.economy_core',
    'EntityService': 'world3d.core.entity_core',
    'RulesService': 'world3d.core.rules_core',
    'TerrainService': 'world3d.core.terrain_core',
    'PathingService': 'world3d.core.pathing_core',
    'ParticlesService': 'world3d.core.particles_core',
    'SdfVoxelService': 'world3d.core.sdf_core',
    'AudioService': 'world3d.core.audio_core',
    'TimelineService': 'world3d.core.timeline_core',
}

__all__ = sorted(_SERVICES)


def __getattr__(name):
    """PEP 562 懒导出：按需 import 对应共同核模块（首次访问才触发 cordis）。"""
    mod = _SERVICES.get(name)
    if mod is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    import importlib
    return getattr(importlib.import_module(mod), name)

# -*- coding: utf-8 -*-
"""服务注册表：**服务短名 → 实现类**（P0-4）。

## 为什么需要它

P0-4 之前，配置树（`configs/*.yml`）里的每条 entry 都指向一个
`experiments/cordis/services/<名>.py` —— 那些文件**全是 5 行样板**，内容只有一句
`from <真实模块> import <类>` 加 `default = <类>`，共 23 个。样板本身不含逻辑，
却要"新增一个核 → 新建一个文件 → 改 yml"，且真实实现在哪个模块得逐个打开看。

现在收口成**这一张表**：yml 里直接写服务短名（`- name: render`），由
`host.tree.load_tree` 在挂载前解析成实现类。好处：

- **单一事实来源**：引擎提供哪些服务、各自实现在哪，一眼看完（本文件）；
- **安全边界更紧**：短名必须命中本表才能挂载（封闭白名单），比"任何
  `experiments.cordis.*` 模块都可 import"更严；
- 新增服务只改这张表，不再加样板文件。

## 为什么不是"把 `default` 挂到真实模块上"

`games/fps/kernel.py` 同时提供 `FpsKernel` 与 `MazeService`，
`games/puzzle/kernel.py` 同时提供 `PuzzleKernel` 与 `GridService` ——
一个模块一个 `default` 装不下两个服务，因此改为**为每个短名生成一个只含
`default` 的宿主模块**（`_3dwsrv.<短名>`），见 `bind()`。
"""
from __future__ import annotations

import importlib
import sys
import types

__all__ = ['SERVICES', 'SYNTH_PREFIX', 'bind', 'is_service', 'service_names']

#: 合成宿主模块的顶层包名（挂进 `sys.modules`，只含 `default`）
SYNTH_PREFIX = '_3dwsrv'

#: 短名 → '真实模块路径:实现类名'
SERVICES = {
    # ---- 13 个共同核（实现住 core/，无 GL 依赖或自带 GL 回滚）----
    'render': 'core.render_core:RenderService',
    'economy': 'core.economy_core:EconomyService',
    'entity': 'core.entity_core:EntityService',
    'rules': 'core.rules_core:RulesService',
    'terrain': 'core.terrain_core:TerrainService',
    'pathing': 'core.pathing_core:PathingService',
    'particles': 'core.particles_core:ParticlesService',
    'sdf': 'core.sdf_core:SdfVoxelService',
    'audio': 'core.audio_core:AudioService',
    'timeline': 'core.timeline_core:TimelineService',
    'inventory': 'core.inventory_core:InventoryService',
    'skills': 'core.skills_core:SkillsService',
    'loot': 'core.loot_core:LootService',
    # ---- 11 个玩法内核服务（实现住 games/）----
    'storm': 'games.storm.kernel:StormKernel',
    'fps': 'games.fps.kernel:FpsKernel',
    'maze': 'games.fps.kernel:MazeService',
    'puzzle': 'games.puzzle.kernel:PuzzleKernel',
    'grid': 'games.puzzle.kernel:GridService',
    'tower_defense': 'games.tower_defense.kernel:TowerDefenseKernel',
    'hall': 'games.hall.kernel:HallKernel',
    'void': 'games.void.kernel:VoidKernel',
    'pulse': 'games.pulse.kernel:PulseKernel',
    'godhand': 'games.godhand.kernel:GodHandKernel',
    'brickward': 'games.brickward.kernel:BrickwardKernel',   # BQM 机制致敬 · B1
}


def is_service(name) -> bool:
    """``name`` 是否是注册表里的服务短名。"""
    return isinstance(name, str) and name in SERVICES


def service_names():
    """全部服务短名（含内核），供体检 / 文档生成用。"""
    return tuple(sorted(SERVICES))


def bind(name: str) -> str:
    """把服务短名绑定成 cordis 能加载的**模块路径**，返回该路径。

    cordis 的 Loader 只认模块路径、取模块的 `default` 作为 Service 类。这里为每个
    短名创建一个只含 `default` 的宿主模块（`_3dwsrv.<短名>`）并登记进 `sys.modules`，
    因此**同一个实现模块可以提供多个服务**（`fps` / `maze` 同在 `games/fps/kernel.py`）。
    幂等：重复调用返回同一路径。
    """
    if name not in SERVICES:
        raise KeyError(f'未知服务短名：{name!r}（见 host/registry.py 的 SERVICES）')
    full = f'{SYNTH_PREFIX}.{name}'
    if full in sys.modules:
        return full
    module_path, attr = SERVICES[name].split(':', 1)
    impl = getattr(importlib.import_module(module_path), attr)
    mod = types.ModuleType(full)
    mod.default = impl                      # cordis 只取这一个属性
    mod.__doc__ = f'服务 {name!r} 的宿主模块（P0-4 生成；实现：{module_path}:{attr}）'
    sys.modules[full] = mod
    return full

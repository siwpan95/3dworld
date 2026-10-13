# -*- coding: utf-8 -*-
"""配置树引导 —— `configs/*.yml` → 「共同核 + 内核」服务图的共享入口。

（P0-2 试点转正：原 `experiments/cordis/tree_boot.py` → `host/tree.py`；
9 棵配置树 → 顶层 `configs/`。见 `docs/5_引擎化改造规划.md`。）

在 Cordis v4.0.0 原生 ``Loader`` / ``EntryTree`` 之上封装三件小事，让
``config_boot.py``（headless 验证）与 ``demos/*.py``（真实应用）走**同一条**挂载路径——
「本次挂哪些插件、各带什么配置」只由 yaml 声明一次（单一事实来源）::

    - name: experiments.cordis.services.economy
      id: economy
      config: { gold: 300 }

每个 entry 的 ``name`` 经 ``importlib`` 解析为模块，取其 ``default``（Service 类），
``config`` 透传给构造；Cordis 按 ``inject`` 依赖图自动排序挂载，无需手工编排。

- ``load_tree(ctx, path)``    挂载配置树（内含 Loader 子插件就绪所需的 drain 循环）。
- ``await_ready(ctx, ...)``   等待「服务可达 + 内核 GL 句柄已取得」。
- ``mount_tree(path, want)``  一行式：新建 Context → 挂树 → 等就绪 → 返回 ctx。

demos/tower_defense.py / demos/godhand.py 的 ``_mount_kernel`` 即调用 ``mount_tree``；
config_boot.py 的 headless 验证复用同一函数与同一 ``WANT_*`` 列表——
「demo 怎么挂的」= 「验证怎么挂的」，不存在两套接线。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    # 供配置树条目的 name（experiments.cordis.services.*）经 importlib 解析
    sys.path.insert(0, ROOT)

# ---- cordis 定位（vendored 源码经 .pth 暴露；换环境时可设 CORDIS_SRC） ----
import _cordis_locate  # noqa: E402

_cordis_locate.ensure()

import yaml  # noqa: E402
from cordis import Context, Loader  # noqa: E402

# 配置树住顶层 configs/（P0-2 起；此前散在 experiments/cordis/）
CONFIG_DIR = os.path.join(ROOT, 'configs')

# 各配置树就绪所需的最低服务集（demo 与 headless 验证共用，避免两处漂移）
WANT_TD = ('tower_defense', 'render', 'rules', 'economy', 'entity')
WANT_GODHAND = ('godhand', 'render', 'terrain', 'rules', 'economy', 'entity')
WANT_FPS = ('fps', 'render', 'rules', 'economy', 'entity', 'maze')
WANT_PUZZLE = ('puzzle', 'render', 'rules', 'entity', 'grid')
WANT_STORM = ('storm', 'render', 'rules', 'economy', 'entity', 'particles', 'pathing')
WANT_HALL = ('hall', 'render', 'rules')
WANT_VOID = ('void', 'render', 'rules', 'economy', 'entity')
WANT_PULSE = ('pulse', 'render', 'rules', 'economy', 'audio')
# 全共同核（P0-4 配置树统一）：cordis.cores.yml 声明挂载的全部核
WANT_CORES = ('render', 'economy', 'entity', 'rules', 'terrain',
              'pathing', 'particles', 'sdf', 'audio', 'timeline',
              'inventory', 'skills', 'loot')      # P1-⑤ 玩法通用模块 +3


def config_path(name: str) -> str:
    """配置树文件路径（name 如 ``'cordis.td.yml'``）。"""
    return os.path.join(CONFIG_DIR, name)


# ---- 配置树 entry 白名单（安全审计 F-002：配置即代码）----------------------
# cordis 的 Loader 会把每条 entry 的 ``name`` 直接交给 ``importlib.import_module``
# （见 vendor/cordis/src/cordis/loader.py）。也就是说：**改一个 .yml 就等于改一段
# 可执行代码**——而 .yml 在评审者眼里通常被当作"数据"，因此一行 diff 即可获得任意
# 代码执行，属典型的"评审绕过"（在 cordis.puzzle.yml 里加一行 ``- name: os`` 即可）。
# 信任边界虽已收敛在"仓库内容"（用了 yaml.safe_load，且 CLI 的 kind 走白名单词典
# 查表、外部无法指定 YAML 路径），仍在此处强制白名单，把这条原语钉死。
ALLOWED_NAME_PREFIXES = ('experiments.cordis.',)
ALLOWED_NAME_EXACT = ('@cordisjs/plugin-group',)


def _validate_entry_names(data, path: str) -> None:
    """拒绝白名单之外的 entry ``name``（它们会被 cordis 当作模块名导入）。"""
    if not isinstance(data, list):
        return
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        if not isinstance(name, str):
            continue
        if name in ALLOWED_NAME_EXACT or name.startswith(ALLOWED_NAME_PREFIXES):
            continue
        raise ValueError(
            f'配置树 {path} 第 {i} 条 entry 的 name={name!r} 不在白名单内 —— '
            f'entry name 会被 cordis 直接 import，等同于执行代码。'
            f'允许前缀：{ALLOWED_NAME_PREFIXES}；允许精确值：{ALLOWED_NAME_EXACT}。'
        )


async def load_tree(ctx, path: str):
    """用 Cordis 原生 Loader 把 cordis.yml 配置树挂载为服务图。

    ``ctx.plugin(Loader)`` 是异步的；Loader 内部会自动注册 ``isolate`` 等子插件，
    并安装 ``loader/entry-init`` 处理器——必须先把事件循环 drain 几轮，等这些
    处理器就绪再 ``root.update(...)``，否则首条 entry 建好时会因 ``_isolate``
    尚未提供而抛 ``KeyError``（本坑已由试点三首次 headless 运行实证）。
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f'配置树不存在: {path}')
    await ctx.plugin(Loader)                       # 提供 "loader" 服务（Entry 机制依赖）
    loader = ctx.loader                            # Loader 实例（暴露为 ctx.loader）
    for _ in range(16):
        await asyncio.sleep(0)                     # drain：等 isolate 等子插件初始化完毕
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    _validate_entry_names(data, path)              # 白名单：name 会被当模块名导入
    await loader.root.update(data)                 # 配置驱动：声明即挂载
    return loader


async def await_ready(ctx, loader, want=(), timeout: float = 8.0,
                      require_gl: bool = True) -> bool:
    """等待配置树加载完成：``want`` 中服务全部可达。

    ``require_gl=True``（默认）额外要求「内核已取得 GL 句柄」（``live_count() > 0``）
    —— 即「挂完且内核初始化完」，适用于**含游戏内核**的配置树（td/godhand/fps）。
    **纯共同核**配置树（cores）没有内核去取 GL 句柄，应传 ``require_gl=False``。
    """
    want = tuple(want)
    dt = 1.0 / 60.0
    elapsed = 0.0
    check_gl = require_gl and ('render' in want)
    while elapsed < timeout:
        await loader.await_()
        await asyncio.sleep(0)
        if all(getattr(ctx, n, None) is not None for n in want):
            if not check_gl:
                return True
            render = getattr(ctx, 'render', None)
            if render is None or render.live_count() > 0:
                return True
        elapsed += dt
    missing = [n for n in want if getattr(ctx, n, None) is None]
    extra = ''
    render = getattr(ctx, 'render', None)
    if check_gl and render is not None and render.live_count() == 0:
        extra = '（服务已就绪，但内核尚未取得 GL 句柄）'
    raise RuntimeError(f"配置树未在 {timeout}s 内就绪；缺失服务: {missing}{extra}")


async def mount_tree(path: str, want=(), timeout: float = 8.0,
                     require_gl: bool = True):
    """一行式引导：新建 Context、按 cordis.yml 挂载、等到就绪，返回 ctx。

    demos 的 ``_mount_kernel`` 与 config_boot 的 headless 验证都调用本函数。
    ``require_gl=False`` 用于**纯共同核**配置树（无内核取 GL 句柄）。
    """
    ctx = Context()
    loader = await load_tree(ctx, path)
    if want:
        await await_ready(ctx, loader, want, timeout, require_gl)
    return ctx


__all__ = [
    'CONFIG_DIR', 'WANT_TD', 'WANT_GODHAND', 'WANT_FPS', 'WANT_PUZZLE',
    'WANT_STORM', 'WANT_HALL', 'WANT_VOID', 'WANT_PULSE', 'WANT_CORES',
    'config_path', 'load_tree', 'await_ready', 'mount_tree',
]

# -*- coding: utf-8 -*-
"""配置树引导 —— `configs/*.yml` → 「共同核 + 内核」服务图的共享入口。

（P0-2 试点转正：原 `experiments/cordis/tree_boot.py` → `host/tree.py`；
9 棵配置树 → 顶层 `configs/`。见 `docs/5_引擎化改造规划.md`。）

在 Cordis v4.0.0 原生 ``Loader`` / ``EntryTree`` 之上封装三件小事，让
``cli/config_boot.py``（headless 验证）与 ``examples/*.py``（真实应用）走**同一条**挂载路径——
「本次挂哪些插件、各带什么配置」只由 yaml 声明一次（单一事实来源）::

    - name: economy          # <- 服务短名，表在 host/registry.py
      id: economy
      config: { gold: 300 }

每个 entry 的 ``name``：**命中 `world3d.host.registry.SERVICES` 的短名**会被解析成实现类
（P0-4 起；此前是 23 个 5 行样板壳），也仍接受**模块路径**（取其 ``default``）以兼容
手写配置树。``config`` 透传给构造；Cordis 按 ``inject`` 依赖图自动排序挂载，无需手工编排。

- ``load_tree(ctx, path)``    挂载配置树（内含 Loader 子插件就绪所需的 drain 循环）。
- ``await_ready(ctx, ...)``   等待「服务可达 + 内核 GL 句柄已取得」。
- ``mount_tree(path, want)``  一行式：新建 Context → 挂树 → 等就绪 → 返回 ctx。

examples/tower_defense.py / examples/godhand.py 的 ``_mount_kernel`` 即调用 ``mount_tree``；
`cli/config_boot.py` 的 headless 验证复用同一函数与同一 ``WANT_*`` 列表——
「demo 怎么挂的」= 「验证怎么挂的」，不存在两套接线。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    # 供配置树条目的 name（服务短名的实现模块 / 手写模块路径）经 importlib 解析
    sys.path.insert(0, ROOT)

# ---- cordis 定位（vendored 源码经 .pth 暴露；换环境时可设 CORDIS_SRC） ----
import world3d._cordis_locate as _cordis_locate  # noqa: E402

_cordis_locate.ensure()

import yaml  # noqa: E402
from cordis import Context, Loader  # noqa: E402

from world3d.host import registry                        # noqa: E402  P0-4：服务短名 → 实现类
from world3d.host.registry import SYNTH_PREFIX           # noqa: E402

# 配置树住顶层 configs/（P0-2 起；此前散在 experiments/cordis/）。
#
# ⚠️ 为什么要有环境变量逃生口：`configs/` 是**仓库资产**，不随 wheel 分发
# （见 pyproject.toml 的 packages 说明）—— 所以非 editable 安装下 `ROOT` 落在
# site-packages，那里没有 configs/。此时用 `WORLD3D_CONFIGS` 指向别处的配置树，
# 或直接从仓库运行（仓库/editable 下 ROOT 就是仓库根，天然可用）。
CONFIG_DIR = os.environ.get('WORLD3D_CONFIGS') or os.path.join(ROOT, 'configs')

# 各配置树就绪所需的最低服务集（demo 与 headless 验证共用，避免两处漂移）
WANT_TD = ('tower_defense', 'render', 'rules', 'economy', 'entity')
WANT_GODHAND = ('godhand', 'render', 'terrain', 'rules', 'economy', 'entity')
WANT_FPS = ('fps', 'render', 'rules', 'economy', 'entity', 'maze')
WANT_PUZZLE = ('puzzle', 'render', 'rules', 'entity', 'grid')
WANT_STORM = ('storm', 'render', 'rules', 'economy', 'entity', 'particles', 'pathing')
WANT_HALL = ('hall', 'render', 'rules')
WANT_VOID = ('void', 'render', 'rules', 'economy', 'entity')
WANT_PULSE = ('pulse', 'render', 'rules', 'economy', 'audio')
# BQM 机制致敬 · B1/B2/B3：内核 `inject = []`（纯逻辑、零 GL），但 B3 的 demo 要用
# `ctx.render` 记账 GL 资源 ⇒ 配置树把 render 一起挂上（「demo 怎么挂的 = 验证怎么挂的」）。
# ⚠ 内核自己**不取** GL 句柄，所以 `cli/config_boot` 对 brickward 要传 `require_gl=False`：
# 否则 `await_ready` 的"等 live_count() > 0"永远等不到东西（见 config_boot.run_one）。
WANT_BRICKWARD = ('brickward', 'render')
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
#
# P0-4：白名单**只在校验"解析之后"的 name**（见 `_resolve_service_names`）——
# 服务短名先被 `host.registry.bind()` 换成 `_3dwsrv.<短名>` 这类**合成宿主模块**，
# 而短名必须命中 `registry.SERVICES`（封闭表）才可能走到这里；手写模块路径则仍受
# 下面的前缀白名单约束。两条路都不可任意 import。
ALLOWED_NAME_PREFIXES = (
    f'{SYNTH_PREFIX}.',      # 服务短名（host/registry.py 的封闭表，P0-4 起为主）
    'core.',                 # 共同核实现模块
    'games.',                # 玩法内核实现模块
)
ALLOWED_NAME_EXACT = ('@cordisjs/plugin-group',)


def _resolve_service_names(data, path: str) -> int:
    """把 entry 里的**服务短名**解析成 cordis 能加载的模块路径，返回替换条数。

    ``- name: economy`` → ``- name: _3dwsrv.economy``（合成宿主模块，只含 ``default``）。
    非短名（含 ``.`` 的模块路径）原样保留，交给后面的白名单校验。
    """
    if not isinstance(data, list):
        return 0
    n = 0
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = entry.get('name')
        if registry.is_service(name):
            entry['name'] = registry.bind(name)
            n += 1
    return n


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
            f'允许前缀：{ALLOWED_NAME_PREFIXES}；允许精确值：{ALLOWED_NAME_EXACT}；'
            f'或使用 host/registry.py 里的服务短名（{len(registry.SERVICES)} 个）。'
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
    _resolve_service_names(data, path)             # P0-4：短名（host/registry.py）→ 模块路径
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
    raise RuntimeError(
        f"配置树未在 {timeout}s 内就绪；缺失服务: {missing}{extra}\n"
        '  提示：cordis 的 Loader 会**吞掉插件构造期异常** —— 某个 entry 的模块 import 成功、'
        '但 Service.__init__ / setup 抛错时，这里只能看到"服务不就绪"。\n'
        '  定位办法：手工 `import <该 entry 的 name>` 一次，看真实异常。'
        '（P0-3 实踩：kernel 里少了一行 import，NameError 被吞成"服务不就绪"，查了半天。）')


async def mount_tree(path: str, want=(), timeout: float = 8.0,
                     require_gl: bool = True):
    """一行式引导：新建 Context、按 cordis.yml 挂载、等到就绪，返回 ctx。

    examples 的 ``_mount_kernel`` 与 config_boot 的 headless 验证都调用本函数。
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
    'WANT_BRICKWARD',
    'config_path', 'load_tree', 'await_ready', 'mount_tree',
]

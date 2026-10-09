# -*- coding: utf-8 -*-
"""试点三引导器：从 cordis.yml 配置树加载「共同核 + 内核」并 headless 验证。

cordis v4.0.0 自带 EntryTree / EntryGroup / Loader 配置树机制：把 cordis.yml
解析出的「插件声明列表」喂给 ``loader.root.update(...)``，Cordis 即按依赖图自动
排序并挂载每个 Service；内核只声明 ``inject`` 取所需核心，零编排。

这证明「本次挂哪些插件」可以完全由一份 yaml 声明，而无需在 Python 里逐个
``ctx.plugin(...)``——试点三的收口点。

运行（无需显示器 / 不依赖 GL 窗口）::

    python experiments/cordis/config_boot.py td
    python experiments/cordis/config_boot.py godhand
    python experiments/cordis/config_boot.py all
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # experiments/cordis

try:
    import cordis  # noqa: F401
except ImportError:
    _src = os.environ.get('CORDIS_SRC')
    if _src and os.path.isdir(_src):
        sys.path.insert(0, _src)
    try:
        import cordis  # noqa: F401
    except ImportError:
        sys.exit('[cordis] 未找到 cordis 包。设置 CORDIS_SRC 或安装。')

import yaml  # noqa: E402
from cordis import Context, Loader  # noqa: E402


CONFIG_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIGS = {
    'td': 'cordis.td.yml',
    'godhand': 'cordis.godhand.yml',
}


async def load_tree(ctx, path):
    """用 Cordis 原生 Loader 把 cordis.yml 配置树挂载为服务图。"""
    await ctx.plugin(Loader)                       # 提供 "loader" 服务（Entry 机制依赖）
    loader = ctx.loader                            # Loader 实例（暴露为 ctx.loader）
    # 让 Loader 的子插件（isolate 等）完成初始化并注册 loader/entry-init 处理器，
    # 否则首条 entry 建好时 _isolate 尚未就绪 -> KeyError。
    for _ in range(16):
        await asyncio.sleep(0)
    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)
    await loader.root.update(data)                 # 配置驱动：声明即挂载
    return loader


async def await_ready(ctx, loader, want, timeout=8.0):
    """等待配置树加载完成（服务可达 + GL 资源已就绪）。"""
    dt = 1.0 / 60.0
    elapsed = 0.0
    while elapsed < timeout:
        await loader.await_()
        await asyncio.sleep(0)
        if all(getattr(ctx, name, None) is not None for name in want):
            if getattr(ctx, 'render', None) is not None and ctx.render.live_count() > 0:
                return True
        elapsed += dt
    missing = [n for n in want if getattr(ctx, n, None) is None]
    raise RuntimeError(f"配置树未在 {timeout}s 内就绪；缺失服务: {missing}")


async def _rollback(ctx, kernel, render, label):
    """dispose 内核 fiber -> 所有 GL 资源随内核一起回滚。"""
    gl_before = render.live_count()
    await kernel.ctx.fiber.dispose()
    gl_after = render.live_count()
    print(f"[rollback] GL live: {gl_before} -> {gl_after}  (stats={render.stats()})")
    assert gl_after == 0, f"GL 资源未全部释放！仍存活 {gl_after}"
    assert render.stats()['released'] == render.stats()['acquired'], "释放计数 != 取得计数"
    print(f"== {label}_OK ==")


async def run_td(ctx, loader):
    await await_ready(ctx, loader,
                      ['tower_defense', 'render', 'rules', 'economy', 'entity'])
    kernel = ctx.tower_defense
    render = ctx.render
    assert render.live_count() > 0, "GL 资源未取得"

    dt = 1.0 / 60.0
    t = 0.0
    while ctx.rules.state() not in ('won', 'lost') and t < 400.0:
        kernel.update(dt)
        t += dt
    r = ctx.rules
    print(f"RESULT state={r.state()} t={t:.1f} "
          f"gold={ctx.economy.balance('gold'):.0f} base_hp={r.base_hp} "
          f"enemies_left={len(kernel.enemies)} towers={len(kernel.towers)}")
    assert r.state() == 'won', f"预期通关，实际 {r.state()}"
    await _rollback(ctx, kernel, render, 'TDKERNEL')


async def run_godhand(ctx, loader):
    await await_ready(ctx, loader,
                      ['godhand', 'render', 'terrain', 'rules', 'economy', 'entity'])
    kernel = ctx.godhand
    render = ctx.render
    terrain = ctx.terrain
    assert render.live_count() > 0, "GL 资源未取得"

    dt = 1.0 / 60.0
    t = 0.0
    while ctx.rules.state() not in ('won', 'lost') and t < 120.0:
        for eid in list(kernel.settlements):
            e = ctx.entity.get(eid)
            if e is None:
                continue
            if terrain.height_at(e['x'], e['z']) >= terrain.sea:
                kernel.lower_at(e['x'], e['z'], 1.5)
        kernel.update(dt)
        t += dt
    r = ctx.rules
    print(f"RESULT state={r.state()} t={t:.1f} "
          f"remaining_settlements={len(kernel.settlements)} "
          f"mana_left={ctx.economy.balance('mana'):.0f}")
    assert r.state() == 'won', f"预期通关（淹没全部聚落），实际 {r.state()}"
    await _rollback(ctx, kernel, render, 'GODHAND')


async def run_one(kind):
    ctx = Context()
    path = os.path.join(CONFIG_DIR, CONFIGS[kind])
    loader = await load_tree(ctx, path)
    if kind == 'td':
        await run_td(ctx, loader)
    else:
        await run_godhand(ctx, loader)


def main(argv=None):
    ap = argparse.ArgumentParser(description="试点三 cordis.yml 配置树引导")
    ap.add_argument('kind', nargs='?', default='all',
                    choices=['td', 'godhand', 'all'],
                    help='运行哪个配置树（默认 all）')
    args = ap.parse_args(argv)
    kinds = ['td', 'godhand'] if args.kind == 'all' else [args.kind]
    for k in kinds:
        print(f"\n########## 配置树：{k} ##########")
        asyncio.run(run_one(k))
    return 0


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""试点三引导器：从 cordis.yml 配置树加载「共同核 + 内核」并 headless 验证。

本脚本与 demos/*.py 共用 ``tree_boot.mount_tree`` 的同一条挂载路径——
「demo 怎么挂的 = 验证怎么挂的」，不存在两套接线。在此之上，本脚本额外做三件事：

1. 全速跑完一局（TD：撑过 5 波 / 神之手：淹没全部聚落），验证内核仍可胜；
2. 断言 yaml 的 ``config`` 真正到达服务（TD：gold=300 / total_waves=5；神之手：mana 基数 0）；
3. ``dispose`` 内核 fiber，断言 GL 资源随内核可逆回滚（live → 0）。

运行（无需显示器 / 不依赖 GL 窗口）::

    python experiments/cordis/config_boot.py td
    python experiments/cordis/config_boot.py godhand
    python experiments/cordis/config_boot.py all
    python experiments/cordis/config_boot.py fps --seeds 20261010,11,22   # 多种子回归
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from experiments.cordis.tree_boot import (  # noqa: E402
    WANT_FPS, WANT_GODHAND, WANT_TD, config_path, mount_tree,
)
from experiments.cordis.godhand_kernel import START_MANA  # noqa: E402
from experiments.cordis.fps_kernel import START_AMMO  # noqa: E402
from experiments.cordis.tower_defense_kernel import (  # noqa: E402
    START_GOLD, WAVES as TD_WAVES,
)

CONFIGS = {
    'td': ('cordis.td.yml', WANT_TD),
    'godhand': ('cordis.godhand.yml', WANT_GODHAND),
    'fps': ('cordis.fps.yml', WANT_FPS),
}


async def _rollback(ctx, kernel, render, label):
    """dispose 内核 fiber -> 所有 GL 资源随内核一起回滚。"""
    gl_before = render.live_count()
    await kernel.ctx.fiber.dispose()
    gl_after = render.live_count()
    print(f"[rollback] GL live: {gl_before} -> {gl_after}  (stats={render.stats()})")
    assert gl_after == 0, f"GL 资源未全部释放！仍存活 {gl_after}"
    assert render.stats()['released'] == render.stats()['acquired'], "释放计数 != 取得计数"
    print(f"== {label}_OK ==")


async def run_td(ctx):
    kernel = ctx.tower_defense
    render = ctx.render
    # config 传递核验：yaml 声明值须到达服务，且与内核默认一致（防配置树与玩法漂移）
    gold0 = ctx.economy.balance('gold')
    waves = ctx.rules.total_waves
    print(f"[config] gold={gold0:.0f} total_waves={waves}")
    assert gold0 == START_GOLD, f"yaml gold 与内核默认不一致: {gold0} != {START_GOLD}"
    assert waves == len(TD_WAVES), f"yaml total_waves 与内核默认不一致: {waves} != {len(TD_WAVES)}"
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


async def run_godhand(ctx):
    kernel = ctx.godhand
    render = ctx.render
    terrain = ctx.terrain
    # config 传递核验：yaml mana=0 + 内核开局授予 START_MANA 即为当前存量
    mana0 = ctx.economy.balance('mana')
    print(f"[config] mana={mana0:.0f} (= yaml 0 + 内核开局授予 {START_MANA})")
    assert mana0 == START_MANA, f"yaml mana 基数未生效（期望 {START_MANA}，实际 {mana0}）"
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


def _fps_game(ctx, kernel, max_t=180.0):
    """用 auto-pilot 跑一局，返回 (state, t, kills, hp, ammo)。"""
    dt = 1.0 / 60.0
    t = 0.0
    while ctx.rules.state() not in ('won', 'lost') and t < max_t:
        kernel.auto_step(dt)
        kernel.update(dt)
        ctx.entity.update(dt)
        t += dt
    r = ctx.rules
    return (r.state(), t, kernel.kills, r.base_hp, kernel.ammo())


async def run_fps(ctx, seeds=None):
    """射线 FPS：auto-pilot 走迷宫 + 遇敌射击 → 抵达出口为胜（与 demo --flag auto 同逻辑）。

    ``seeds`` 给定时逐种子 ``restart`` 各跑一局（``--seeds`` 多种子回归——
    平衡复调后用同一组种子前后对比，防止"调好一局调坏十局"）。
    """
    kernel = ctx.fps
    render = ctx.render
    maze = ctx.maze
    # config 传递核验 + 迷宫自身的可玩性核验（出口必须可达，否则 auto-pilot 永远到不了）
    hp0 = ctx.rules.base_hp
    ammo0 = kernel.ammo()
    print(f"[config] seed={maze.seed} cells={maze.n}x{maze.n} hp={hp0} "
          f"ammo={ammo0:.0f} enemies={len(kernel.enemies)}")
    assert hp0 == 100, f"yaml base_hp 未到达 rules: {hp0}"
    assert ammo0 == START_AMMO, f"开局弹药应为 {START_AMMO}，实际 {ammo0}"
    assert len(kernel.enemies) > 0, "敌人未生成"
    assert render.live_count() > 0, "GL 资源未取得"
    assert maze.find_path(maze.start, maze.exit), "迷宫起点到出口不连通（生成有 bug）"
    assert maze.is_wall_cell(*maze.exit) is False, "出口落在墙里"

    results = []
    for k, sd in enumerate(seeds if seeds else [maze.seed]):
        if k > 0:
            kernel.restart(int(sd))
        state, t, kills, hp, ammo = _fps_game(ctx, kernel)
        print(f"RESULT seed={sd} state={state} t={t:.1f} kills={kills} "
              f"hp={hp:.0f} ammo={ammo:.0f} shots={kernel.shots} "
              f"hits={kernel.hits} enemies_left={len(kernel.enemies)}")
        assert state == 'won', f"种子 {sd}：预期 auto-pilot 抵达出口，实际 {state}"
        results.append((int(sd), t, kills, hp))
    if len(results) > 1:
        ts = [t for _, t, *_ in results]
        print(f"[seeds] {len(results)}/{len(results)} 通关  "
              f"用时 {min(ts):.1f}~{max(ts):.1f}s  "
              f"最差 HP={min(h for *_, h in results):.0f}")
    await _rollback(ctx, kernel, render, 'FPSKERNEL')


async def run_one(kind, seeds=None):
    name, want = CONFIGS[kind]
    ctx = await mount_tree(config_path(name), want=want)
    if kind == 'td':
        await run_td(ctx)
    elif kind == 'godhand':
        await run_godhand(ctx)
    else:
        await run_fps(ctx, seeds)


def main(argv=None):
    ap = argparse.ArgumentParser(description="试点三 cordis.yml 配置树引导")
    ap.add_argument('kind', nargs='?', default='all',
                    choices=['td', 'godhand', 'fps', 'all'],
                    help='运行哪个配置树（默认 all）')
    ap.add_argument('--seeds', default=None,
                    help='fps 专属：逗号分隔多种子回归（如 --seeds 20261010,11,22）'
                         '——每种子 restart 各跑一局，全部须通关')
    args = ap.parse_args(argv)
    if args.seeds and args.kind != 'fps':
        ap.error('--seeds 仅支持 fps')
    seeds = ([int(s) for s in args.seeds.split(',') if s.strip()]
             if args.seeds else None)
    kinds = (['td', 'godhand', 'fps'] if args.kind == 'all' else [args.kind])
    for k in kinds:
        print(f"\n########## 配置树：{k} ##########")
        asyncio.run(run_one(k, seeds))
    return 0


if __name__ == '__main__':
    sys.exit(main())

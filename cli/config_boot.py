# -*- coding: utf-8 -*-
"""配置树引导器（CLI）：从 `configs/*.yml` 加载「共同核 + 内核」并 headless 验证。

（P0-2 试点转正：原 `experiments/cordis/config_boot.py` → `cli/config_boot.py`。）

本脚本与 demos/*.py 共用 ``host.tree.mount_tree`` 的同一条挂载路径——
「demo 怎么挂的 = 验证怎么挂的」，不存在两套接线。在此之上，本脚本额外做三件事：

1. 全速跑完一局（TD：撑过 5 波 / 神之手：淹没全部聚落），验证内核仍可胜；
2. 断言 yaml 的 ``config`` 真正到达服务（TD：gold=300 / total_waves=5；神之手：mana 基数 0）；
3. ``dispose`` 内核 fiber，断言 GL 资源随内核可逆回滚（live → 0）。

运行（无需显示器 / 不依赖 GL 窗口）::

    python -m cli.config_boot td
    python -m cli.config_boot all
    python -m cli.config_boot fps --seeds 20261010,11,22   # 多种子回归
    python -m cli.config_boot puzzle --seeds 20261010,11   # 解谜多种子
    python -m cli.config_boot storm --seeds 20261011,7     # 弹幕多种子
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from host.tree import (  # noqa: E402
    WANT_CORES, WANT_FPS, WANT_GODHAND, WANT_HALL, WANT_PULSE, WANT_PUZZLE,
    WANT_STORM, WANT_TD, WANT_VOID,
    config_path, mount_tree,
)
from experiments.cordis.godhand_kernel import START_MANA  # noqa: E402
from experiments.cordis.fps_kernel import START_AMMO  # noqa: E402
from experiments.cordis.puzzle_kernel import (  # noqa: E402
    DEMO_SEEDS, HANDMADE_LEVELS,
)
from experiments.cordis.tower_defense_kernel import (  # noqa: E402
    START_GOLD, WAVES as TD_WAVES,
)

CONFIGS = {
    'td': ('cordis.td.yml', WANT_TD),
    'godhand': ('cordis.godhand.yml', WANT_GODHAND),
    'fps': ('cordis.fps.yml', WANT_FPS),
    'cores': ('cordis.cores.yml', WANT_CORES),
    'puzzle': ('cordis.puzzle.yml', WANT_PUZZLE),
    'storm': ('cordis.storm.yml', WANT_STORM),
    'hall': ('cordis.hall.yml', WANT_HALL),
    'void': ('cordis.void.yml', WANT_VOID),
    'pulse': ('cordis.pulse.yml', WANT_PULSE),
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


def _replay_to_win(ctx, kernel, max_t=120.0):
    """用 auto-pilot（求解器回放）把当前关玩到胜，返回用时。"""
    dt = 1.0 / 60.0
    t = 0.0
    rules = ctx.rules
    while rules.state() != 'won' and t < max_t:
        kernel.auto_step(dt)
        ctx.entity.update(dt)
        t += dt
    return t


async def run_puzzle(ctx, seeds=None):
    """方块解谜：手工关 + 种子生成关全部「求解器回放到胜」（§11.6 验收 1/2）。"""
    kernel = ctx.puzzle
    grid = ctx.grid
    render = ctx.render
    rules = ctx.rules
    seed_list = list(seeds) if seeds else list(DEMO_SEEDS)
    print(f"[config] handmade={len(HANDMADE_LEVELS)} gen_seeds={len(seed_list)} "
          f"cells={grid.n} boxes={grid.box_total}")
    assert render.live_count() > 0, "GL 资源未取得"

    # ---- 手工教学关：必须可解且回放到胜 ----
    for k, lv in enumerate(HANDMADE_LEVELS):
        kernel.load_level(lv)
        sol = grid.solve()
        assert sol is not None, f"手工关 {k}（{lv['meta']['name']}）不可解"
        t = _replay_to_win(ctx, kernel)
        assert rules.state() == 'won', f"手工关 {k} 回放未胜"
        print(f"RESULT handmade[{k}] name={lv['meta']['name']} moves={len(sol.moves)} "
              f"pushes={sol.pushes} t={t:.2f}")

    # ---- 生成关：多种子（生成器已验证可解；这里独立复验 + 回放）----
    results = []
    for sd in seed_list:
        kernel.load_generated(int(sd))
        sol = grid.solve()
        assert sol is not None, f"种子 {sd} 生成关不可解（生成器/求解器不一致！）"
        assert sol.pushes >= kernel.ctx.grid.box_total, f"种子 {sd} 推数过少（平凡关）"
        t = _replay_to_win(ctx, kernel)
        assert rules.state() == 'won', f"种子 {sd} 回放未胜"
        assert kernel.steps == len(sol.moves), \
            f"种子 {sd} 步数 {kernel.steps} != 解长 {len(sol.moves)}（回放漂移）"
        print(f"RESULT seed={sd} name={kernel.level_name} moves={len(sol.moves)} "
              f"pushes={sol.pushes} t={t:.2f}")
        results.append((int(sd), len(sol.moves), sol.pushes))
    if len(results) > 1:
        mv = [m for _, m, _ in results]
        print(f"[seeds] {len(results)}/{len(results)} 通关  "
              f"解长 {min(mv)}~{max(mv)} 步")
    await _rollback(ctx, kernel, render, 'BOXWARD')


async def run_cores(ctx):
    """全共同核自检（P0-4）：断言 10 个共同核均经配置树挂载且**可用**。

    不只是"属性存在"——逐个调用关键方法验证**行为**（非仅 isinstance 检查）。
    """
    from core.contracts import CORES

    missing = [n for n in WANT_CORES if getattr(ctx, n, None) is None]
    assert not missing, f"共同核未挂载: {missing}"

    ctx.economy.add('gold', 10.0)
    assert ctx.economy.balance('gold') >= 10.0
    assert ctx.economy.spend('gold', 5.0) is True, 'economy.spend 异常'

    eid = ctx.entity.spawn('probe', (0.0, 0.0, 0.0))
    assert ctx.entity.get(eid) is not None and ctx.entity.count('probe') == 1

    assert ctx.rules.state() in ('prep', 'playing'), 'rules 初始状态异常'

    assert ctx.terrain.height_at(0.0, 0.0) is not None

    assert ctx.pathing.find_path((0, 0), (2, 2)) == [(0, 0), (2, 2)], 'pathing 直线寻路异常'
    assert len(ctx.pathing.formation((0.0, 0.0, 0.0), 3, 1.0)) == 3, 'pathing 编队异常'

    assert ctx.particles.emit('spark', 8, origin=(0.0, 0.0, 0.0)) == 8, 'particles.emit 异常'

    assert ctx.sdf.voxel_get(0, 0, 0) == 0
    ctx.sdf.voxel_set(0, 0, 0, 3)
    assert ctx.sdf.block_at(0, 0, 0) == 3, 'sdf 体素写读不一致'

    assert ctx.timeline.make_sampler(
        [0.0, 1.0], [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]) is not None, 'timeline.make_sampler 异常'

    assert isinstance(ctx.audio.available(), bool), 'audio.available 应返回 bool'

    print(f"[cores] 全部 {len(WANT_CORES)} 核经配置树可达：{', '.join(WANT_CORES)}")
    print(f"[cores] contracts 注册表 {len(CORES)} 契约；本配置树挂载 {len(WANT_CORES)} 个共同核")
    print("== CORES_OK ==")


async def run_storm(ctx, seeds=None):
    """弹幕固定屏：auto-pilot（闪避 AI）跑满 10 波 → won（§12.6 验收 2）。"""
    kernel = ctx.storm
    rules = ctx.rules
    render = ctx.render
    particles = ctx.particles
    seed_list = list(seeds) if seeds else [kernel.seed]
    print(f"[config] waves={rules.total_waves} lives={rules.base_hp} "
          f"seeds={len(seed_list)} particles_ready={particles is not None} "
          f"pathing_ready={ctx.pathing is not None}")
    assert rules.base_hp == 3, f"yml base_hp（命数）未到达 rules: {rules.base_hp}"
    assert rules.total_waves == 10, f"yml total_waves 未到达 rules: {rules.total_waves}"
    assert render.live_count() > 0, "GL 资源未取得"
    # 两个"首个内核消费者"的显式核验（§12.4）
    assert particles is not None and hasattr(particles, 'snapshot'), \
        "particles 核未挂载或缺 snapshot()（首个内核消费者核验失败）"
    assert ctx.pathing is not None, "pathing 核未挂载（编队进场核验失败）"

    results = []
    for k, sd in enumerate(seed_list):
        if k > 0:
            kernel.restart(int(sd))
        dt = 1.0 / 60.0
        t = 0.0
        while rules.state() not in ('won', 'lost') and t < 240.0:
            kernel.auto_step(dt)
            kernel.update(dt)
            ctx.entity.update(dt)
            t += dt
        st = rules.state()
        assert st == 'won', (f"种子 {sd}：预期 auto-pilot 打满 {rules.total_waves} 波，"
                             f"实际 {st}（wave={kernel.wave + 1} lives={rules.base_hp}）")
        s = kernel.stats
        peak_parts = particles.count()
        print(f"RESULT seed={sd} state={st} wave={kernel.wave + 1} t={t:.1f} "
              f"kills={s['kills']} shots={s['shots']} hits={s['hits']} "
              f"acc={100.0 * s['hits'] / max(1, s['shots']):.0f}% "
              f"max_combo={s['max_combo']} score={ctx.economy.balance('score'):.0f} "
              f"live_particles={peak_parts}")
        results.append((int(sd), t, s['kills']))
    if len(results) > 1:
        print(f"[seeds] {len(results)}/{len(results)} 通关  "
              f"用时 {min(t for _, t, _ in results):.1f}~{max(t for _, t, _ in results):.1f}s")
    await _rollback(ctx, kernel, render, 'STORMKERNEL')


async def run_hall(ctx, seeds=None):
    """静馆：加载场景 JSON -> 点击序列 -> 3 谜全解 -> puzzle_solved -> won（§14.6 验收）。"""
    import json as _json
    import tempfile

    from experiments.cordis.hall_kernel import (DEFAULT_SCENE, graph_reachable,
                                                validate_scene)
    kernel = ctx.hall
    render = ctx.render
    rules = ctx.rules
    scene = kernel.scene
    print(f"[config] scene={kernel.loaded_from} nodes={len(scene['nodes'])} "
          f"steps={len(scene['puzzle']['steps'])} hotspots="
          f"{sum(len(n.get('hotspots', [])) for n in scene['nodes'])}")
    assert render.live_count() > 0, "GL 资源未取得"

    # ---- 节点图连通：3 景必须全部可达 ----
    reach = graph_reachable(scene['edges'], scene['start'])
    ids = {n['id'] for n in scene['nodes']}
    assert reach == ids, f"节点图不连通: 可达 {reach} != 全部 {ids}"
    print(f"RESULT graph connected: {sorted(reach)}")

    # ---- 正向：点击序列（headless 与 demo 同一 click() 入口）----
    seq = ['h_take_key', 'h_to_hall', 'h_lamp', 'h_to_vault', 'h_final']
    for hsid in seq:
        r = kernel.click(hsid)
        print(f"  click {hsid:<12} ok={r['ok']} {r['detail']}")
        assert r['ok'], f"点击 {hsid} 失败: {r['detail']}"
    assert 'final_open' in kernel.flags, "终局门未开启"
    assert rules.state() == 'won', f"puzzle_solved 未让 rules 进 won: {rules.state()}"
    print(f"RESULT solve ok: items={kernel.inventory()} flags={sorted(kernel.flags)}")

    # ---- 反向：谜题的"锁"真的生效（缺前置直接点终局/上锁通道 -> 拒绝）----
    kernel.node_id = scene['start']
    kernel.flags, kernel.items = set(), []
    r_bad = kernel.click('h_final')
    assert not r_bad['ok'], "缺齿轮时终局门竟被打开（锁失效）"
    kernel.flags, kernel.items = set(), []
    kernel.node_id = 'hall'
    r_locked = kernel.click('h_to_vault')
    assert not r_locked['ok'], "未点灯时穹室通道竟可通行（锁失效）"
    print(f"RESULT locks ok: {r_bad['detail']} / {r_locked['detail']}")

    # ---- 热重载：改文件生效；坏 JSON 拒绝并保留旧场景 ----
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, 'scene.json')
        with open(p, 'w', encoding='utf-8') as f:
            _json.dump(DEFAULT_SCENE, f, ensure_ascii=False)
        assert kernel.load_scene(p), "合法 JSON 加载失败"
        assert kernel.loaded_from == p
        before = kernel.scene['nodes'][0]['name']
        with open(p, 'w', encoding='utf-8') as f:
            _json.dump({'nodes': 'not-a-list'}, f)          # 坏结构
        assert not kernel.load_scene(p), "坏 JSON 竟被接受"
        assert kernel.reload_errors == 1, kernel.reload_errors
        assert kernel.scene['nodes'][0]['name'] == before, "坏 JSON 污染了旧场景"
        print(f"RESULT reload ok: 坏 JSON 被拒（旧场景保留 name={before}）")

    ok_bad, reason = validate_scene({'start': 'x', 'nodes': []})
    assert not ok_bad, f"空场景竟通过校验: {reason}"
    # OK 标记由 _rollback 统一打印（避免重复）
    await _rollback(ctx, kernel, render, 'HALLKERNEL')


async def run_void(ctx, seeds=None):
    """星阑：市场确定性/事件冲击/均值回归 + 贸易脚本达标 won（含海盗战胜利）（§15.6）。"""
    from experiments.cordis.void_kernel import (GOOD_BASE, GOODS, mean_reversion_gap,
                                                shock_at, shock_table, station_price)
    kernel = ctx.void
    render = ctx.render
    rules = ctx.rules
    print(f"[config] seed={kernel.seed} stations={len(kernel.stations)} goods={len(GOODS)} "
          f"target={kernel.target:.0f} pirates={len(kernel.pirates)}")
    assert render.live_count() > 0, "GL 资源未取得"

    # ---- 市场：确定性（同 t 同站同货 -> 同价）----
    st = kernel.stations[1]
    a = station_price('chips', st, 123.4, kernel.seed, {})
    b = station_price('chips', st, 123.4, kernel.seed, {})
    assert abs(a - b) < 1e-12, '价格不确定'
    c = station_price('chips', st, 123.4, kernel.seed + 1, {})
    assert abs(a - c) > 1e-9, '换种子价格应变化（相位不同）'
    print(f"RESULT price deterministic: {a:.2f}（同参一致；换种子 {c:.2f}）")

    # ---- 事件冲击：方向正确（冲击 > 0 时价格抬升）----
    p0 = station_price('ore', kernel.stations[0], 50.0, kernel.seed, {})
    p1 = station_price('ore', kernel.stations[0], 50.0, kernel.seed,
                       {('hub', 'ore'): 0.22})
    assert p1 > p0, f'正向冲击未抬价: {p0:.2f} -> {p1:.2f}'
    # 冲击项加在 base 上（shock × base），所以增量是 base*0.22 而非 p0*0.22
    assert abs(p1 - (p0 + GOOD_BASE['ore'] * 0.22)) < 1e-9, '冲击幅度不对'
    evs = shock_table(kernel.seed)
    assert len(evs) >= 3 and shock_at(evs, evs[0][0] + 1.0), '事件表/查询不一致'
    print(f"RESULT shock ok: {p0:.2f} -> {p1:.2f}（+22%）；事件 {len(evs)} 条")

    # ---- 均值回归：价格围绕基准波动，长期不跑偏 ----
    series = [station_price('grain', kernel.stations[2], t, kernel.seed, {})
              for t in range(0, 400)]
    gap = mean_reversion_gap(series)
    assert gap < GOOD_BASE['grain'] * 0.30, f'波动过大（未围绕基准）: gap={gap:.2f}'
    print(f"RESULT mean-reversion ok: 均价 {sum(series) / len(series):.2f} gap {gap:.2f}")

    # ---- 海盗战斗：单测（开火 -> 命中 -> 击退 + 赏金）----
    k0 = kernel.pirates_beaten
    if kernel.pirates:
        p = next((q for q in kernel.pirates if q['alive']), None)
        if p is not None:
            kernel.ship['x'], kernel.ship['z'] = p['x'] - 3.0, p['z']
            kernel.ship['heading'] = 0.0
            for _ in range(24):
                kernel.ship['cd'] = 0.0
                kernel.fire()
                kernel._bullets_step(1.0 / 60.0)
            assert kernel.pirates_beaten > k0, '对近距海盗开火竟未击退'
            print(f"RESULT combat ok: 击退 {kernel.pirates_beaten - k0} 艘（赏金入账）")

    # ---- 贸易脚本：auto_step 跑到资产达标 ----
    dt = 1.0 / 60.0
    t = 0.0
    while rules.state() not in ('won', 'lost') and t < 900.0:
        kernel.auto_step(dt)
        t += dt
    st_ = rules.state()
    assert st_ == 'won', (f"贸易脚本未达标：state={st_} t={t:.1f} "
                          f"资产={kernel.total_asset():.0f}/{kernel.target:.0f} "
                          f"credits={kernel.ctx.economy.balance('credits'):.0f}")
    assert kernel.pirates_beaten >= 1, '至少要有一次海盗战胜利'
    assert kernel.trades >= 4, f'交易次数过少（{kernel.trades}）'
    print(f"RESULT trade ok: state=won t={t:.1f}s 资产={kernel.total_asset():.0f} "
          f"credits={kernel.ctx.economy.balance('credits'):.0f} 交易={kernel.trades} "
          f"海盗={kernel.pirates_beaten}")
    # OK 标记由 _rollback 统一打印（避免重复）
    await _rollback(ctx, kernel, render, 'VOIDKERNEL')


async def run_pulse(ctx, seeds=None):
    """落音：3 谱面自动演奏全连 won + 可打性/时长/判定窗/音频合成（§16.6）。"""
    import os as _os

    from experiments.cordis.pulse_kernel import (BARS, BPM_LIST, GOOD_MS, N_TRACKS,
                                                 PERFECT_MS, accuracy, chart, judge)
    kernel = ctx.pulse
    render = ctx.render
    rules = ctx.rules
    print(f"[config] seed={kernel.seed} tracks={N_TRACKS} bpm_list={BPM_LIST} "
          f"bars={BARS} judge=±{PERFECT_MS:.0f}/{GOOD_MS:.0f}ms audio={ctx.audio is not None}")
    assert render.live_count() > 0, "GL 资源未取得"
    assert ctx.audio is not None, "audio 核未挂载（§16 的首个内核消费者）"

    # ---- 判定窗（纯函数）----
    assert judge(0.0) == 'PERFECT' and judge(-39.9) == 'PERFECT'
    assert judge(40.1) == 'GOOD' and judge(-90.0) == 'GOOD'
    assert judge(90.1) == 'MISS' and judge(-500.0) == 'MISS'
    assert abs(accuracy(10, 0, 0) - 100.0) < 1e-9
    assert abs(accuracy(0, 10, 0) - 40.0) < 1e-9
    assert abs(accuracy(0, 0, 10)) < 1e-9
    print(f"RESULT judge ok: ±{PERFECT_MS:.0f}ms PERFECT / ±{GOOD_MS:.0f}ms GOOD；准确率口径正确")

    # ---- 谱面：可打性约束 + 时长准确（种子复现）----
    for bpm in BPM_LIST:
        notes, synth, meta = chart(kernel.seed, bpm, BARS)
        want = BARS * 4 * 60.0 / bpm
        assert abs(meta['duration'] - want) < 1e-6, f'{bpm} 时长不准'
        # 同轨最小间隔 >= 1/8 拍
        last = {}
        for t, tr in notes:
            if tr in last:
                assert t - last[tr] >= 60.0 / bpm * 0.5 - 1e-6, f'{bpm} 同轨间隔过近'
            last[tr] = t
        assert len(synth) >= len(notes), '合成事件应覆盖所有 note'
        n2, _, _ = chart(kernel.seed, bpm, BARS)
        assert n2 == notes, f'{bpm} 谱面不可复现'
    print(f"RESULT chart ok: 3 谱面时长准确 + 同轨间隔合规 + 种子复现")

    # ---- 3 谱面自动演奏（全连 won）----
    for bpm in BPM_LIST:
        kernel.load_chart(bpm)
        dur = kernel.meta['duration']
        dt = 1.0 / 60.0
        t = 0.0
        while rules.state() not in ('won', 'lost') and t < dur + 30.0:
            kernel.auto_step(dt)
            t += dt
        r = kernel.result()
        assert rules.state() == 'won', (f'BPM {bpm} 自动演奏未通关：{rules.state()} '
                                        f"M{r['MISS']} 血量={rules.base_hp}")
        assert r['full_combo'], f"BPM {bpm} 未全连：{r}"
        print(f"RESULT autoplay bpm={bpm}: P{r['PERFECT']} G{r['GOOD']} M{r['MISS']} "
              f"combo={r['combo_max']} score={r['score']:.0f} acc={r['acc']:.1f}% t={t:.1f}s")

    # ---- 音频合成 smoke（wav 落盘；audio 核首个消费者）----
    path = kernel.render_music()
    assert path and _os.path.isfile(path), f'音频渲染失败: {path}'
    size = _os.path.getsize(path)
    assert size > 1000, f'wav 过小: {size}'
    print(f"RESULT audio smoke ok: {_os.path.basename(path)} {size} bytes")
    # OK 标记由 _rollback 统一打印（避免重复）
    await _rollback(ctx, kernel, render, 'PULSEKERNEL')


async def run_one(kind, seeds=None):
    name, want = CONFIGS[kind]
    # 纯共同核配置树（cores）无内核取 GL 句柄 -> 不要求 GL 就绪
    ctx = await mount_tree(config_path(name), want=want,
                           require_gl=(kind != 'cores'))
    if kind == 'td':
        await run_td(ctx)
    elif kind == 'godhand':
        await run_godhand(ctx)
    elif kind == 'cores':
        await run_cores(ctx)
    elif kind == 'storm':
        await run_storm(ctx, seeds)
    elif kind == 'hall':
        await run_hall(ctx, seeds)
    elif kind == 'void':
        await run_void(ctx, seeds)
    elif kind == 'pulse':
        await run_pulse(ctx, seeds)
    elif kind == 'fps':
        await run_fps(ctx, seeds)
    else:
        await run_puzzle(ctx, seeds)


def main(argv=None):
    ap = argparse.ArgumentParser(description="试点三 cordis.yml 配置树引导")
    ap.add_argument('kind', nargs='?', default='all',
                    choices=['td', 'godhand', 'fps', 'puzzle', 'storm', 'hall', 'void', 'pulse',
                             'cores', 'all'],
                    help='运行哪个配置树（默认 all；cores = 全共同核自检）')
    ap.add_argument('--seeds', default=None,
                    help='fps/puzzle/storm 专属：逗号分隔多种子回归（如 --seeds 20261010,11,22）'
                         '——每种子各跑一局，全部须通关')
    args = ap.parse_args(argv)
    if args.seeds and args.kind not in ('fps', 'puzzle', 'storm'):
        ap.error('--seeds 仅支持 fps / puzzle / storm')
    seeds = ([int(s) for s in args.seeds.split(',') if s.strip()]
             if args.seeds else None)
    kinds = (['td', 'godhand', 'fps', 'puzzle', 'storm', 'hall', 'void', 'pulse',
              'cores']
             if args.kind == 'all' else [args.kind])
    for k in kinds:
        print(f"\n########## 配置树：{k} ##########")
        asyncio.run(run_one(k, seeds))
    return 0


if __name__ == '__main__':
    sys.exit(main())

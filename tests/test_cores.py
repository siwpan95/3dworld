# -*- coding: utf-8 -*-
"""core 共同核测试套件（P0-1）。

覆盖 12 个服务契约的**结构**（isinstance Protocol）与**关键行为**。

运行::

    python -m tests.test_cores

原则与 run_tests / test_engine 一致：断言数值、打印实测数据，不用"看起来对"。
需要 cordis（vendored）——未暴露时设 ``CORDIS_SRC`` 指向 ``vendor/cordis/src``。
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core._cordis import Context                       # noqa: E402  （含 cordis 定位）
from core.contracts import (                           # noqa: E402
    CORES, FROM_SCRATCH_CORES, AudioCore, EconomyCore, EntityCore, HudCore,
    InputCore, ParticlesCore, PathingCore, RenderCore, RulesCore, SdfVoxelCore,
    TerrainCore, TimelineCore,
)
from core.audio_core import AudioService               # noqa: E402
from core.economy_core import EconomyService           # noqa: E402
from core.entity_core import EntityService             # noqa: E402
from core.pathing_core import PathingService           # noqa: E402
from core.particles_core import ParticlesService       # noqa: E402
from core.render_core import RenderService             # noqa: E402
from core.rules_core import RulesService               # noqa: E402
from core.sdf_core import SdfVoxelService              # noqa: E402
from core.terrain_core import TerrainService           # noqa: E402
from core.timeline_core import TimelineService         # noqa: E402


def _ctx():
    """每个用例一个独立 Context（避免服务名重复注册）。"""
    return Context()


# ---------------------------------------------------------------- 注册表 ----
def test_cores_registry_complete():
    """12 个契约全部注册，且无「待从零建」遗留。"""
    assert len(CORES) == 12, f'期望 12 核，实际 {len(CORES)}'
    assert FROM_SCRATCH_CORES == [], f'仍有待从零建的核心: {FROM_SCRATCH_CORES}'
    for name, meta in CORES.items():
        assert meta.get('module'), f'{name} 缺 module'
        assert meta.get('status'), f'{name} 缺 status'
    print(f'  ok: 12 核注册表完整，待从零建 = {FROM_SCRATCH_CORES}')


# ------------------------------------------------------------- 结构检查 ----
def test_protocol_structures():
    """每个服务实例都满足其契约 Protocol（runtime_checkable）。"""
    ctx = _ctx()
    pairs = [
        ('render', RenderService(ctx, None), RenderCore),
        ('economy', EconomyService(ctx, None), EconomyCore),
        ('entity', EntityService(ctx, None), EntityCore),
        ('rules', RulesService(ctx, None), RulesCore),
        ('terrain', TerrainService(ctx, None), TerrainCore),
        ('pathing', PathingService(ctx, None), PathingCore),
        ('particles', ParticlesService(ctx, None), ParticlesCore),
        ('sdf', SdfVoxelService(ctx, None), SdfVoxelCore),
        ('audio', AudioService(ctx, None), AudioCore),
        ('timeline', TimelineService(ctx, None), TimelineCore),
    ]
    for name, obj, proto in pairs:
        assert isinstance(obj, proto), f'{name} 未满足 {proto.__name__} 契约'
    print(f'  ok: {len(pairs)} 个服务均满足契约 Protocol（isinstance 通过）')


# --------------------------------------------------------------- render ----
def test_render_accounting_rollback():
    """GL 资源原语：记账 + 回滚语义。"""
    r = RenderService(_ctx(), None)
    assert r.stats()['live'] == 0
    pid = r.compile_program('main')
    mid = r.upload_mesh('ground', [], [])
    assert r.live_count() == 2, r.stats()
    r.release(pid)
    r.release(mid)
    st = r.stats()
    assert st['live'] == 0 and st['acquired'] == 2 and st['released'] == 2, st
    assert r.program('main') in (None, pid)   # 释放后 program 表仍可查（语义：句柄表）
    print(f'  ok: render 记账/回滚 {st}')


# -------------------------------------------------------------- economy ----
def test_economy_spend_and_balance():
    e = EconomyService(_ctx(), {'gold': 100})
    assert e.balance('gold') == 100.0
    assert e.spend('gold', 40) is True
    assert e.balance('gold') == 60.0
    assert e.spend('gold', 1000) is False, '余额不足必须返回 False'
    assert e.balance('gold') == 60.0, '失败的花费不应改动余额'
    e.add('gold', 5)
    assert e.balance('gold') == 65.0
    assert e.balance('unknown') == 0.0
    print('  ok: economy add/spend/balance + 不足拒绝')


# --------------------------------------------------------------- entity ----
def test_entity_crud_and_query():
    s = EntityService(_ctx(), None)
    a = s.spawn('tower', (1.0, 0.0, 1.0), hp=10)
    b = s.spawn('enemy', (5.0, 0.0, 5.0), hp=3)
    c = s.spawn('enemy', (6.0, 0.0, 6.0), hp=3)
    assert s.count() == 3 and s.count('enemy') == 2
    assert s.get(a)['kind'] == 'tower'
    hits = s.query({'min': (4.5, -1.0, 4.5), 'max': (6.5, 1.0, 6.5)})
    assert sorted(hits) == sorted([b, c]), hits
    s.remove(b)
    assert s.count() == 2 and s.get(b) is None
    print('  ok: entity spawn/get/remove/count/query')


# ---------------------------------------------------------------- rules ----
def test_rules_all_three_win_conditions():
    """塔防 / 神之手 / 射线 FPS 三种胜负口径。"""
    # 塔防：漏怪扣血至 0 -> lost
    r = RulesService(_ctx(), {'base_hp': 3, 'total_waves': 5})
    for _ in range(3):
        r.on_event({'type': 'leak', 'dmg': 1})
    assert r.state() == 'lost' and r.lose() is True and r.win() is False
    # 塔防：过 5 波 -> won
    r2 = RulesService(_ctx(), {'total_waves': 2})
    r2.on_event({'type': 'wave_cleared'})
    assert r2.state() == 'prep' and not r2.win()
    r2.on_event({'type': 'wave_cleared'})
    assert r2.win() is True
    # 神之手：聚落全清 -> won
    r3 = RulesService(_ctx(), None)
    r3.on_event({'type': 'settlements_cleared'})
    assert r3.win() is True
    # FPS：抵达出口 -> won；受击致死 -> lost
    r4 = RulesService(_ctx(), {'base_hp': 10})
    r4.on_event({'type': 'exit_reached'})
    assert r4.win() is True
    r5 = RulesService(_ctx(), {'base_hp': 2})
    r5.on_event({'type': 'player_hit', 'dmg': 2})
    assert r5.lose() is True
    # reset
    r5.reset()
    assert r5.state() == 'prep' and r5.base_hp == 2
    print('  ok: rules 三口径胜负 + reset')


# -------------------------------------------------------------- terrain ----
def test_terrain_edit_and_mesh():
    t = TerrainService(_ctx(), None)
    g = t.g
    cx = cz = 0.0
    h0 = t.height_at(cx, cz)
    t.edit_height(cx, cz, +2.0)
    assert abs(t.height_at(cx, cz) - (h0 + 2.0)) < 1e-9, 'edit_height 应增量'
    t.edit_height(cx, cz, -5.0)
    h1 = t.height_at(cx, cz)
    assert abs(h1 - (h0 - 3.0)) < 1e-9
    # 钳制 [-6, 8]
    for _ in range(50):
        t.edit_height(cx, cz, +1.0)
    assert t.height_at(cx, cz) <= 8.0 + 1e-9, '高度上钳制'
    pos, shade = t.rebuild_mesh()
    exp_verts = (g - 1) * (g - 1) * 6 * 3
    assert len(pos) == exp_verts, f'顶点数 {len(pos)} != {exp_verts}'
    assert len(shade) == len(pos), '颜色与位置等长'
    assert t.ground_height(cx, cz) == t.height_at(cx, cz)
    print(f'  ok: terrain edit/clamp/rebuild_mesh（{g}×{g}，{exp_verts // 3} 顶点）')


# -------------------------------------------------------------- pathing ----
def test_pathing_astar_and_formation():
    p = PathingService(_ctx(), None)
    # 无障碍：直线两点
    assert p.find_path((0, 0), (3, 4)) == [(0, 0), (3, 4)]
    # 有障碍：3x3 网格，中间 (1,1) 是墙 -> 绕行
    grid = [[True, True, True], [True, False, True], [True, True, True]]
    path = p.find_path((0, 0), (2, 2), grid)
    assert path[0] == (0, 0) and path[-1] == (2, 2), path
    assert (1, 1) not in path, '路径不得穿墙'
    assert len(path) >= 2
    # 不可达
    blocked = [[True, False], [False, True]]
    assert p.find_path((0, 0), (1, 1), blocked) == []
    # 编队
    ring = p.formation((0.0, 0.0, 0.0), 6, 1.0, 'ring')
    assert len(ring) == 6
    grid_f = p.formation((0.0, 0.0, 0.0), 4, 2.0, 'grid')
    assert len(grid_f) == 4
    print(f'  ok: pathing A*（绕障 {len(path)} 点）+ ring/grid 编队')


# ------------------------------------------------------------ particles ----
def test_particles_emit_and_lifetime():
    ps = ParticlesService(_ctx(), {'seed': 7})
    assert ps.count() == 0
    ps.emit('spark', 100, origin=(0.0, 0.0, 0.0))
    assert ps.count() == 100
    ps.update(0.2)
    assert ps.count() == 100, '寿命 0.8s，0.2s 后应全存活'
    for _ in range(6):
        ps.update(0.2)
    assert ps.count() == 0, f'1.4s 后应全部回收，实际 {ps.count()}'
    n = ps.set_count(5)
    assert n == 5 and ps.count() == 5
    print('  ok: particles emit/update 寿命回收 + set_count')


# ------------------------------------------------------------------ sdf ----
def test_sdf_voxel_and_mesh():
    s = SdfVoxelService(_ctx(), None)
    assert s.voxel_get(0, 0, 0) == 0
    s.voxel_set(0, 0, 0, 3)
    assert s.voxel_get(0, 0, 0) == 3
    assert s.block_at(0, 0, 0) == 3
    s.voxel_set(0, 0, 0, 0)          # 置空气 = 删除
    assert s.voxel_get(0, 0, 0) == 0
    # 单个孤立方块 -> 6 面 × 2 三角形 × 3 顶点 = 36 顶点 × 3 分量
    s.voxel_set(0, 0, 0, 1)
    pos, shade = s.world_mesh()
    assert len(pos) == 6 * 6 * 3, f'孤立方块应 36 顶点，实际 {len(pos) // 3}'
    assert len(shade) == len(pos)
    # 相邻方块 -> 共享面被剔除（2 方块若完全相邻则 10 面而非 12）
    s.voxel_set(1, 0, 0, 1)
    pos2, _ = s.world_mesh()
    assert len(pos2) // 3 == 60, f'两相邻方块应 10 面=60 顶点，实际 {len(pos2) // 3}'
    print('  ok: sdf 体素读写 + 面剔除网格（1 块 36 顶点 / 2 块 60 顶点）')


# ------------------------------------------------------------- timeline ----
def test_timeline_factories():
    from engine.timeline import Channel, PlaybackController, Sampler, Timeline

    tl = TimelineService(_ctx(), None)
    smp = tl.make_sampler([0.0, 1.0], [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]], 'LINEAR')
    assert isinstance(smp, Sampler)
    v = smp.sample(0.5)
    mid = [v.x, v.y, v.z] if hasattr(v, 'x') else list(v)
    assert abs(mid[0] - 0.5) < 1e-6, mid
    node = type('N', (), {'translation': (0.0, 0.0, 0.0)})()
    ch = tl.make_channel(node, 'translation', smp)
    assert isinstance(ch, Channel)
    t = tl.make_timeline(1.0, [ch])
    assert isinstance(t, Timeline)
    pb = tl.make_playback(t, looping=False, speed=2.0)
    assert isinstance(pb, PlaybackController)
    print('  ok: timeline make_sampler/channel/timeline/playback（0.5s 采样中值 0.5）')


# ---------------------------------------------------------------- audio ----
def test_audio_track_and_render():
    a = AudioService(_ctx(), None)
    track = a.create_track(0.05, rate=8000)
    a.tone(track, 0.0, 0.05, 440.0, wave='sine', amp=0.5)
    a.noise(track, 0.0, 0.02, amp=0.2, seed=1)
    assert track.peak() > 0.0, '应合成了非零信号'
    with tempfile.TemporaryDirectory() as d:
        out = a.render_wav(track, os.path.join(d, 't.wav'))
        assert os.path.isfile(out) and os.path.getsize(out) > 44, '应写出非空 wav'
    assert isinstance(a.available(), bool)
    print(f'  ok: audio create_track/tone/render_wav（peak={track.peak():.3f}）')


# ----------------------------------------------------------- contracts ----
def test_contracts_verify_no_err():
    """契约自检：任何核都不得出现 err:（cordis 缺失记为 pending，不算失败）。"""
    from core import contracts as c
    res = c.verify(verbose=False)
    bad = {k: v for k, v in res.items() if str(v).startswith('err:')}
    assert not bad, f'契约自检出现错误: {bad}'
    print(f'  ok: contracts.verify 无 err（{res}）')


TESTS = [
    test_cores_registry_complete,
    test_protocol_structures,
    test_render_accounting_rollback,
    test_economy_spend_and_balance,
    test_entity_crud_and_query,
    test_rules_all_three_win_conditions,
    test_terrain_edit_and_mesh,
    test_pathing_astar_and_formation,
    test_particles_emit_and_lifetime,
    test_sdf_voxel_and_mesh,
    test_timeline_factories,
    test_audio_track_and_render,
    test_contracts_verify_no_err,
]


def main():
    print('=' * 60)
    print('core 共同核测试（P0-1）')
    print('=' * 60)
    failed = []
    for t in TESTS:
        print(t.__name__)
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, e))
            import traceback
            traceback.print_exc()
            print(f'  FAIL: {e}')
    print('-' * 60)
    for name, e in failed:
        print(f'FAILED {name}: {e}')
    if failed:
        print(f'{len(failed)}/{len(TESTS)} FAILED')
        return 1
    print('ALL_CORE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

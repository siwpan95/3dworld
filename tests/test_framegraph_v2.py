# -*- coding: utf-8 -*-
"""帧图 v2 测试套件 —— 生命周期 / 别名组 / 槽位池 / 峰值（P0-3）。

运行::

    python -m tests.test_framegraph_v2

v1（`tests/test_framegraph.py`）钉的是"顺序、剔除、直通、环"；
本套件钉 **v2 的四本账**：

* **生命周期**：`standard_3d_plan` 的每个 transient 资源编译后都有
  `first_use < last_use`，且与 `plan.lifetimes` 逐项一致；
* **别名组**：生命周期不重叠 + 描述符相同 ⇒ 同一 `alias_group`；
  重叠 ⇒ 不许同组（反例与正例都在）；
* **峰值**：关掉 dof ⇒ `peak_bytes` 严格下降（"关掉效果资源真的少了"）；
* **池**：`compile()` 借槽位、`release_plan()` 归还；连跑 10 轮槽位**不增长**
  （泄漏的判据是"创建数不涨"，不是"空闲数非零"）。

外加两条守卫：读未写资源报错（消息带资源名与 pass 名）；`cli/frame_plan.py`
的 **16 种效果组合**门禁仍然全绿（直接复用它的组合枚举，不复制常量）。
"""
import itertools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.cli import frame_plan as fp                          # noqa: E402
from world3d.engine.framegraph import (Barrier, FrameGraph, FrameGraphError,  # noqa: E402
                                        SlotPool, derive_barriers,
                                        estimate_bytes, resolve_size,
                                        standard_3d_plan)


def _expect_error(fn, label, needles=()):
    try:
        fn()
    except FrameGraphError as exc:
        msg = str(exc)
        for needle in needles:
            assert needle in msg, f'{label}: 报错信息里应含 {needle!r}，实际 {msg}'
        return msg
    raise AssertionError(f'{label}：竟然没有报错')


# ------------------------------------------------------------- 生命周期 --
def test_standard_plan_lifetimes():
    """标准 3D 帧的每个瞬时资源都要有 `first_use < last_use`，并与 Resource 回写一致。"""
    plan = standard_3d_plan().compile()
    checked = 0
    for name, res in plan.resources.items():
        if res.lifetime != 'transient':
            continue
        checked += 1
        assert name in plan.lifetimes, f'{name} 是瞬时资源却没进生命周期表'
        lo, hi = plan.lifetimes[name]
        assert lo < hi, f'{name} 的 first_use({lo}) 应严格小于 last_use({hi})'
        assert (res.first_use, res.last_use) == (lo, hi), \
            f'{name}: Resource 回写 {(res.first_use, res.last_use)} != 计划 {lo, hi}'
    assert checked >= 5, f'标准帧至少 5 个瞬时资源，实际 {checked}'
    # 外部资源不需要生产者，也不占瞬时账本
    assert plan.resources['backbuffer'].lifetime == 'external'
    assert 'backbuffer' not in [n for n, r in plan.resources.items() if r.lifetime == 'transient']
    # 从没被任何 pass 碰过的资源没有生命周期
    assert plan.resources['scene_color'].first_use is None
    print(f'  ok: {checked} 个 transient 资源的 first_use/last_use 都由编译推导且 first<last')


def test_disable_clears_stale_lifetimes():
    """关掉 pass 后重新编译，被直通掉的资源不许残留上一轮的 first_use/last_use。"""
    g = standard_3d_plan()
    full = g.compile()
    assert full.resources['hdr_dof'].first_use is not None
    g.disable('dof_pass')
    part = g.compile()
    assert part.resources['hdr_dof'].first_use is None, \
        f'hdr_dof 已不在本帧，first_use 应清空：{part.resources["hdr_dof"]}'
    g.enable('dof_pass')
    back = g.compile()
    assert back.resources['hdr_dof'].first_use is not None
    print('  ok: 关掉 dof → hdr_dof 的生命周期被清空；重新打开又有了（不残留旧值）')


# --------------------------------------------------------------- 别名 --
def test_disjoint_lifetimes_share_an_alias_group():
    """正例：A 在 p1 写/p2 读，B 在 p3 写/p4 读 ⇒ A、B 落在同一 `alias_group`。"""
    g = FrameGraph()
    g.resource('A', fmt='rgba16f')
    g.resource('B', fmt='rgba16f')
    g.resource('mid', fmt='rgba16f')
    g.resource('out', fmt='rgba8')
    g.external('bb', fmt='rgba8')
    g.add('p1', writes=('A',))
    g.add('p2', reads=('A',), writes=('mid',))
    g.add('p3', reads=('mid',), writes=('B',))
    g.add('p4', reads=('B',), writes=('out',))
    g.add('present', reads=('out', 'bb'), writes=(), side_effect=True)
    g.set_output('out')
    plan = g.compile()

    ga, gb = plan.group_of('A'), plan.group_of('B')
    assert ga is not None and ga == gb, \
        f'A{plan.lifetimes["A"]} 与 B{plan.lifetimes["B"]} 不重叠，应同组：{plan.alias_group}'
    assert plan.group_of('mid') != ga, 'mid 与 A/B 都有重叠，不该挤进同一组'
    group = plan.aliases[ga]
    assert 'A' in group and 'B' in group, f'别名表与 alias_group 不一致：{plan.aliases}'
    print(f'  ok: A{plan.lifetimes["A"]} 与 B{plan.lifetimes["B"]} 同组（组 {ga}），'
          f'mid 因重叠被拆开')


def test_overlapping_lifetimes_are_never_aliased():
    """反例：A 与 B 的区间在 p3 重叠 ⇒ 绝不许同组（也不许出现在同一条 aliases 里）。"""
    g = FrameGraph()
    g.resource('A', fmt='rgba16f')
    g.resource('B', fmt='rgba16f')
    g.resource('out', fmt='rgba8')
    g.external('bb', fmt='rgba8')
    g.add('p1', writes=('A',))
    g.add('p2', writes=('B',))
    g.add('p3', reads=('A', 'B'), writes=('out',))
    g.add('present', reads=('out', 'bb'), writes=(), side_effect=True)
    g.set_output('out')
    plan = g.compile()
    assert plan.lifetimes['A'] == (0, 2) and plan.lifetimes['B'] == (1, 2), plan.lifetimes
    ga, gb = plan.group_of('A'), plan.group_of('B')
    assert not (ga is not None and ga == gb), f'重叠生命周期不该同组：{plan.alias_group}'
    for group in plan.aliases:
        assert not ('A' in group and 'B' in group), f'A/B 出现在同一别名组：{group}'
    print('  ok: A(0,2) 与 B(1,2) 在 p3 共活 → 同组判据拒绝（无共同组号、无同组条目）')


def test_alias_groups_require_identical_descriptors():
    """v2 的硬规则：同组资源必须 `(fmt, size, samples)` 完全一致（否则池里分配不出来）。"""
    plan = standard_3d_plan().compile()
    for group in plan.aliases:
        descs = {plan.resources[n].desc() for n in group}
        assert len(descs) == 1, f'别名组 {group} 描述符不一致：{descs}'
    # 反例：r32f 与 rgba16f 生命周期虽不重叠，也不许同组
    g = FrameGraph()
    g.resource('f32', fmt='r32f')
    g.resource('rgba', fmt='rgba16f')
    g.resource('o1', fmt='rgba8')
    g.resource('o2', fmt='rgba8')
    g.external('bb', fmt='rgba8')
    g.add('s1', writes=('f32',))
    g.add('s2', reads=('f32',), writes=('o1',))
    g.add('s3', reads=('o1',), writes=('rgba',))
    g.add('s4', reads=('rgba',), writes=('o2',))
    g.add('present', reads=('o2', 'bb'), writes=(), side_effect=True)
    g.set_output('o2')
    p = g.compile()
    gf, gr = p.group_of('f32'), p.group_of('rgba')
    assert not (gf is not None and gf == gr), f'不同格式不该同组：{p.alias_group}'
    print('  ok: 别名组的描述符一致（标准帧）＋ r32f/rgba16f 因格式不同被拒（反例）')


# --------------------------------------------------------------- 峰值 --
def test_peak_bytes_drops_when_dof_is_disabled():
    """关掉景深 ⇒ `peak_bytes` 严格下降（建图期与运行期两种关法都要成立）。"""
    full = standard_3d_plan().compile()
    build_off = standard_3d_plan(dof=False).compile()
    g = standard_3d_plan()
    g.disable('dof_pass')
    runtime_off = g.compile()

    assert build_off.peak_bytes < full.peak_bytes, \
        f'关掉 dof 后 peak_bytes 应严格下降：{build_off.peak_bytes} vs {full.peak_bytes}'
    assert runtime_off.peak_bytes < full.peak_bytes, \
        f'运行期关 dof 后 peak_bytes 应严格下降：{runtime_off.peak_bytes} vs {full.peak_bytes}'
    assert build_off.peak_slots < full.peak_slots, \
        f'少一张 hdr_dof，peak_slots 应下降：{build_off.peak_slots} vs {full.peak_slots}'
    assert 'hdr_dof' not in build_off.lifetimes and 'hdr_dof' not in runtime_off.lifetimes
    assert build_off.peak_bytes == runtime_off.peak_bytes, '两种关法应算出同一个账本'
    print(f'  ok: peak_bytes {full.peak_bytes} → {build_off.peak_bytes}（'
          f'-{full.peak_bytes - build_off.peak_bytes} B），两种关法一致')


def test_peak_estimation_is_testable_alone():
    """估算函数可单独调：每像素字节 × 像素 × 采样数；尺寸别名也能单独解析。"""
    assert estimate_bytes('rgba8', 4, 4) == 64
    assert estimate_bytes('rgba16f', 4, 4) == 128
    assert estimate_bytes('r32f', 4, 4) == 64
    assert estimate_bytes('rgba8', 4, 4, samples=4) == 256, 'MSAA 应按采样数放大'
    assert resolve_size('screen', 100, 60) == (100, 60)
    assert resolve_size('half', 100, 60) == (50, 30)
    assert resolve_size((8, 9), 100, 60) == (8, 9)
    _expect_error(lambda: estimate_bytes('nope32x', 4, 4), '未知格式应报错', ('nope32x',))
    _expect_error(lambda: resolve_size('double', 4, 4), '未知尺寸别名应报错', ('double',))
    print('  ok: estimate_bytes/resolve_size 可单测（未知格式直接报错，不猜）')


# ----------------------------------------------------------------- 池 --
def test_slot_pool_leases_and_releases_without_leaking():
    """连跑 10 轮 compile → release：槽位**创建数不增长**，借出数每轮归零。"""
    pool = SlotPool()
    g = standard_3d_plan(pool=pool)
    first = g.compile()
    leases = len(first.pool_leases)
    assert leases > 0 and first.pool_slot, '有池时应给出槽位分配'
    assert pool.leased_slots() == leases
    assert g.release_plan(first) == leases
    del first

    steady_created = pool.created_slots()
    steady_free = pool.free_slots()          # 稳态初值：全部归还之后
    assert pool.leased_slots() == 0

    for _ in range(10):
        plan = g.compile()
        assert len(plan.pool_leases) == leases, f'每帧借的槽位数应恒定：{plan.pool_leases}'
        assert pool.leased_slots() == leases and pool.free_slots() == 0
        assert g.release_plan(plan) == leases
        assert pool.leased_slots() == 0 and pool.free_slots() == steady_free, \
            f'归还后应回到稳态：{pool.stats()}'
        assert pool.created_slots() == steady_created, \
            f'槽位创建数增长了（泄漏）：{pool.stats()}'
        assert plan.pool_leases == (), '归还后计划不应再持有租约（防重复归还）'
        assert g.release_plan(plan) == 0, '重复归还应是幂等的 0'

    assert pool.free_slots() == steady_free and pool.created_slots() == steady_created
    print(f'  ok: 10 轮借还后 created={pool.created_slots()} free={pool.free_slots()} '
          f'leased={pool.leased_slots()}（与首轮稳态一致，无泄漏）')


def test_pool_slots_are_stable_across_frames():
    """同一张图逐帧 compile→release 应拿到**同一组**槽位号（FIFO 复用，帧间不抖）。"""
    pool = SlotPool()
    g = standard_3d_plan(pool=pool)
    a = g.compile()
    sa = dict(a.pool_slot)
    n_leases = len(a.pool_leases)
    g.release_plan(a)
    b = g.compile()
    assert b.pool_slot == sa, f'槽位号在帧间漂移：{sa} vs {b.pool_slot}'
    assert pool.created_slots() == n_leases, '第二帧不该新建槽位'
    # 别名资源必须同槽位
    for group in b.aliases:
        slots = {b.slot_of(n) for n in group}
        assert len(slots) == 1, f'别名组 {group} 的槽位不一致：{slots}'
    g.release_plan(b)
    print(f'  ok: 两帧拿到同一组槽位号；别名组共享槽位（created={pool.created_slots()}）')


# --------------------------------------------------------------- 校验 --
def test_reading_an_unwritten_resource_names_both_sides():
    """读一个从未被写、也非 external 的资源 ⇒ 报错，且消息里带**资源名与 pass 名**。"""
    g = FrameGraph()
    g.resource('ghost_target', fmt='rgba16f')
    g.resource('out', fmt='rgba8')
    g.add('composite_pass', reads=('ghost_target',), writes=('out',))
    g.set_output('out')
    msg = _expect_error(g.compile, '读未写资源', ('ghost_target', 'composite_pass'))
    assert 'lifetime' in msg, f'报错应说明资源生命周期状态：{msg}'
    # 对照组：同样"没人写"，但声明成 external 就合法
    g2 = FrameGraph()
    g2.external('from_engine', fmt='rgba16f')
    g2.resource('out', fmt='rgba8')
    g2.add('composite_pass', reads=('from_engine',), writes=('out',))
    g2.set_output('out')
    assert g2.compile().order == ['composite_pass']
    print(f'  ok: 报错同时点名资源与 pass（{msg[:38]}…）；external 资源不需要生产者')


# --------------------------------------------------------------- 门禁 --
def test_all_sixteen_effect_combinations_still_pass():
    """回归：复用 `cli/frame_plan.py` 的枚举与判定，16 种组合仍然全绿。"""
    combos = [c for r in range(len(fp.EFFECTS) + 1)
              for c in itertools.combinations(fp.EFFECTS, r)]
    assert len(combos) == 16, f'组合数应为 2^{len(fp.EFFECTS)} = 16，实际 {len(combos)}'
    for off in combos:
        label = f'关掉 {",".join(off) or "无"}'
        plan = fp.build(off).compile()
        problems = fp.check(plan, label)
        assert problems == [], f'{label}: {problems}'
        assert plan.peak_bytes > 0 and plan.peak_slots > 0, f'{label} 峰值账本为空'
    print(f'  ok: {len(combos)} 种效果组合全部合法、顺序正确，且每种的峰值账本非空')


# --------------------------------------------------- 自动屏障计划（v2+）--
def test_barriers_are_derived_for_raw_hazards():
    """写→读（RAW）必须插屏障：标准计划里 scene_pass 读 depth/shadow 即典型。"""
    plan = standard_3d_plan().compile()
    kinds = {(b.resource, b.hazard) for b in plan.barriers}
    assert ('distance', 'RAW') in kinds, sorted(kinds)
    assert ('shadow', 'RAW') in kinds, sorted(kinds)
    for b in plan.barriers:
        assert b.hazard in ('RAW', 'WAR', 'WAW'), b
    print(f'  ok: 标准计划推导 {len(plan.barriers)} 条屏障，RAW 依赖全部覆盖')


def test_barriers_catch_alias_reuse_war_hazard():
    """**关键**：别名复用产生的 WAR 冒险必须被抓到。

    `hdr` 与 `hdr_dof` 共用同一物理槽位；按资源名追踪会漏（hdr_dof 此前从未
    被访问过），按**物理存储（别名组）**追踪才抓得到 —— 这是手写 barrier 最
    容易漏的一类。
    """
    plan = standard_3d_plan().compile()
    war = [b for b in plan.barriers if b.hazard == 'WAR']
    assert war, '别名复用应至少产生一条 WAR 屏障'
    # 每条 WAR 的 storage 应是别名组键（组内资源共享存储）
    for b in war:
        assert b.storage == plan.alias_group.get(b.resource, b.resource)
        assert plan.alias_group.get(b.resource) is not None, \
            f'{b.resource} 的 WAR 应来自别名复用'
    print(f'  ok: 抓到 {len(war)} 条别名复用 WAR 冒险：'
          f'{[b.resource for b in war]}')


def test_read_after_read_needs_no_barrier():
    plan = standard_3d_plan().compile()
    # 只读→只读不构成冒险：任何屏障都不该是 read->read
    for b in plan.barriers:
        assert not (b.prev_kind == 'read' and b.kind == 'read'), b
    # 构造：两个 pass 都只读同一外部资源 ⇒ 零屏障
    g = FrameGraph()
    g.external('lut')
    g.resource('out')
    g.add('a', reads=('lut',), writes=('out',))
    g.add('b', reads=('lut', 'out'), writes=())
    g.set_output('out')
    p = g.compile()
    lut_barriers = [b for b in p.barriers if b.resource == 'lut']
    assert lut_barriers == [], f'lut 只读两次不该有屏障：{lut_barriers}'
    print('  ok: 只读→只读不插屏障（无假阳性）')


def test_barriers_are_deterministic_and_stable():
    """确定性：同图重复编译、重复推导，逐条相同（可进快照断言）。"""
    g = standard_3d_plan()
    a = [b.as_dict() for b in g.compile().barriers]
    b = [b.as_dict() for b in g.compile().barriers]
    assert a == b, '同一张图两次编译的屏障计划必须逐条相同'
    plan = g.compile()
    assert [x.as_dict() for x in derive_barriers(plan)] == a, '重推必须与编内一致'
    # 排序稳定：按 (pass_index, resource)
    idx = [x['index'] for x in a]
    assert idx == sorted(idx), '屏障应按执行序排列'
    print('  ok: 屏障计划确定性 + 按执行序稳定排序')


def test_barriers_of_returns_only_that_pass():
    plan = standard_3d_plan().compile()
    total = 0
    for name in plan.order:
        bs = plan.barriers_of(name)
        for b in bs:
            assert b.pass_name == name
        total += len(bs)
    assert total == len(plan.barriers), '每个 pass 的屏障之和应等于总数'
    print(f'  ok: barriers_of 按 pass 切分完整（合计 {total} 条）')


def test_barrier_exposes_gl_bits_without_calling_gl():
    """屏障给出建议的 GL 位（**只是数据**），本模块不调用任何 GL。"""
    plan = standard_3d_plan().compile()
    assert plan.barriers
    for b in plan.barriers:
        assert isinstance(b, Barrier)
        assert b.gl_bits and 'GL_' in b.gl_bits, b.hazard
    # 未冒险的组合为 None
    from world3d.engine.framegraph import HAZARDS
    assert HAZARDS[('read', 'read')] is None
    print('  ok: 屏障自带 GL 位建议（数据），且 read→read 判为无冒险')


# --------------------------------------------------------------- runner --
def main() -> int:
    tests = [
        ('test_standard_plan_lifetimes', test_standard_plan_lifetimes),
        ('test_disable_clears_stale_lifetimes', test_disable_clears_stale_lifetimes),
        ('test_disjoint_lifetimes_share_an_alias_group',
         test_disjoint_lifetimes_share_an_alias_group),
        ('test_overlapping_lifetimes_are_never_aliased',
         test_overlapping_lifetimes_are_never_aliased),
        ('test_alias_groups_require_identical_descriptors',
         test_alias_groups_require_identical_descriptors),
        ('test_peak_bytes_drops_when_dof_is_disabled',
         test_peak_bytes_drops_when_dof_is_disabled),
        ('test_peak_estimation_is_testable_alone', test_peak_estimation_is_testable_alone),
        ('test_slot_pool_leases_and_releases_without_leaking',
         test_slot_pool_leases_and_releases_without_leaking),
        ('test_pool_slots_are_stable_across_frames',
         test_pool_slots_are_stable_across_frames),
        ('test_reading_an_unwritten_resource_names_both_sides',
         test_reading_an_unwritten_resource_names_both_sides),
        ('test_all_sixteen_effect_combinations_still_pass',
         test_all_sixteen_effect_combinations_still_pass),
        # ---- 自动屏障计划（v2+）----
        ('test_barriers_are_derived_for_raw_hazards',
         test_barriers_are_derived_for_raw_hazards),
        ('test_barriers_catch_alias_reuse_war_hazard',
         test_barriers_catch_alias_reuse_war_hazard),
        ('test_read_after_read_needs_no_barrier',
         test_read_after_read_needs_no_barrier),
        ('test_barriers_are_deterministic_and_stable',
         test_barriers_are_deterministic_and_stable),
        ('test_barriers_of_returns_only_that_pass',
         test_barriers_of_returns_only_that_pass),
        ('test_barrier_exposes_gl_bits_without_calling_gl',
         test_barrier_exposes_gl_bits_without_calling_gl),
    ]
    failed = []
    for name, fn in tests:
        try:
            print(name)
            fn()
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_framegraph_v2] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_FRAMEGRAPH_V2_TESTS_FAILED')
        return 1
    print('ALL_FRAMEGRAPH_V2_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

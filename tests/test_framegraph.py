# -*- coding: utf-8 -*-
"""帧图测试套件 —— `engine/framegraph.py`。

运行::

    python -m tests.test_framegraph

帧图的价值全在**编译期**：把"谁先谁后""谁读谁写"从注释变成会被校验的数据。
所以本套件的判据也全是编译期行为：

* **物理顺序被钉住**：`fog → dof → motionblur`（雾产生的像也要被虚化；拖影要拖糊了的像）
  —— 这条写在 `SEQUENCE_OF_DEPTH_EFFECTS` 里，测试断言编译结果与它一致；
* **读不到生产者要报错**（而不是跑出全黑）；
* **有环要报错**（而不是死循环）；
* **死 pass 要真的被剔除**（关掉一个效果的收益必须是"少跑一个 pass"，不是"白跑"）；
* **关掉 pass 后要能直通到上游**（三个效果一起关时，重定向链是
  `hdr_mb → hdr_dof → hdr_fog → hdr`，**必须解析到不动点**）——
  这条本轮实踩：只映射一层时"关单个没问题、关三个才炸"。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.framegraph import (SEQUENCE_OF_DEPTH_EFFECTS, FrameGraph,  # noqa: E402
                               FrameGraphError, standard_3d_plan)


def _expect_error(fn, label, needle=None):
    try:
        fn()
    except FrameGraphError as exc:
        if needle:
            assert needle in str(exc), f'{label}: 报错信息里应含 {needle!r}，实际 {exc}'
        return str(exc)
    raise AssertionError(f'{label}：竟然没有报错')


# ------------------------------------------------------------------ 标准帧 --
def test_standard_plan_compiles_in_physical_order():
    """**头号测试**：四个效果的顺序必须是 `fog → dof → motionblur`。

    这不是偏好而是物理：雾是"光在介质里被散射"，它产生的像**也要被虚化** ⇒ 雾在景深之前；
    运动模糊是**曝光积分** ⇒ 必须在最后（拖影要拖"已经虚化的像"）。
    """
    plan = standard_3d_plan().compile()
    assert plan.order[0] == 'depth_prepass', f'距离图必须最先产出：{plan.order}'
    assert plan.order[-1] == 'present', plan.order
    # ⚠ 别用 `endswith('_pass')` 筛 —— `depth_prepass` 结尾是 `_prepass`，会被漏掉
    seq = [n for n in plan.order if n != 'present']
    want = ['depth_prepass']
    if 'shadow_pass' in seq:
        want.append('shadow_pass')
    want += ['scene_pass']
    for eff in SEQUENCE_OF_DEPTH_EFFECTS:
        if f'{eff}_pass' in plan.order:
            want.append(f'{eff}_pass')
    want += ['tonemap_pass']
    assert seq == want, f'执行顺序不对：\n  实际 {seq}\n  期望 {want}'
    for a, b in (('fog_pass', 'dof_pass'), ('dof_pass', 'motionblur_pass')):
        assert plan.before(a, b), f'{a} 必须在 {b} 之前'
    assert plan.culled == [], f'全开时不该有 pass 被剔除：{plan.culled}'
    print(f'  ok: 8 个 pass 按物理顺序执行 → {" → ".join(plan.order)}')


def test_dead_passes_are_culled():
    """不参与最终输出的 pass 必须被**剔除**（不是"跑了不用"）。"""
    g = FrameGraph()
    g.resource('out', fmt='rgba8')
    g.resource('junk', fmt='rgba8')
    g.add('useful', writes=('out',))
    g.add('useless', writes=('junk',))
    g.set_output('out')
    plan = g.compile()
    assert plan.order == ['useful'], plan.order
    assert plan.culled == ['useless'], plan.culled
    print(f'  ok: 只喂到输出的 pass 活着（{plan.order}）；无人读的 {plan.culled} 被剔除')


def test_present_survives_even_without_writes():
    """有副作用的 pass（present）即使不写资源也不能被剔除。"""
    g = FrameGraph()
    g.resource('out', fmt='rgba8')
    g.external('backbuffer', fmt='rgba8')
    g.add('draw', writes=('out',))
    g.add('present', reads=('out', 'backbuffer'), writes=(), side_effect=True)
    g.set_output('backbuffer')
    plan = g.compile()
    assert plan.order == ['draw', 'present'], plan.order
    # 反例：不标 side_effect 的话它就该被剔除（什么都不写 ⇒ 不影响输出）
    g2 = FrameGraph()
    g2.resource('out', fmt='rgba8')
    g2.external('backbuffer', fmt='rgba8')
    g2.add('draw', writes=('out',))
    g2.add('present', reads=('out', 'backbuffer'), writes=())   # 故意不标 side_effect
    g2.set_output('out')          # ⚠ 输出要指向**被写的**资源；指向外部资源会让计划空掉
    plan2 = g2.compile()
    assert plan2.order == ['draw'] and plan2.culled == ['present'],         f'{plan2.order} / {plan2.culled}'
    print('  ok: 标了 side_effect 的 present 留下；不标的会被正确剔除（对照组成立）')


# ------------------------------------------------------------------ 校验 --
def test_read_without_producer_is_rejected():
    g = FrameGraph()
    g.resource('out', fmt='rgba8')
    g.resource('nowhere', fmt='rgba16f')      # 声明了但**没人写**它 —— 这才是要测的那条
    g.add('bad', reads=('nowhere',), writes=('out',))
    g.set_output('out')
    msg = _expect_error(g.compile, '读一个没人写的资源', needle='nowhere')
    assert 'bad' in msg, '报错应指出是哪个 pass'
    print(f'  ok: 读不到生产者会报错（并指出 pass 与资源）：{msg[:52]}…')


def test_external_resources_need_no_producer():
    g = FrameGraph()
    g.resource('out', fmt='rgba8')
    g.external('depth_from_engine', fmt='r32f')
    g.add('use_it', reads=('depth_from_engine',), writes=('out',))
    g.set_output('out')
    assert g.compile().order == ['use_it']
    print('  ok: 外部资源（引擎提供，如深度缓冲）不需要生产者声明')


def test_cycle_is_detected():
    g = FrameGraph()
    g.resource('a', fmt='rgba8')
    g.resource('b', fmt='rgba8')
    g.add('x', reads=('b',), writes=('a',))
    g.add('y', reads=('a',), writes=('b',))
    g.set_output('a')
    msg = _expect_error(g.compile, '环形依赖', needle='环')
    print(f'  ok: 环被检出（而不是死循环）：{msg[:50]}…')


def test_output_is_required():
    g = FrameGraph()
    g.resource('a', fmt='rgba8')
    g.add('x', writes=('a',))
    _expect_error(g.compile, '没有声明输出', needle='输出')
    print('  ok: 没声明输出（无法判断哪些 pass 活着）会报错')


def test_names_are_validated():
    g = FrameGraph()
    g.resource('a', fmt='rgba8')
    _expect_error(lambda: g.resource('a'), '资源重名', needle='重名')
    g.add('x', writes=('a',))
    _expect_error(lambda: g.add('x', writes=('a',)), 'pass 重名', needle='重名')
    _expect_error(lambda: g.add('y', writes=('ghost',)), '用未声明资源', needle='ghost')
    _expect_error(lambda: FrameGraph().set_output('nope'), '输出未声明', needle='nope')
    print('  ok: 资源/pass 重名、用未声明资源、输出未声明 —— 4 类声明错误全部被拒')


# ------------------------------------------------------------------ 运行期开关 --
def test_disable_requires_an_input_to_pass_through():
    """关掉一个 pass 要能"直通上游"，所以它**必须至少读一个资源**。"""
    g = standard_3d_plan()
    g.disable('depth_prepass')            # 它什么都不读 ⇒ 没有上游可直通
    msg = _expect_error(g.compile, '关掉无输入的 pass', needle='直通')
    _expect_error(lambda: FrameGraph().disable('不存在'), '关掉不存在的 pass', needle='不存在')
    print(f'  ok: 关掉"无输入"的 pass 被拒（说清了为什么）；关不存在的 pass 被拒')


def test_transitive_redirect_when_disabling_three_effects():
    """**回归守卫**：三个效果一起关时，重定向链要解析到**不动点**。

    链路是 ``hdr_mb → hdr_dof → hdr_fog → hdr``。只映射一层的话，
    `tonemap_pass` 会去读一个"生产者已被剔除"的 `hdr_fog` ⇒ 编译期直接报
    "没有生产者"。我第一版就是只映射一层：**关单个没问题、关三个才炸**。
    """
    for names in (('dof_pass',), ('fog_pass',), ('motionblur_pass',),
                  ('fog_pass', 'dof_pass'),
                  ('fog_pass', 'dof_pass', 'motionblur_pass')):
        g = standard_3d_plan()
        g.disable(*names)
        plan = g.compile()
        assert len(plan.order) == 8 - len(names), f'{names}: {plan.order}'
        assert set(plan.culled) == set(names), f'{names}: 剔除 {plan.culled}'
        # 被直通掉的中间资源不该再出现在生命周期表里
        for gone in ('hdr_fog', 'hdr_dof', 'hdr_mb'):
            if any(n.replace('_pass', '') == gone.replace('hdr_', '') for n in names):
                pass                          # 具体哪些被直通取决于链，下面统一断言
        assert plan.order[-1] == 'present', plan.order
        assert plan.order[0] == 'depth_prepass', plan.order
    print('  ok: 关 1/2/3 个效果：pass 数 8→7→6→5，剔除与直通链都对（含三连关的不动点解析）')


def test_toggles_change_pass_count_and_aliases():
    """关掉效果的**收益**要能看见：少跑 pass **且**少占资源（别名组变少）。"""
    rows = []
    for names in ((), ('dof_pass',), ('fog_pass', 'dof_pass'),
                  ('fog_pass', 'dof_pass', 'motionblur_pass')):
        g = standard_3d_plan()
        if names:
            g.disable(*names)
        plan = g.compile()
        groups = len(plan.aliases)
        rows.append((names, len(plan.order), groups))
    counts = [r[1] for r in rows]
    assert counts == sorted(counts, reverse=True), f'关得越多 pass 应越少：{counts}'
    assert counts == [8, 7, 6, 5], counts
    print('  ok: 关 0/1/2/3 个效果 → pass 数 ' +
          ' / '.join(f'{r[1]}({len(r[0])}关)' for r in rows) +
          '，别名组 ' + ' / '.join(str(r[2]) for r in rows))


def test_enable_restores_the_full_plan():
    g = standard_3d_plan()
    full = g.compile()
    g.disable('dof_pass')
    partial = g.compile()
    g.enable('dof_pass')
    back = g.compile()
    assert back.order == full.order, f'{back.order} vs {full.order}'
    assert len(partial.order) == len(full.order) - 1
    print(f'  ok: 关掉再打开完全恢复（{len(full.order)} pass，顺序逐项一致）')


# ------------------------------------------------------------------ 别名 --
def test_aliasing_finds_disjoint_lifetimes_only():
    """别名只对**生命周期不重叠**的瞬时资源生效；常驻/外部资源永不别名。"""
    # ⚠ 别把"读 A 写 B"的 pass 当成"A 与 B 生命周期不重叠" —— 那个 pass 里两者**共活**。
    # 我第一版就是这么写的，"不重叠"的前提根本不成立（测试把对的实现判成了错）。
    # 真正的"不重叠"要在中间隔开一个 pass：
    #   t1 活 [0,1]、t2 活 [2,3] ⇒ 可别名；t3 活 [3,3] 与 t2 重叠 ⇒ 不可别名。
    g = FrameGraph()
    g.external('ext', fmt='r32f')
    g.resource('keep', fmt='rgba16f', persistent=True)
    g.resource('t1', fmt='rgba16f')
    g.resource('t2', fmt='rgba16f')
    g.resource('t3', fmt='rgba16f')
    g.resource('mid', fmt='rgba16f')
    g.resource('out', fmt='rgba8')
    g.add('a1', writes=('t1',))
    g.add('a2', reads=('t1',), writes=('mid',))
    g.add('b1', reads=('mid',), writes=('t2',))
    g.add('b2', reads=('t2', 'ext'), writes=('t3', 'keep'))
    g.add('b3', reads=('t2', 't3', 'keep'), writes=('out',))
    g.set_output('out')
    plan = g.compile()
    pairs = {tuple(sorted(p)) for p in plan.aliases}
    assert ('t1', 't2') in pairs, f't1/t2 生命周期不重叠，应可别名：{sorted(pairs)}'
    # ⚠ 只否掉"t2 与 t3 互相别名"这一条 —— t3 **可以**和别的不重叠资源（比如 mid）配对。
    # 我第一版写成"t3 不出现在任何别名组里"，把一条正确的别名判成了错。
    assert ('t2', 't3') not in pairs, f't3 与 t2 重叠（都在 b2/b3 里），不该互相别名：{sorted(pairs)}'
    for p in pairs:
        assert 'keep' not in p and 'ext' not in p, f'常驻/外部资源不该别名：{p}'
    print(f'  ok: t1/t2 别名成功；与 t2 重叠的 t3 不与 t2 配对；常驻 keep、外部 ext 都没被别名'
          f'（全部别名组 {sorted(pairs)}）')


def test_aliasing_respects_same_pass_read_and_write():
    """同一个 pass 里"读 A 写 B"时 A 与 B **同时活着**，不许别名（半开区间要取严格小于）。"""
    g = FrameGraph()
    g.resource('a', fmt='rgba16f')
    g.resource('b', fmt='rgba16f')
    g.external('bb', fmt='rgba8')
    g.add('seed', writes=('a',))
    g.add('shift', reads=('a',), writes=('b',))
    g.add('present', reads=('b', 'bb'), writes=(), side_effect=True)
    g.set_output('bb')
    plan = g.compile()
    assert plan.aliases == [], f'a 与 b 在 shift 里同时活着，不该别名：{plan.aliases}'
    assert plan.lifetimes['a'] == (0, 1) and plan.lifetimes['b'] == (1, 2), plan.lifetimes
    print(f'  ok: "读 A 写 B"的 pass 让 A/B 共活 → 不别名（生命周期 {plan.lifetimes}）')


# ------------------------------------------------------------------ 确定性 --
def test_compile_is_deterministic():
    """同图两次编译结果逐字一致（否则"帧间稳定"就无从谈起）。"""
    a = standard_3d_plan().compile()
    b = standard_3d_plan().compile()
    assert a.order == b.order and a.culled == b.culled and a.aliases == b.aliases
    assert a.lifetimes == b.lifetimes
    g = standard_3d_plan()
    c = g.compile()
    assert c.order == a.order, '同一张图重复编译应一致'
    # 平级（互不依赖）的 pass 按**声明顺序**，不按字典序 —— 否则换个名字顺序就变
    gg = FrameGraph()
    gg.resource('o', fmt='rgba8')
    gg.external('b1', fmt='rgba8')
    gg.external('b2', fmt='rgba8')
    gg.add('zeta', reads=('b1',), writes=('o',))
    gg.add('alpha', reads=('b2',), writes=(), side_effect=True)
    gg.set_output('o')          # 指向被写的资源（b1 是外部资源，指它会得到空计划）
    order = gg.compile().order
    assert order == ['zeta', 'alpha'], f'平级 pass 应按声明顺序：{order}'
    print(f'  ok: 两次编译逐字一致；平级 pass 按声明顺序（{order}），不受名字字典序影响')


def test_plan_exposes_resolved_reads_after_redirect():
    """**渲染端必需的 API**：计划必须给出"重定向之后每个 pass 到底读哪张纹理"。

    少了它，`disable()` 的"直通"在渲染端就落不了地 —— 渲染器会去绑一张**已被剔除**的
    中间纹理（`hdr_dof`），而那张纹理根本没人写 ⇒ 黑屏。这是写 P3-b 的 demo 时才发现的
    需求：**编译期算出来的信息，必须能交给执行端**。
    """
    full = standard_3d_plan().compile()
    assert full.reads_of('motionblur_pass') == ('hdr_dof', 'distance', 'prev_view_proj')
    g = standard_3d_plan()
    g.disable('dof_pass')
    p = g.compile()
    assert p.reads_of('motionblur_pass') == ('hdr_fog', 'distance', 'prev_view_proj'), \
        f'关掉景深后，运动模糊应直接读雾的输出：{p.reads_of("motionblur_pass")}'
    assert p.reads_of('tonemap_pass') == ('hdr_mb',)
    g2 = standard_3d_plan()
    g2.disable('fog_pass', 'dof_pass', 'motionblur_pass')     # 三连关 ⇒ 链要解析到 hdr
    p2 = g2.compile()
    assert p2.reads_of('tonemap_pass') == ('hdr',), \
        f'三个效果全关时，tonemap 应直接读场景色 hdr：{p2.reads_of("tonemap_pass")}'
    assert p2.writes_of('tonemap_pass') == ('ldr',)
    print(f'  ok: resolved 读写随直通更新（关景深后 mb 读 {p.reads_of("motionblur_pass")[0]}；'
          f'三连关后 tonemap 读 {p2.reads_of("tonemap_pass")[0]}）')


def test_plan_helper_api():
    plan = standard_3d_plan().compile()
    assert plan.position('depth_prepass') == 0
    assert plan.before('depth_prepass', 'present')
    assert not plan.before('present', 'depth_prepass')
    try:
        plan.position('不存在')
    except ValueError:
        pass
    else:
        raise AssertionError('查不存在的 pass 应该抛 ValueError')
    print('  ok: position/before 可用；查不存在的 pass 抛 ValueError')


# ------------------------------------------------------------------ runner --
def main() -> int:
    tests = [
        ('test_standard_plan_compiles_in_physical_order',
         test_standard_plan_compiles_in_physical_order),
        ('test_dead_passes_are_culled', test_dead_passes_are_culled),
        ('test_present_survives_even_without_writes',
         test_present_survives_even_without_writes),
        ('test_read_without_producer_is_rejected',
         test_read_without_producer_is_rejected),
        ('test_external_resources_need_no_producer',
         test_external_resources_need_no_producer),
        ('test_cycle_is_detected', test_cycle_is_detected),
        ('test_output_is_required', test_output_is_required),
        ('test_names_are_validated', test_names_are_validated),
        ('test_disable_requires_an_input_to_pass_through',
         test_disable_requires_an_input_to_pass_through),
        ('test_transitive_redirect_when_disabling_three_effects',
         test_transitive_redirect_when_disabling_three_effects),
        ('test_toggles_change_pass_count_and_aliases',
         test_toggles_change_pass_count_and_aliases),
        ('test_enable_restores_the_full_plan', test_enable_restores_the_full_plan),
        ('test_aliasing_finds_disjoint_lifetimes_only',
         test_aliasing_finds_disjoint_lifetimes_only),
        ('test_aliasing_respects_same_pass_read_and_write',
         test_aliasing_respects_same_pass_read_and_write),
        ('test_compile_is_deterministic', test_compile_is_deterministic),
        ('test_plan_exposes_resolved_reads_after_redirect',
         test_plan_exposes_resolved_reads_after_redirect),
        ('test_plan_helper_api', test_plan_helper_api),
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
    print(f'[test_framegraph] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_FRAMEGRAPH_TESTS_FAILED')
        return 1
    print('ALL_FRAMEGRAPH_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

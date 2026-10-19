# -*- coding: utf-8 -*-
"""行为树测试：组合/装饰/叶子语义 + 声明式构造 + 假时钟超时。

移植自 zhuangtaiji（MIT）的语义逐项钉住：本测试既是回归保护，也是
「移植没走样」的证明 —— 每个断言对应原版文档明写的节点语义。
"""
from __future__ import annotations

import sys

from world3d.logic.behavior_tree import (
    Action, BehaviorTree, Blackboard, Cooldown, Condition, Failer, Inverter,
    Parallel, Repeater, Selector, Sequence, Service, Status, Succeeder,
    TickContext, Timeout, UntilFailure, UntilSuccess, build_behavior_tree,
)


def _ok(name='ok'):
    return Action(name, func=lambda ctx: Status.SUCCESS)


def _fail(name='fail'):
    return Action(name, func=lambda ctx: Status.FAILURE)


def _running(name='run'):
    return Action(name, func=lambda ctx: Status.RUNNING, running=True)


def test_sequence_semantics():
    """Sequence：遇 FAILURE/RUNNING 即停；全 SUCCESS 才 SUCCESS。"""
    # 全成功
    t = BehaviorTree(Sequence('s', [_ok('a'), _ok('b')]))
    assert t.tick() is Status.SUCCESS
    # 中途失败：后续节点不被 tick（用计数证明短路）
    calls = []

    def rec(ctx):
        calls.append('b')
        return Status.SUCCESS

    t2 = BehaviorTree(Sequence('s', [_fail('a'), Action('b', func=rec)]))
    assert t2.tick() is Status.FAILURE
    assert calls == [], 'FAILURE 后不该执行后续节点'
    # RUNNING 也短路
    t3 = BehaviorTree(Sequence('s', [_running('a'), _ok('b')]))
    assert t3.tick() is Status.RUNNING
    print('  ok: Sequence 成功/失败短路/RUNNING 短路')


def test_selector_semantics():
    """Selector：遇 SUCCESS/RUNNING 即停；全 FAILURE 才 FAILURE。"""
    t = BehaviorTree(Selector('sel', [_fail('a'), _ok('b'), _ok('c')]))
    assert t.tick() is Status.SUCCESS
    # 短路证明：第一个成功后后续不执行
    calls = []

    def rec(ctx):
        calls.append('c')
        return Status.SUCCESS

    t2 = BehaviorTree(Selector('sel', [_ok('a'), Action('c', func=rec)]))
    assert t2.tick() is Status.SUCCESS
    assert calls == []
    # 全失败
    t3 = BehaviorTree(Selector('sel', [_fail('a'), _fail('b')]))
    assert t3.tick() is Status.FAILURE
    print('  ok: Selector 成功短路/全失败')


def test_parallel_thresholds():
    """Parallel：success/failure 阈值语义。"""
    # 默认：全 SUCCESS 才 SUCCESS
    t = BehaviorTree(Parallel('p', [_ok(), _ok(), _fail()]))
    assert t.tick() is Status.FAILURE, '默认 failure_threshold=1，一个失败即失败'
    # success_threshold=2：两个成功即成功（即使第三个失败）
    t2 = BehaviorTree(Parallel('p', [_ok(), _ok(), _fail()], success_threshold=2))
    assert t2.tick() is Status.SUCCESS
    # 有 RUNNING 且未达阈值 → RUNNING
    t3 = BehaviorTree(Parallel('p', [_ok(), _running()], success_threshold=2))
    assert t3.tick() is Status.RUNNING
    print('  ok: Parallel 默认阈值/success_threshold/RUNNING 传播')


def test_decorators():
    """装饰器全集：Inverter/Succeeder/Failer/Repeater/Cooldown/Until*。"""
    # Inverter：SUCCESS<->FAILURE，RUNNING 原样
    assert BehaviorTree(Inverter('i', _ok())).tick() is Status.FAILURE
    assert BehaviorTree(Inverter('i', _fail())).tick() is Status.SUCCESS
    assert BehaviorTree(Inverter('i', _running())).tick() is Status.RUNNING
    # Succeeder / Failer：吞掉子节点结果
    assert BehaviorTree(Succeeder('s', _fail())).tick() is Status.SUCCESS
    assert BehaviorTree(Failer('f', _ok())).tick() is Status.FAILURE
    # Repeater：times=3 → 前两次 RUNNING，第三次 SUCCESS
    n = {'c': 0}

    def inc(ctx):
        n['c'] += 1
        return Status.SUCCESS

    rep = Repeater('r', Action('inc', func=inc), times=3)
    t = BehaviorTree(rep)
    assert t.tick() is Status.RUNNING and n['c'] == 1
    assert t.tick() is Status.RUNNING and n['c'] == 2
    assert t.tick() is Status.SUCCESS and n['c'] == 3
    # Cooldown：成功后 ticks 次强制 FAILURE
    cd = Cooldown('c', _ok(), ticks=2)
    t2 = BehaviorTree(cd)
    assert t2.tick() is Status.SUCCESS   # 首次成功
    assert t2.tick() is Status.FAILURE   # 冷却 1
    assert t2.tick() is Status.FAILURE   # 冷却 2
    assert t2.tick() is Status.SUCCESS   # 冷却结束
    # UntilSuccess：未成功前 RUNNING
    us = UntilSuccess('us', _fail())
    t3 = BehaviorTree(us)
    assert t3.tick() is Status.RUNNING
    # UntilFailure：未失败前 RUNNING
    uf = UntilFailure('uf', _ok())
    t4 = BehaviorTree(uf)
    assert t4.tick() is Status.RUNNING
    print('  ok: 八个装饰器语义（含 Repeater 计数与 Cooldown 冷却窗）')


def test_timeout_with_fake_clock():
    """Timeout：假时钟注入 —— 不真实等待即可验证超时路径（本仓适配点）。"""
    now = {'t': 0.0}
    clock = lambda: now['t']  # noqa: E731
    # 子节点永远 RUNNING：时钟走过 budget 后判 FAILURE
    to = Timeout('to', _running(), budget_ms=100, clock=clock)
    t = BehaviorTree(to)
    now['t'] = 0.0
    assert t.tick() is Status.RUNNING   # 0ms < 100ms
    now['t'] = 0.05
    assert t.tick() is Status.RUNNING   # 50ms < 100ms
    now['t'] = 0.11
    assert t.tick() is Status.FAILURE   # 110ms ≥ 100ms → 超时
    # 子节点及时成功：不超时，且 _start 复位（下次重新计时）
    to2 = Timeout('to2', _ok(), budget_ms=100, clock=clock)
    t2 = BehaviorTree(to2)
    now['t'] = 0.0
    assert t2.tick() is Status.SUCCESS
    now['t'] = 0.2
    assert t2.tick() is Status.SUCCESS, '成功后 _start 应复位，不累积旧时钟'
    print('  ok: Timeout 假时钟（超时路径 + 成功复位）')


def test_condition_action():
    """Condition 查询黑板；Action 的 Status/bool/异常返回语义。"""
    bb = Blackboard({'hp': 50, 'name': '勇士'})
    cond = Condition('hp>30', lambda ctx: ctx.blackboard.get('hp', 0) > 30)
    t = BehaviorTree(cond, blackboard=bb)
    assert t.tick() is Status.SUCCESS
    bb.set('hp', 10)
    assert t.tick() is Status.FAILURE
    # Condition 的 predicate 抛异常 → FAILURE（不向上炸）
    bad = Condition('bad', lambda ctx: 1 / 0)
    assert BehaviorTree(bad).tick() is Status.FAILURE
    # Action：True/None → SUCCESS，False → FAILURE，Status 原样，异常 → FAILURE
    assert BehaviorTree(Action('a', lambda ctx: True)).tick() is Status.SUCCESS
    assert BehaviorTree(Action('a', lambda ctx: None)).tick() is Status.SUCCESS
    assert BehaviorTree(Action('a', lambda ctx: False)).tick() is Status.FAILURE
    assert BehaviorTree(Action('a', lambda ctx: Status.RUNNING)).tick() is Status.RUNNING
    assert BehaviorTree(Action('a', lambda ctx: 1 / 0)).tick() is Status.FAILURE
    # Action 写黑板（条件写、动作读的协作范式）
    bb2 = Blackboard()
    writer = Action('w', lambda ctx: ctx.blackboard.set('done', True))
    reader = Condition('r', lambda ctx: ctx.blackboard.get('done') is True)
    t2 = BehaviorTree(Sequence('s', [writer, reader]), blackboard=bb2)
    assert t2.tick() is Status.SUCCESS
    assert bb2.get('done') is True
    print('  ok: Condition 查询/异常兜底；Action 四种返回 + 黑板协作')


def test_service_node():
    """Service：predicate 满足前 RUNNING，满足后 SUCCESS。"""
    bb = Blackboard({'progress': 0})
    svc = Service('dl', lambda ctx: ctx.blackboard.get('progress', 0) >= 100)
    t = BehaviorTree(svc, blackboard=bb)
    assert t.tick() is Status.RUNNING
    bb.set('progress', 60)
    assert t.tick() is Status.RUNNING
    bb.set('progress', 100)
    assert t.tick() is Status.SUCCESS
    print('  ok: Service 进度门（RUNNING→SUCCESS）')


def test_declarative_build():
    """声明式构造：spec → build_behavior_tree → run 到终态。"""
    spec = {
        'name': '敌人 AI',
        'root': {
            'type': 'selector',
            'children': [
                {  # 血量低 → 撤退（条件 gt 不满足时走下一支）
                    'type': 'sequence',
                    'children': [
                        {'type': 'condition', 'key': 'hp', 'op': 'lt', 'value': 30},
                        {'type': 'action', 'kind': 'set', 'key': 'state',
                         'value': 'retreat'},
                    ],
                },
                {  # 否则 → 进攻
                    'type': 'sequence',
                    'children': [
                        {'type': 'action', 'kind': 'set', 'key': 'state',
                         'value': 'attack'},
                    ],
                },
            ],
        },
    }
    bb = Blackboard({'hp': 80})
    t = build_behavior_tree(spec, blackboard=bb)
    assert t.run() is Status.SUCCESS
    assert bb.get('state') == 'attack', 'hp=80 应走进攻支'
    # 血量低 → 撤退支
    bb2 = Blackboard({'hp': 10})
    t2 = build_behavior_tree(spec, blackboard=bb2)
    assert t2.run() is Status.SUCCESS
    assert bb2.get('state') == 'retreat', 'hp=10 应走撤退支'
    # 声明式 condition 的十种算子抽样
    ops_spec = {'root': {'type': 'condition', 'key': 'x', 'op': 'ge', 'value': 5}}
    assert build_behavior_tree(ops_spec, blackboard=Blackboard({'x': 5})).tick() \
        is Status.SUCCESS
    assert build_behavior_tree(ops_spec, blackboard=Blackboard({'x': 4})).tick() \
        is Status.FAILURE
    # 未知类型炸 ValueError（fail-fast）
    try:
        build_behavior_tree({'root': {'type': 'nonexistent'}})
        raise AssertionError('未知节点类型应抛 ValueError')
    except ValueError:
        pass
    print('  ok: 声明式构造（选择支路由 + 算子抽样 + 未知类型 fail-fast）')


def test_reset_and_run_loop():
    """reset 清状态；run 到非 RUNNING 即停（含 max_ticks 上限）。"""
    n = {'c': 0}

    def inc(ctx):
        n['c'] += 1
        return Status.SUCCESS

    rep = Repeater('r', Action('inc', func=inc), times=5)
    t = BehaviorTree(rep)
    assert t.run() is Status.SUCCESS
    assert n['c'] == 5 and t.tick_count == 5
    t.reset()
    assert t.tick_count == 0 and rep._count == 0, 'reset 应清计数'
    # 永远 RUNNING：run 到 max_ticks 上限返回 RUNNING
    t2 = BehaviorTree(_running())
    assert t2.run(max_ticks=7) is Status.RUNNING
    assert t2.tick_count == 7
    print('  ok: reset 清状态 + run 上限语义')


def test_blackboard_sharing():
    """黑板：initial 注入 / snapshot / update / __contains__。"""
    bb = Blackboard({'a': 1})
    bb.set('b', 2)
    bb.update({'c': 3})
    assert 'a' in bb and 'z' not in bb
    assert bb.snapshot() == {'a': 1, 'b': 2, 'c': 3}
    snap = bb.snapshot()
    bb.set('a', 99)
    assert snap['a'] == 1, 'snapshot 必须是快照（拷贝非引用）'
    assert bb.get('missing', 'default') == 'default'
    # 树内所有节点共享同一块黑板
    inner = _ok('inner')
    t = BehaviorTree(Sequence('s', [inner]), blackboard=bb)
    t.tick()
    assert inner.blackboard is bb, '子节点应绑定树的黑板'
    print('  ok: 黑板注入/快照/共享绑定')


def main() -> int:
    tests = [
        test_sequence_semantics,
        test_selector_semantics,
        test_parallel_thresholds,
        test_decorators,
        test_timeout_with_fake_clock,
        test_condition_action,
        test_service_node,
        test_declarative_build,
        test_reset_and_run_loop,
        test_blackboard_sharing,
    ]
    failed = []
    for t in tests:
        print(t.__name__)
        try:
            t()
        except Exception as e:
            failed.append((t.__name__, e))
            import traceback
            traceback.print_exc()
    print('=' * 60)
    print(f'[test_behavior_tree] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_BEHAVIOR_TREE_TESTS_FAILED')
        return 1
    print('ALL_BEHAVIOR_TREE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

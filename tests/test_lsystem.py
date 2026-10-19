# -*- coding: utf-8 -*-
"""L-System 测试：重写（确定式/随机式）+ 2D/3D 龟图解释器 + 确定性。

golden 值多为**可手算复核**的字符串（F→FF 翻倍、产生式组合），龟图则校验
线段数 / 起止点 / 3D 旋转方向 + 确定性。随机式由注入 ``random.Random`` 复现。
"""
from __future__ import annotations

import math
import random

from world3d.logic import lsystem as L


def test_expand_doubling():
    """公理 'F'，规则 F→FF，迭代 3 次 → 8 个 F（每次翻倍）。"""
    s = L.expand('F', {'F': 'FF'}, 3)
    assert s == 'F' * 8, f'期望 F*8，得到 {s!r}'
    print('  ok: F→FF 迭代 3 次 = 8 个 F')


def test_expand_composition():
    """迭代两次 == 对一次结果再迭代一次（确定性重写的可组合性）。"""
    rule = {'F': 'F+F-F-F+F'}
    two = L.expand('F', rule, 2)
    composed = L.expand(L.expand('F', rule, 1), rule, 1)
    assert two == composed, '两次迭代应等于两次单步的组合'
    print('  ok: 重写可组合（迭代两次 == 组合两次）')


def test_expand_constants():
    """带常量的产生式：X→F+X，F 不变，迭代 2 次 → 'F+F+X'。"""
    s = L.expand('X', {'X': 'F+X', 'F': 'F'}, 2)
    assert s == 'F+F+X', f'期望 F+F+X，得到 {s!r}'
    print('  ok: 常量产生式 F+F+X golden')


def test_expand_stochastic_seed():
    """随机式：同 seed 必同结果；结果字符均来自产生式替换集。"""
    rule = {'F': [(0.5, 'F+F'), (0.5, 'F-F')]}
    a = L.expand('F', rule, 4, rng=random.Random(123))
    b = L.expand('F', rule, 4, rng=random.Random(123))
    assert a == b, '同 seed 随机式必须可复现'
    assert set(a) <= set('F+-'), '随机式结果不应出现产生式外的字符'
    # 不同 seed 可能不同（不强求，但至少合法）
    c = L.expand('F', rule, 4, rng=random.Random(999))
    assert set(c) <= set('F+-')
    print('  ok: 随机式 同 seed 可复现 + 字符集合法')


def test_turtle2d_segments():
    """科赫一阶骨架 'F+F-F-F+F'：5 个 F → 5 条线段，每条长 = step。"""
    t = L.Turtle2D(step=1.0, angle=math.radians(90))
    segs = t.run('F+F-F-F+F')
    assert len(segs) == 5, f'5 个 F 应画 5 段，得到 {len(segs)}'
    for (a, b) in segs:
        assert abs(math.hypot(b[0] - a[0], b[1] - a[1]) - 1.0) < 1e-9, '每段长应=step=1'
    print('  ok: 2D 龟图 5 段 + 每段长 1')


def test_turtle2d_start_end():
    """单步 'F' 从 (0,0) 朝 0° → 线段 ((0,0),(1,0))。"""
    t = L.Turtle2D(pos=(0.0, 0.0), heading=0.0, step=1.0)
    segs = t.run('F')
    assert segs == [((0.0, 0.0), (1.0, 0.0))], f'起止点应为 ((0,0),(1,0))，得到 {segs}'
    print('  ok: 2D 龟图 单步起止点 golden')


def test_turtle2d_bracket_balance():
    """括号 '[ ]' 压栈出栈不影响绘制段数（只存姿态）。'F[+F]F' → 3 段。"""
    t = L.Turtle2D(step=1.0, angle=math.radians(90))
    segs = t.run('F[+F]F')
    assert len(segs) == 3, f'3 个 F 应画 3 段，得到 {len(segs)}'
    print('  ok: 2D 龟图 括号不改变段数（3 段）')


def test_turtle3d_forward():
    """3D 单步 'F'，航向 (1,0,0) → 线段 ((0,0,0),(1,0,0))。"""
    t = L.Turtle3D(pos=(0.0, 0.0, 0.0), heading=(1.0, 0.0, 0.0), step=1.0)
    segs = t.run('F')
    assert segs == [((0.0, 0.0, 0.0), (1.0, 0.0, 0.0))], f'3D 单步应沿 +x，得到 {segs}'
    print('  ok: 3D 龟图 单步沿航向 golden')


def test_turtle3d_yaw():
    """绕上向量 (0,0,1) 左偏航 90° 后，航向 (1,0,0) → (0,1,0)，'F' 沿 +y。"""
    t = L.Turtle3D(heading=(1.0, 0.0, 0.0), up=(0.0, 0.0, 1.0),
                   step=1.0, angle=math.radians(90))
    segs = t.run('+F')
    end = segs[0][1]
    assert abs(end[0]) < 1e-9 and abs(end[1] - 1.0) < 1e-9 and abs(end[2]) < 1e-9, \
        f'偏航后落点应≈(0,1,0)，得到 {end}'
    print('  ok: 3D 龟图 偏航 90° 落点 (0,1,0) golden')


def test_turtle3d_determinism():
    """同指令同初始姿态必同输出（纯几何，无随机）。"""
    cmds = 'F+F[+F-F-F]-F'
    a = L.Turtle3D(angle=math.radians(25)).run(cmds)
    b = L.Turtle3D(angle=math.radians(25)).run(cmds)
    assert a == b
    print('  ok: 3D 龟图 同指令确定性')


def main() -> int:
    tests = [
        test_expand_doubling,
        test_expand_composition,
        test_expand_constants,
        test_expand_stochastic_seed,
        test_turtle2d_segments,
        test_turtle2d_start_end,
        test_turtle2d_bracket_balance,
        test_turtle3d_forward,
        test_turtle3d_yaw,
        test_turtle3d_determinism,
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
    print(f'[test_lsystem] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, e in failed:
            print(f'  - {n}: {e}')
        print('ALL_LSYSTEM_TESTS_FAILED')
        return 1
    print('ALL_LSYSTEM_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())

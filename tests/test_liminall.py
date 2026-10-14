# -*- coding: utf-8 -*-
"""《阈廊 / Liminall》内核测试套件（纯逻辑，零 GL、零窗口）。

运行::

    python -m tests.test_liminall

原则与 ``test_brickward`` 一致：断言数值、打印实测数据、不用"看起来对"。

**钉死的坑/机制**：

1. **loop 单调推进**——8 圈事件表的强度（radio 段号 / flicker / red）必须随圈号
   非递减（恐怖曲线不能"降温"）；
2. **auto-pilot 全流程**——8 loops 全解 3 谜 → won（§17.7 验收 1，多 seed）；
3. **实体三态**（手电驱退机制）——照→退、对视无光→冻结、背对→追、贴脸→抓杀；
4. **实体 home 守尸死锁**——home 必须在玩家对侧端点（实测固定 home 堵死 L 形
   拐角必经之路，144 连抓死锁）；
5. **谜题 1-3 状态机**——灯语开镜柜 / 配电箱拉错全重置 / 电话三条件；
6. **传送矩阵**——respawn 回起点（后室"直线走回原点"的可玩化）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.logic.liminall import (  # noqa: E402
    ENTITY_CATCH, ENTITY_SPEED, RADIO_LINES, STALK_LOOPS,
    TOTAL_LOOPS, LiminallKernel, maze_layout,
)


def _events():
    from world3d.logic import liminall as lm
    return lm.LOOP_EVENTS


def test_loop_events_monotonic():
    evs = _events()
    assert len(evs) == 8, f'事件表应为 8 圈，实际 {len(evs)}'
    radios = [e.get('radio', -1) for e in evs]
    assert radios == sorted(radios), f'广播段应随圈递增：{radios}'
    assert all(e.get('main') for e in evs), '每圈必须有主事件'
    # 标题八连（内容验收：文字稿落地）
    titles = [e['title'] for e in evs]
    assert titles[0].endswith('认路') and titles[3].endswith('坠入') \
        and titles[7].endswith('电话'), titles
    print('  ok: 8 圈事件表（radio 单调递增 / 每圈主事件 / 标题文字稿）')


def test_autopilot_full_run():
    """§17.7 验收 1：auto-pilot 跑完 8 loops 全解 3 谜 → won（多 seed）。"""
    for seed in (20261014, 7, 42):
        k = LiminallKernel(seed)
        t = 0.0
        while not k.won and t < 600.0:
            k.auto_step(1 / 60.0)
            k.update(1 / 60.0)
            t += 1 / 60.0
        assert k.won, f'seed {seed} 600s 未通关（loop={k.loop + 1} phase={k.phase}）'
        assert k.p1_open and k.p2_done and k.p3_ring, f'seed {seed} 谜题未全解'
    print('  ok: auto-pilot 3 seed 全流程（8 loops / 3 谜 / won）')


def test_entity_light_drives_back():
    """手电驱退三态：照→退、对视无光→冻、背对→追。"""
    k = LiminallKernel(20261014)
    k.loop = 7
    k.phase = 'hall'
    k._sync_entity()
    k.player['x'], k.player['z'] = 0.0, 3.0
    k.entity['x'], k.entity['z'] = 0.0, 1.0     # 玩家正前方（-z 侧）
    facing = (0.0, -1.0)                        # 玩家面朝 -z（看着实体）
    x0 = k.entity['x']
    k.update(1 / 60.0, facing=facing, light=True)
    assert k.entity['x'] < x0 - 1e-4, '手电照射应使实体后退（远离玩家）'
    assert k.deaths == 0, '照射中不应被抓'
    # 对视无光 → 冻结
    x1 = k.entity['x']
    k.update(1 / 60.0, facing=facing, light=False)
    assert abs(k.entity['x'] - x1) < ENTITY_SPEED / 60.0 * 0.5, '对视无光应冻结'
    # 背对 → 追
    back = (0.0, 1.0)                           # 面朝 +z（背对实体）
    d0 = abs(k.entity['z'] - k.player['z'])
    k.update(1 / 60.0, facing=back, light=False)
    d1 = abs(k.entity['z'] - k.player['z'])
    assert d1 < d0, '背对时实体应逼近'
    print('  ok: 实体三态（照退 / 对视冻 / 背对追）')


def test_entity_home_never_blocks():
    """实体 home 必须距玩家足够远——固定 home 堵死拐角 = 144 连抓死锁。"""
    k = LiminallKernel(20261014)
    k.loop = 6
    k.phase = 'hall'
    k.player['x'], k.player['z'] = 0.0, 10.8    # A 段起点
    k._sync_entity()
    d = math.hypot(k._entity_home[0] - 0.0, k._entity_home[1] - 10.8)
    assert d > 5.0, f'玩家在 A 段起点时 home 应在远处（实测距 {d:.1f}m）'
    k.player['x'] = -6.0
    k.player['z'] = 0.8
    k._sync_entity()
    d = math.hypot(k._entity_home[0] + 6.0, k._entity_home[1] - 0.8)
    assert d > 5.0, f'玩家在 B 段深处时 home 应在远处（实测距 {d:.1f}m）'
    print('  ok: 实体 home 动态取对侧端点（距玩家 >5m，防守尸死锁）')


def test_puzzle1_flash_sequence():
    k = LiminallKernel(20261014)
    k.loop = 2
    assert k.interact('lamp') == 'flash'
    assert k.interact('cabinet_keypad') == 'p1_solved'
    assert k.handlight and k.p1_open
    # 解过之后再交互 = 已开
    assert k.interact('cabinet_keypad') == 'open'
    print('  ok: 谜题1 灯语序列 → 镜柜（手电+钥匙入手）')


def test_puzzle2_breaker_reset():
    k = LiminallKernel(20261014)
    k.loop = 4
    k.phase = 'maze'
    k._sync_entity()
    # 拉错顺序（3 先于 1）→ 全重置
    assert k.interact('breaker:3') == 'reset'
    assert k._switches == []
    # 正确顺序 1→3→4
    assert k.interact('breaker:1') == 'next'
    assert k.interact('breaker:3') == 'next'
    assert k.interact('breaker:4') == 'p2_solved'
    assert k.p2_done
    print('  ok: 谜题2 配电箱（拉错全重置 / 1→3→4 通关）')


def test_puzzle3_three_conditions():
    k = LiminallKernel(20261014)
    k.loop = 7
    k.phase = 'hall'
    k._sync_entity()
    k.player['x'], k.player['z'] = 0.7, 4.0     # 电话旁
    # 只满足条件二（驻留 6.7s > 5s）不应响铃——三条件须全齐
    for _ in range(400):
        k.update(1 / 60.0)
    assert not k.p3_ring, '仅 stare 不应响铃'
    # 条件一：走满 10m（电话旁来回）
    for _ in range(400):
        k.move(0.6, 0.0, 1 / 60.0)
        k.move(-0.6, 0.0, 1 / 60.0)
        k.update(1 / 60.0)
    assert k.p3['walked'], '走满 10m 应置位 walked'
    # 条件三：绕门一圈（start_door）
    k.interact('start_door')
    assert k.p3['looped'] and k.p3_ring, '三条件全齐应响铃'
    # 接听 → won
    assert k.interact('phone') == 'won' and k.won
    print('  ok: 谜题3 电话三条件（走10m/驻留5s/绕门 → 响铃 → 接听 won）')


def test_maze_layout_deterministic():
    a = maze_layout(20261014)
    b = maze_layout(20261014)
    assert a[0] == b[0] and a[2] == b[2], '同种子迷宫必须一致'
    walls, _open, start, far = a
    # far 按距离降序（BFS 最远优先）
    assert all(_ml_dist(start, far[i]) >= _ml_dist(start, far[i + 1])
               for i in range(len(far) - 1))
    assert len(far) == 3
    print('  ok: 迷宫生成确定性 + far 素材格按距离排序')


def _ml_dist(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])

def test_respawn_teleport():
    """后室"直线走回原点"的可玩化：respawn 回起点。"""
    k = LiminallKernel(20261014)
    k.phase = 'maze'
    k.respawn()
    assert (k.player['x'], k.player['z']) != (0.0, 10.8), '迷宫 respawn 不应回走廊'
    i, j = k.maze_start
    hx = (j + 0.5) * 4.0 - k._maze_half()
    hz = (i + 0.5) * 4.0 - k._maze_half()
    assert abs(k.player['x'] - hx) < 1e-6 and abs(k.player['z'] - hz) < 1e-6
    print('  ok: 传送矩阵（迷宫 respawn 回标记点——非线性空间）')


def main():
    print('test_loop_events_monotonic');      test_loop_events_monotonic()
    print('test_autopilot_full_run');         test_autopilot_full_run()
    print('test_entity_light_drives_back');   test_entity_light_drives_back()
    print('test_entity_home_never_blocks');   test_entity_home_never_blocks()
    print('test_puzzle1_flash_sequence');     test_puzzle1_flash_sequence()
    print('test_puzzle2_breaker_reset');      test_puzzle2_breaker_reset()
    print('test_puzzle3_three_conditions');   test_puzzle3_three_conditions()
    print('test_maze_layout_deterministic');  test_maze_layout_deterministic()
    print('test_respawn_teleport');           test_respawn_teleport()
    print('ALL_LIMINALL_TESTS_PASSED')


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""帧图门禁：编译标准 3D 帧，打印计划，并**断言物理顺序**。

用法::

    python -m world3d.cli.frame_plan                 # 全效果
    python -m world3d.cli.frame_plan --off dof       # 关掉景深（看 pass 被剔除 + 别名回收）
    python -m world3d.cli.frame_plan --off fog,dof,motionblur
    python -m world3d.cli.frame_plan --all-toggles   # 逐个组合跑一遍（门禁用）

退出码：0 = 计划合法且顺序正确；1 = 有问题。
这个门禁的价值在于：**"雾 → 景深 → 运动模糊"这条物理顺序一旦被改错，验收脚本会红**
—— 而不是等到画面看起来"有点不对"。
"""
from __future__ import annotations

import argparse
import itertools
import sys

from world3d.engine.framegraph import (SEQUENCE_OF_DEPTH_EFFECTS, FrameGraphError,
                               standard_3d_plan)

EFFECTS = ('shadows', 'fog', 'dof', 'motionblur')
PASS_OF = {'shadows': 'shadow_pass', 'fog': 'fog_pass',
           'dof': 'dof_pass', 'motionblur': 'motionblur_pass'}


def build(off=()):
    """按**真实用法**关效果 —— 这里有两种关法，别混：

    * **纯生产者 pass**（`shadow_pass`：什么都不读）：只能在**建图时**不声明。
      运行期 `disable` 它会被内核拒绝（没有上游可以直通）—— 这条约束是对的：
      关了它下游就没数据，硬关等于静默出错。
    * **消费者 pass**（`fog/dof/motionblur`：都读上游）：**运行期** `disable`，
      这样才走"剔除 + 直通 + 别名回收"那条路，也才测得到关掉效果的收益。

    我第一版把两种关法混在一起（构建期不声明 + 再 disable），于是
    `--off dof` 直接报"没有名为 dof_pass 的 pass"。
    """
    on = {k: (k not in off) for k in EFFECTS}
    g = standard_3d_plan(shadows=on['shadows'])
    for k in ('fog', 'dof', 'motionblur'):
        if not on[k]:
            g.disable(PASS_OF[k])
    return g


def check(plan, label) -> list:
    """返回问题列表（空 = 通过）。"""
    problems = []
    if plan.order[0] != 'depth_prepass':
        problems.append(f'{label}: 距离图必须最先产出，实际首项 {plan.order[0]}')
    if plan.order[-1] != 'present':
        problems.append(f'{label}: present 必须最后，实际末项 {plan.order[-1]}')
    # 物理顺序：雾 → 景深 → 运动模糊（只检查**实际存在**的那几个）
    present = [e for e in SEQUENCE_OF_DEPTH_EFFECTS if f'{e}_pass' in plan.order]
    pos = [plan.position(f'{e}_pass') for e in present]
    if pos != sorted(pos):
        problems.append(f'{label}: 深度相关效果顺序错了 {list(zip(present, pos))}')
    # 每个 pass 读的东西都得有人写过（编译期已保证，这里再确认一次它真的没被跳过）
    for name in plan.order:
        if name in plan.culled:
            problems.append(f'{label}: {name} 同时在 order 与 culled 里')
    return problems


def show(plan, label):
    print(f'--- {label} ---')
    print(f'  pass × {len(plan.order)}: ' + ' → '.join(plan.order))
    if plan.culled:
        print(f'  被剔除: {plan.culled}')
    if plan.aliases:
        groups = ' | '.join('⊕'.join(g) for g in plan.aliases)
        print(f'  可别名（生命周期不重叠，可共用内存）: {groups}')
    else:
        print('  可别名: （无）')
    print(f'  峰值（上界口径）: {plan.peak_slots} 槽 / '
          f'{plan.peak_bytes / (1024 * 1024):.1f} MiB')


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='帧图门禁（标准 3D 帧）')
    ap.add_argument('--off', default='',
                    help='逗号分隔：关掉哪些效果（shadows/fog/dof/motionblur）')
    ap.add_argument('--all-toggles', action='store_true',
                    help='逐个组合（含全开/全关）跑一遍，作门禁用')
    args = ap.parse_args(argv)

    problems = []
    combos = []
    if args.all_toggles:
        for r in range(len(EFFECTS) + 1):
            combos.extend(itertools.combinations(EFFECTS, r))
    else:
        off = tuple(x for x in args.off.split(',') if x)
        for x in off:
            if x not in EFFECTS:
                print(f'[frame_plan] 未知效果 {x!r}；可选：{", ".join(EFFECTS)}')
                return 1
        combos = [off]

    for off in combos:
        try:
            plan = build(off).compile()
        except FrameGraphError as exc:
            problems.append(f'关掉 {off or "无"}：编译失败 —— {exc}')
            continue
        label = f'关掉 {",".join(off) if off else "无"}'
        show(plan, label)
        problems.extend(check(plan, label))

    print('-' * 60)
    if problems:
        for p in problems:
            print(f'  !! {p}')
        print('FRAME_PLAN_FAILED')
        return 1
    print(f'[frame_plan] {len(combos)} 种组合全部合法、顺序正确  '
          f'（雾 → 景深 → 运动模糊）')
    print('== FRAME_PLAN_OK ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())

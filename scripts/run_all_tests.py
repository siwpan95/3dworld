#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""3dworld 一键回归入口 —— 一条命令跑完全部测试模块。

用法::

    python scripts/run_all_tests.py            # 跑全部
    python scripts/run_all_tests.py --quiet    # 只打印汇总
    python scripts/run_all_tests.py --list     # 只列模块，不执行

设计要点
--------
* 每个测试模块都是自研 runner（自带 ``if __name__ == '__main__'`` 顺序调用测试函数），
  末尾打印各自的 ``ALL_*_PASSED`` 标记。本脚本用**子进程**逐个跑——不用 import，
  避免某个模块崩溃拖垮其余模块（这正是此前 "全新克隆下 36 项根本没机会执行" 的成因）。
* 判定通过 = 退出码 0 **且** 出现该模块专属的 PASSED 标记。只看退出码会漏掉
  "模块级 import 失败但进程仍正常退出" 这类假绿。
* 统计用**实际执行数**为分母：跑挂的模块其名下测试记为"未执行"而非"跳过"。

上线前全检时新增（此前项目零 CI、零一键回归入口）。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# (模块, 成功标记, 说明)
MODULES = [
    ('tests.run_tests', 'ALL_LOGIC_TESTS_PASSED', '逻辑/玩法内核'),
    ('tests.test_cores', 'ALL_CORE_TESTS_PASSED', '共同核契约'),
    ('tests.test_engine', 'ALL_ENGINE_TESTS_PASSED', '引擎层'),
    ('tests.test_brickward', 'ALL_BRICKWARD_TESTS_PASSED', '砖境内核（BQM 致敬 B1/B2/B3）'),
    ('tests.test_csm', 'ALL_CSM_TESTS_PASSED', '级联阴影 CSM'),
    ('tests.test_volumetrics', 'ALL_VOLUMETRICS_TESTS_PASSED', '体积雾/光轴'),
    ('tests.test_dof', 'ALL_DOF_TESTS_PASSED', '景深 DoF'),
    ('tests.test_motionblur', 'ALL_MOTIONBLUR_TESTS_PASSED', '运动模糊'),
    ('tests.test_framegraph', 'ALL_FRAMEGRAPH_TESTS_PASSED', '帧图（编译期校验）'),
    ('tests.test_compat', 'ALL_COMPAT_TESTS_PASSED', '旧路径兼容层（P2-4）'),
    ('tests.test_gpu_cull', 'ALL_GPU_CULL_TESTS_PASSED', 'GPU 剔除'),
    ('tests.test_p2_render', 'ALL_P2_TESTS_PASSED', 'P2 渲染能力'),
    ('tests.test_skin', 'ALL_SKIN_TESTS_PASSED', '蒙皮骨骼'),
    ('tests.test_timeline_ui', 'ALL_TIMELINE_UI_TESTS_PASSED', '时间轴 UI 面板'),
    ('tests.test_liminall', 'ALL_LIMINALL_TESTS_PASSED', '阈廊内核（后室 × P.T. 循环走廊）'),
]


def _count_ok(output: str) -> int:
    """统计一个模块实际执行的断言/用例数（以 '  ok:' 行计）。"""
    return sum(1 for line in output.splitlines() if line.strip().startswith('ok:'))


def run_one(mod: str, marker: str, quiet: bool):
    """跑单个模块，返回 (是否通过, 用例数, 输出, 错误摘要)。"""
    env = dict(os.environ)
    env['PYTHONPATH'] = _ROOT + os.pathsep + env.get('PYTHONPATH', '')
    proc = subprocess.run(
        [sys.executable, '-m', mod],
        cwd=_ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='utf-8', errors='replace',
    )
    out = proc.stdout or ''
    ok = proc.returncode == 0 and marker in out
    err = ''
    if not ok:
        tail = [l for l in out.splitlines() if l.strip()][-3:]
        err = ' / '.join(tail) if tail else f'exit={proc.returncode}'
    if not quiet:
        print(out, end='' if out.endswith('\n') else '\n')
    return ok, _count_ok(out), out, err


def main() -> int:
    ap = argparse.ArgumentParser(description='3dworld 一键回归入口')
    ap.add_argument('--quiet', action='store_true', help='只打印汇总，不打各模块明细')
    ap.add_argument('--list', action='store_true', help='只列出测试模块')
    args = ap.parse_args()

    if args.list:
        for mod, _, desc in MODULES:
            print(f'{mod:<24} {desc}')
        return 0

    print(f'[run_all_tests] python = {sys.executable}')
    print(f'[run_all_tests] root   = {_ROOT}')
    print('=' * 62)

    total = 0
    passed_mods = 0
    failed = []
    for mod, marker, desc in MODULES:
        ok, n, out, err = run_one(mod, marker, args.quiet)
        total += n
        if ok:
            passed_mods += 1
            print(f'  [PASS] {mod:<20} {n:>3} 项  ({desc})')
        else:
            failed.append((mod, err))
            print(f'  [FAIL] {mod:<20} {n:>3} 项  ({desc})  {err}')

    print('=' * 62)
    print(f'[run_all_tests] 模块 {passed_mods}/{len(MODULES)} 通过，'
          f'实际执行用例 {total} 项')
    if failed:
        print(f'[run_all_tests] 失败 {len(failed)} 个模块：')
        for mod, err in failed:
            print(f'  - {mod}: {err}')
        print('ALL_TESTS_FAILED')
        return 1
    print('ALL_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

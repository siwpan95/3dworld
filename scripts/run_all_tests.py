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


def _force_utf8_stdout() -> None:
    """把本进程 stdout 钉成 UTF-8（errors='replace'）。

    不这么做会踩到一个**会吃掉结论**的坑（2026-10-14 实测）：Windows 控制台默认
    GBK，子进程输出里只要出现 GBK 编不出的字符（如 ``\\u037c``），第 78 行的
    ``print(out)`` 就抛 ``UnicodeEncodeError`` —— 进程带栈退出、**一条汇总都不打印**，
    于是"有模块跑挂"这件事被表达成"runner 崩了"，真失败被埋掉
    （当时 tests.test_compat 正因为仓根残留目录而 4/6 失败，全程无人看见）。

    ``errors='replace'`` 是有意的：宁可让个别字符显示成 ``?``，也不能让整段
    测试结论消失。判定只看退出码与模块专属 PASSED 标记，与显示编码无关。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure is not None:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except (ValueError, OSError):
                pass

# (模块, 成功标记, 说明)
MODULES = [
    ('tests.run_tests', 'ALL_LOGIC_TESTS_PASSED', '逻辑/玩法内核'),
    ('tests.test_cores', 'ALL_CORE_TESTS_PASSED', '共同核契约'),
    ('tests.test_engine', 'ALL_ENGINE_TESTS_PASSED', '引擎层'),
    ('tests.test_shaders', 'ALL_SHADERS_TESTS_PASSED', '着色器库'),
    ('tests.test_noise', 'ALL_NOISE_TESTS_PASSED', '噪声库（procgen 移植）'),
    ('tests.test_replay', 'ALL_REPLAY_TESTS_PASSED', '回放系统（roleplayer 移植）'),
    ('tests.test_behavior_tree', 'ALL_BEHAVIOR_TREE_TESTS_PASSED', '行为树（zhuangtaiji 移植）'),
    ('tests.test_dungeon', 'ALL_DUNGEON_TESTS_PASSED', '地牢/洞穴生成（procgen 移植）'),
    ('tests.test_town', 'ALL_TOWN_TESTS_PASSED', 'Voronoi 城镇生成（procgen 移植）'),
    ('tests.test_ca', 'ALL_CA_TESTS_PASSED', '元胞自动机 141 规则（cellular_automaton 转借）'),
    ('tests.test_curves', 'ALL_CURVES_TESTS_PASSED', '曲线库 bezier/catmull（自研）'),
    ('tests.test_physics3d', 'ALL_PHYSICS3D_TESTS_PASSED', '3D 物理 碰撞/扫掠/积分（自研）'),
    ('tests.test_lsystem', 'ALL_LSYSTEM_TESTS_PASSED', 'L-Systems 林氏系统（自研）'),
    ('tests.test_wfc', 'ALL_WFC_TESTS_PASSED', 'WFC 波函数坍缩（自研）'),
    ('tests.test_brickward', 'ALL_BRICKWARD_TESTS_PASSED', '砖境内核（BQM 致敬 B1/B2/B3）'),
    ('tests.test_csm', 'ALL_CSM_TESTS_PASSED', '级联阴影 CSM'),
    ('tests.test_volumetrics', 'ALL_VOLUMETRICS_TESTS_PASSED', '体积雾/光轴'),
    ('tests.test_dof', 'ALL_DOF_TESTS_PASSED', '景深 DoF'),
    ('tests.test_motionblur', 'ALL_MOTIONBLUR_TESTS_PASSED', '运动模糊'),
    ('tests.test_framegraph', 'ALL_FRAMEGRAPH_TESTS_PASSED', '帧图（编译期校验）'),
    ('tests.test_terrain_lod', 'ALL_TERRAIN_LOD_TESTS_PASSED',
     'CDLOD 地形（选层/无缝/morph/面数）'),
    ('tests.test_framegraph_v2', 'ALL_FRAMEGRAPH_V2_TESTS_PASSED',
     '帧图 v2（生命周期/别名组/槽位池/峰值）'),
    ('tests.test_compat', 'ALL_COMPAT_TESTS_PASSED', '旧路径兼容层（P2-4）'),
    ('tests.test_gpu_cull', 'ALL_GPU_CULL_TESTS_PASSED', 'GPU 剔除'),
    ('tests.test_p2_render', 'ALL_P2_TESTS_PASSED', 'P2 渲染能力'),
    ('tests.test_skin', 'ALL_SKIN_TESTS_PASSED', '蒙皮骨骼'),
    ('tests.test_timeline_ui', 'ALL_TIMELINE_UI_TESTS_PASSED', '时间轴 UI 面板'),
    ('tests.test_liminall', 'ALL_LIMINALL_TESTS_PASSED', '阈廊内核（后室 × P.T. 循环走廊）'),
    ('tests.test_texproj', 'ALL_TEXPROJ_TESTS_PASSED', 'UV 投影与贴图规则（投影/反解/图集/密度）'),
    ('tests.test_source2', 'ALL_SOURCE2_TESTS_PASSED', 'Source 2 解析（VPK / KV3 v5 / LZ4 block）'),
    ('tests.test_meshopt', 'ALL_MESHOPT_TESTS_PASSED', 'meshopt 解码（顶点/索引 codec）'),
    ('tests.test_source2_model', 'ALL_SOURCE2_MODEL_TESTS_PASSED', 'vmdl_c → MeshData 转换（合成容器）'),
    ('tests.test_heightfield', 'ALL_HEIGHTFIELD_TESTS_PASSED', 'heightfield 地形生成器（短剧《最后一百公里》补件）'),
    ('tests.test_sandward', 'ALL_SANDWARD_TESTS_PASSED', '沙垒：场地/巡游/碰撞/UV 密度（纯数学）'),
    ('tests.test_proctex', 'ALL_PROCTEX_TESTS_PASSED', '程序化纹理 × PBR 图集（通道打包逐像素）'),
    ('tests.test_voxel_mesh', 'ALL_VOXEL_MESH_TESTS_PASSED',
     '体素网格化 v2（三轴贪心合并 / 无内部面 / 逐顶点 AO / 对角翻转）'),
    ('tests.test_binary_greedy', 'ALL_BINARY_GREEDY_TESTS_PASSED',
     '体素二进制贪心网格化（uint64 位掩码 + quadtree 全覆盖合并 / 精确覆盖）'),
    ('tests.test_terrain_clipmap', 'ALL_TERRAIN_CLIPMAP_TESTS_PASSED',
     '几何 clipmap（嵌套 ring 网格 / 刚性吸附 / 顶点位移，纯数学）'),
    ('tests.test_jolt_lite', 'ALL_JOLT_LITE_TESTS_PASSED',
     '刚体 GJK-EPA 穿透深度（Jolt 思路子集，纯数学）'),
    ('tests.test_box2d_lite', 'ALL_BOX2D_LITE_TESTS_PASSED',
     '2D 序列冲量 solver + OBB 裁剪（Box2D-lite 思路，零 GL）'),
    ('tests.test_ozz_lite', 'ALL_OZZ_LITE_TESTS_PASSED',
     '骨架 FK / 采样 / 混合 / 分层 / 加法 / root motion（ozz 思路）'),
    ('tests.test_matcache_lite', 'ALL_MATCACHE_LITE_TESTS_PASSED',
     '材质变体键 → 产物 → 磁盘缓存（Filament matc 思路，零 GLSL）'),
    ('tests.test_gltf_lite', 'ALL_GLTF_LITE_TESTS_PASSED',
     'glTF 2.0 数据模型 / 节点 FK / 扩展矩阵（Khronos 规范实现）'),
    ('tests.test_rid', 'ALL_RID_TESTS_PASSED',
     'RID 不透明句柄表 / 代际失效 / 槽位复用（Godot RID 思路，零 GL）'),
    ('tests.test_reflect', 'ALL_REFLECT_TESTS_PASSED',
     '反射注册表一物多用：序列化 / 检视 / 脚本绑定 / 校验（ClassDB 思路）'),
    ('tests.test_solidtex', 'ALL_SOLIDTEX_TESTS_PASSED', 'solidtex'),
    ('tests.test_cave3d', 'ALL_CAVE3D_TESTS_PASSED', 'cave3d'),
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
    _force_utf8_stdout()
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

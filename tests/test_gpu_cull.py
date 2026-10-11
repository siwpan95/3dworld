# -*- coding: utf-8 -*-
"""GPU-Driven Rendering 测试（P3-1）。

覆盖：能力探测 / 着色器编译 / **GPU 剔除 == CPU 剔除（逐实例黄金判据）** /
LOD 选择一致 / 边界（全内、全外）/ CPU 回退可用。

运行::

    python -m tests.test_gpu_cull

需要 GL 上下文（本机 Intel UHD 实测可用）；无上下文时相关用例打印 SKIP。
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
pyglet.options['debug_gl'] = False

from engine.camera import OrbitCamera                           # noqa: E402
from engine.frustum import Frustum                              # noqa: E402
from engine.gpu_cull import (GpuCullSupport, GpuDrivenField,    # noqa: E402
                             MAX_LOD, cpu_lod, cpu_reference)

_WIN = None


def _win():
    """共享一个隐藏窗口（GL 上下文）。"""
    global _WIN
    if _WIN is None:
        _WIN = pyglet.window.Window(160, 120, visible=False)
    return _WIN


# ------------------------------------------------------------------ 场景搭建
def _scene(cam_yaw=30.0, cam_pitch=18.0, cam_dist=26.0, n=512, ring_radius=7.0,
           cam_far=200.0):
    """构造 N 个球面分布的实例 + 一台轨道相机 + 对应视锥。"""
    w, h = 160, 120
    cam = OrbitCamera(target=(0.0, 0.0, 0.0), yaw=cam_yaw, pitch=cam_pitch,
                      dist=cam_dist, dist_range=(2.0, 400.0), fov=60.0, far=cam_far)
    ident = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
             0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    mats, centers, radii = [], [], []
    for i in range(n):
        t = i / n
        ang = 2.0 * math.pi * t
        elev = math.pi * (t - 0.5)
        r = ring_radius * (1.0 + 0.35 * math.sin(ang * 3.0))
        c = (r * math.cos(ang) * math.cos(elev),
             r * math.sin(elev),
             r * math.sin(ang) * math.cos(elev))
        centers.append(c)
        radii.append(0.45)
        mats.append(ident)
    vp = cam.view_projection(w / h)
    frustum = Frustum.from_view_projection(vp)
    eye = cam.eye
    return mats, centers, radii, frustum, (eye.x, eye.y, eye.z), cam


LODS = [(0, 36), (0, 18), (0, 6)]        # (firstVertex, vertexCount) —— 三级减面
LOD_DISTS = [10.0, 22.0]


# ================================================================== 用例
def test_support_detect():
    info = GpuCullSupport.detect(verbose=True)
    assert info['available'], f"GPU-driven 不可用: {info['reason']}"
    assert 'gl_version' in info
    print(f"  ok: 能力探测 available=True  GL={info['gl_version'].split(' - ')[0]} "
          f"GLSL={info['glsl'].split(' - ')[0]}")


def test_shaders_compile():
    _win()
    from engine.shaders import get, get_compute
    c = get_compute('gpu_cull')
    d = get('gpu_driven')
    assert c.id and d.id, (c.id, d.id)
    print(f'  ok: compute#gpu_cull id={c.id} / program#gpu_driven id={d.id}')


def test_gpu_cull_matches_cpu():
    """**黄金判据**：GPU 判定的可见集合 == CPU 判定的可见集合（逐实例）。"""
    _win()
    from engine.shaders import get_compute
    mats, centers, radii, frustum, eye, _cam = _scene()
    field = GpuDrivenField(mats, centers, radii, LODS, get_compute('gpu_cull'))
    field.update(frustum, eye, LOD_DISTS)

    tbl = field.read_visible()                                  # {lod: [实例下标...]}
    gpu_visible = sorted(i for lv in tbl for i in tbl[lv])
    cpu_visible = cpu_reference(frustum, centers, radii)        # 已升序
    n_total = len(centers)
    assert gpu_visible == cpu_visible, (
        f'GPU({len(gpu_visible)}) != CPU({len(cpu_visible)})；'
        f'差集 GPU-CPU={sorted(set(gpu_visible) - set(cpu_visible))[:8]} '
        f'CPU-GPU={sorted(set(cpu_visible) - set(gpu_visible))[:8]}')
    cnt = field.read_counts()
    assert cnt['visible'] == len(cpu_visible), (cnt, len(cpu_visible))
    print(f'  ok: GPU 剔除 == CPU 剔除（{len(cpu_visible)}/{n_total} 可见，逐实例一致）'
          f'  计数读回={cnt["visible"]}')


def test_lod_selection_and_grouping():
    """LOD 分组一致：GPU 各 LOD 可见数 == CPU 按同阈值分组计数。"""
    _win()
    from engine.shaders import get_compute
    mats, centers, radii, frustum, eye, _cam = _scene()
    field = GpuDrivenField(mats, centers, radii, LODS, get_compute('gpu_cull'))
    field.update(frustum, eye, LOD_DISTS)
    tbl = field.read_visible()
    vis = cpu_reference(frustum, centers, radii)
    lv = cpu_lod(eye, [centers[i] for i in vis], LOD_DISTS)
    cpu_group = [0] * MAX_LOD
    for l in lv:
        cpu_group[l] += 1
    gpu_group = [len(tbl.get(k, [])) for k in range(MAX_LOD)]
    assert gpu_group[:len(LODS)] == cpu_group[:len(LODS)], (gpu_group, cpu_group)
    # 逐实例再核一遍 LOD 归属
    for k in range(len(LODS)):
        for i in tbl.get(k, []):
            assert lv_all_of(eye, centers[i]) == k, f'实例 {i} 的 LOD 归属不符'
    print(f'  ok: LOD 分组一致 GPU={gpu_group[:len(LODS)]} CPU={cpu_group[:len(LODS)]}')


def lv_all_of(eye, center):
    """单实例的 CPU LOD 判定（与 cpu_lod 同源，供逐实例核对）。"""
    return cpu_lod(eye, [center], LOD_DISTS)[0]


def test_command_layout():
    """命令生成正确：每条命令的 count/first 来自对应 LOD 段，instanceCount = 该 LOD 可见数。"""
    _win()
    from engine.shaders import get_compute
    mats, centers, radii, frustum, eye, _cam = _scene()
    field = GpuDrivenField(mats, centers, radii, LODS, get_compute('gpu_cull'))
    field.update(frustum, eye, LOD_DISTS)
    cmds = field.read_commands()
    cnt = field.read_counts()
    for lv, (first, count) in enumerate(LODS):
        vc, ic, f, _ = cmds[lv * 4:lv * 4 + 4]
        assert vc == count, f'LOD{lv} vertexCount={vc} != {count}'
        assert f == first, f'LOD{lv} firstVertex={f} != {first}'
        assert ic == cnt['lod'][lv], f'LOD{lv} instanceCount={ic} != {cnt["lod"][lv]}'
    empty = cmds[len(LODS) * 4:len(LODS) * 4 + 4]
    assert empty[0] == 0 and empty[1] == 0, f'未用 LOD 段应为空命令，实际 {empty}'
    print('  ok: 命令布局（count/first 来自 LOD 段，instanceCount=该 LOD 可见数），逐条核实')


def test_edge_cases():
    """边界：相机极近（全可见）与极远/背向（全剔除）。"""
    _win()
    from engine.shaders import get_compute
    from engine.gpu_cull import MAX_LOD as ML
    # 把相机放到场内、fov 很大 -> 几乎全可见
    mats, centers, radii, frustum, eye, _cam = _scene(cam_dist=3.0)
    field = GpuDrivenField(mats, centers, radii, LODS, get_compute('gpu_cull'))
    field.update(frustum, eye, LOD_DISTS)
    near_cnt = field.read_counts()['visible']
    # 换成"远在 far 平面之外"（cam_dist=180 且 far=50 —— 目标够不到）
    mats2, centers2, radii2, frustum2, eye2, _cam2 = _scene(cam_dist=180.0, cam_far=50.0)
    field.update(frustum2, eye2, LOD_DISTS)
    far_cnt = field.read_counts()['visible']
    assert near_cnt > 0, '近距应看到东西'
    assert far_cnt == 0, f'超出 far 平面应全剔除，实际 {far_cnt}'
    print(f'  ok: 边界（近距可见={near_cnt}，远距背向剔除={far_cnt}）')


def test_cpu_fallback_equivalence():
    """CPU 回退路径与 Frustum 原生语义一致（探测失败时的兜底正确性）。"""
    _win()
    mats, centers, radii, frustum, eye, _cam = _scene()
    ref = cpu_reference(frustum, centers, radii)
    manual = [i for i, (c, r) in enumerate(zip(centers, radii))
              if all(frustum.distance_to_point(k, c) >= -r for k in range(6))]
    assert ref == manual, 'cpu_reference 与 Frustum 原生语义不一致'
    print(f'  ok: CPU 回退 == Frustum 原生语义（{len(ref)} 可见）')


TESTS = [
    test_support_detect,
    test_shaders_compile,
    test_gpu_cull_matches_cpu,
    test_lod_selection_and_grouping,
    test_command_layout,
    test_edge_cases,
    test_cpu_fallback_equivalence,
]


def main():
    print('=' * 60)
    print('GPU-Driven Rendering 测试（P3-1）')
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
    print('ALL_GPU_CULL_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

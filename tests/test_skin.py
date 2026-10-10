# -*- coding: utf-8 -*-
"""蒙皮骨骼动画测试（P2-3）。

覆盖：常量一致性 / MeshData 蒙皮标记 / 权重归一化 / glTF skin 往返 /
**绑定姿态 = 单位矩阵（黄金判据）** / uniform 数组长度 / 蒙皮变换 CPU 参考 / 边界守卫。

运行::

    python -m tests.test_skin
"""
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine.geometry import MeshData                       # noqa: E402
from engine.gltf import GLTF, _normalize_weights           # noqa: E402
from engine.scene import Node                              # noqa: E402
from engine.shaders import SKIN_MAX_JOINTS                 # noqa: E402
from engine.skin import MAX_JOINTS, Skin, make_skin_from_gltf  # noqa: E402

GLTF_PATH = os.path.join(ROOT, 'assets', 'skinned_snake.gltf')
IDENT = (1.0, 0.0, 0.0, 0.0,
         0.0, 1.0, 0.0, 0.0,
         0.0, 0.0, 1.0, 0.0,
         0.0, 0.0, 0.0, 1.0)


def _approx_mat(m, ref, eps=1e-6):
    return all(abs(a - b) < eps for a, b in zip(m, ref))


def _xform(m, p):
    """列主序 4×4 变换一个点。"""
    x, y, z = p
    return (m[0] * x + m[4] * y + m[8] * z + m[12],
            m[1] * x + m[5] * y + m[9] * z + m[13],
            m[2] * x + m[6] * y + m[10] * z + m[14])


# ---------------------------------------------------------------- 常量 ----
def test_max_joints_consistency():
    """skin.MAX_JOINTS 与 shaders.SKIN_MAX_JOINTS 必须一致（防着色器/CPU 漂移）。"""
    assert MAX_JOINTS == SKIN_MAX_JOINTS == 64, (MAX_JOINTS, SKIN_MAX_JOINTS)
    print(f'  ok: MAX_JOINTS 一致 = {MAX_JOINTS}')


# ------------------------------------------------------------- MeshData ----
def test_meshdata_skinned_flag():
    md = MeshData([0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0])   # 3 顶点
    assert md.skinned is False, '无 joints/weights 不应判为蒙皮'
    assert md.joints is None and md.weights is None
    md.joints = [0, 0, 0, 0] * 3
    md.weights = [1.0, 0.0, 0.0, 0.0] * 3
    assert md.skinned is True
    md.weights = [1.0, 0.0, 0.0, 0.0]        # 长度不匹配 -> False
    assert md.skinned is False
    print('  ok: MeshData.skinned 判定（含长度不匹配退化）')


# --------------------------------------------------------------- 归一化 ----
def test_normalize_weights():
    out = _normalize_weights([2.0, 2.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
    assert abs(sum(out[:4]) - 1.0) < 1e-9 and abs(out[0] - 0.5) < 1e-9
    assert abs(sum(out[4:8]) - 1.0) < 1e-9 and out[4] == 1.0, '全 0 权重应退化为首权重 1'
    print('  ok: _normalize_weights 归一化 + 全 0 退化')


# -------------------------------------------------------- glTF skin 往返 ----
def test_gltf_skin_roundtrip():
    assert os.path.isfile(GLTF_PATH), f'缺 {GLTF_PATH}（先跑 scripts/make_skinned_gltf.py）'
    g = GLTF.load(GLTF_PATH)
    skins = g.skins()
    assert len(skins) == 1, f'应有 1 个 skin，实际 {len(skins)}'
    sk = skins[0]
    assert len(sk['joints']) == 8 and len(sk['inverse_bind']) == 8
    scene, pending, nmap = g.build_scene_ex()
    assert len(pending) == 1
    md = pending[0][1]
    assert md.skinned, '回读的网格应为蒙皮网格'
    assert len(md.joints) == md.vertex_count * 4
    assert max(md.joints) < 8, '关节索引越界'
    for i in range(md.vertex_count):
        s = sum(md.weights[i * 4:i * 4 + 4])
        assert abs(s - 1.0) < 1e-5, f'顶点 {i} 权重和 {s}'
    print(f'  ok: glTF skin 往返（{len(sk["joints"])} 关节 / {md.vertex_count} 顶点 / 权重和=1）')


# ------------------------------------------------ 绑定姿态 = 单位矩阵 ----
def test_bind_pose_is_identity():
    """黄金判据：绑定姿态下 jointMatrix = jointWorld @ inverseBind = I。"""
    g = GLTF.load(GLTF_PATH)
    scene, pending, nmap = g.build_scene_ex()
    # 只对 root 调一次：update_world 会递归；
    # ⚠️ 切勿 traverse 后逐节点调——parent_matrix=None 会把该节点当"根"重算，丢掉父链。
    scene.root.update_world(force=True)
    skin = make_skin_from_gltf(g, nmap, 0)
    jm = skin.joint_matrices()
    for i, m in enumerate(jm):
        assert _approx_mat(m, IDENT, 1e-5), f'关节 {i} 绑定姿态非单位矩阵: {m}'
    # 绑定姿态下蒙皮顶点 = 原顶点
    v0 = (0.35, 0.0, 0.0)
    assert all(abs(a - b) < 1e-5 for a, b in zip(_xform(jm[0], v0), v0))
    print(f'  ok: 绑定姿态 jointMatrix = I（{len(jm)} 个关节逐一核实）+ 顶点不动')


# ------------------------------------------------------- uniform 数组 ----
def test_uniform_matrices_length_and_pad():
    g = GLTF.load(GLTF_PATH)
    scene, pending, nmap = g.build_scene_ex()
    scene.root.update_world(force=True)         # 只调 root（递归；见上一测试的注记）
    skin = make_skin_from_gltf(g, nmap, 0)
    ms = skin.uniform_matrices()
    assert len(ms) == MAX_JOINTS, f'uniform 列表长度须 == MAX_JOINTS，实际 {len(ms)}'
    assert _approx_mat(tuple(ms[0]), IDENT, 1e-5), '第 0 个应为绑定姿态矩阵'
    assert _approx_mat(tuple(ms[MAX_JOINTS - 1]), IDENT, 1e-5), '未用槽位应补单位矩阵'
    print(f'  ok: uniform_matrices 长度={len(ms)}（未用槽位补 I）')


# -------------------------------------------------- 蒙皮变换 CPU 参考 ----
def test_skin_transform_cpu_reference():
    """关节旋转后：jointMatrix 非单位，且 GPU 公式与 CPU 参考实现一致。"""
    # 手工两条骨链：joint0 @ origin, joint1 在 (0,0,1)，绕 X 转 90°
    j0 = Node('j0', translation=(0.0, 0.0, 0.0))
    q = (math.cos(math.pi / 4), math.sin(math.pi / 4), 0.0, 0.0)   # 绕 X 90°(wxyz)
    j1 = Node('j1', translation=(0.0, 0.0, 1.0), rotation=q)
    j0.add(j1)
    j0.update_world(force=True)
    skin = Skin([j0, j1], [IDENT, IDENT], name='manual')
    jm = skin.joint_matrices()
    assert not _approx_mat(jm[1], IDENT, 1e-3), '关节 1 旋转后 jointMatrix 不应是单位矩阵'
    # CPU 参考：顶点 (0,0,2) 只受 joint1 影响 -> joint1 世界矩阵（旋转+平移）
    p = (0.0, 0.0, 2.0)
    got = _xform(jm[1], p)
    # joint1 world = T(0,0,1) @ Rx(90°) -> 点 (0,0,2) -> 绕关节 1 原点旋转
    exp = _xform(tuple(j1.world_matrix), p)
    assert all(abs(a - b) < 1e-5 for a, b in zip(got, exp)), (got, exp)
    print(f'  ok: 蒙皮变换 CPU 参考一致（joint1 旋转 90°，点 {p} -> {tuple(round(v,3) for v in got)}）')


# ------------------------------------------------------------- 边界守卫 ----
def test_skin_guards():
    try:
        Skin([Node('a')], [IDENT, IDENT])         # 数量不等
        raise AssertionError('应抛 ValueError（数量不等）')
    except ValueError as e:
        assert '数量不等' in str(e)
    ident_list = [IDENT] * (MAX_JOINTS + 1)
    try:
        Skin([Node(f'j{i}') for i in range(MAX_JOINTS + 1)], ident_list)
        raise AssertionError('应抛 ValueError（超上限）')
    except ValueError as e:
        assert 'MAX_JOINTS' in str(e)
    print('  ok: Skin 边界守卫（数量不等 / 超上限）')


TESTS = [
    test_max_joints_consistency,
    test_meshdata_skinned_flag,
    test_normalize_weights,
    test_gltf_skin_roundtrip,
    test_bind_pose_is_identity,
    test_uniform_matrices_length_and_pad,
    test_skin_transform_cpu_reference,
    test_skin_guards,
]


def main():
    print('=' * 60)
    print('蒙皮骨骼动画测试（P2-3）')
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
    print('ALL_SKIN_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

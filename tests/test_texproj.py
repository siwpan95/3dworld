# -*- coding: utf-8 -*-
"""UV 投影与贴图规则测试套件（纯数学，零 GL、零窗口）。

运行::

    python -m tests.test_texproj

**钉死的性质**（断言数值，不是"看起来对"）：

1. **轴向基右手系**——``cross(e1, e2) == 轴向量``，主导轴选择与符号无关；
2. **正反两向互逆**——``uv_to_world(project_uv(p), proj, p·d) == p``（恒等）；
3. **texinfo 同构**——``gradients()`` 与投影输出逐分量一致（tv 向量）；
4. **拟合可反解**——合成数据下 scale/rotation/offset 精确复原；
5. **拒绝不合法数据**——剪切/斜投影不被硬塞成轴向投影（给原因）；
6. **顶点拆分**——盒子一个角属于 3 张不同面 → 拆出 3 份（8 角 → 24 顶点）；
7. **贴图规则**——wrap 三模式、图集格内平铺、texel 密度换算、密度体检离群。
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.geometry import MeshData  # noqa: E402
from world3d.engine.texproj import (  # noqa: E402
    Atlas, TexWrap, UvProjection, axis_vector, density_report, dominant_axis,
    face_basis, fit_projection, project_uv, texel_scale, uv_for_faces,
    uv_to_world, wrap_uv,
)

EPS = 1e-9


def _x3(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def test_axis_basis_and_dominant():
    for ax in ('x', 'y', 'z'):
        e1, e2 = face_basis(ax)
        n = _x3(e1, e2)
        d = axis_vector(ax)
        assert max(abs(n[i] - d[i]) for i in range(3)) < 1e-15, \
            f'{ax} 轴向基必须右手系（cross(e1,e2)==d）'
    assert dominant_axis((0.1, -0.9, 0.2)) == 'y', '负分量同样按 |·| 比较'
    assert dominant_axis((-3, 1, 2)) == 'x'
    assert dominant_axis((0.0, 0.0, 0.0)) == 'z', '退化法线回退 z'
    print('  ok: 轴向基右手系 + 主导轴（含符号无关/退化回退）')


def test_projection_identity_rotation_tile():
    ident = UvProjection('z')
    assert project_uv((3.0, 4.0, 5.0), ident) == (3.0, 4.0)
    rot90 = UvProjection('z', rotation=math.pi / 2)
    u, v = project_uv((1.0, 0.0, 0.0), rot90)
    assert abs(u - 0.0) < EPS and abs(v + 1.0) < EPS, '绕轴 90° 应把 +X 映到 -V'
    tile = UvProjection.from_tile(200.0)
    assert abs(tile.scale[0] - 0.005) < 1e-15
    u, v = project_uv((200.0, 100.0, 0.0), tile)
    assert abs(u - 1.0) < EPS and abs(v - 0.5) < EPS, '200 世界单位/贴图'
    xt = UvProjection.from_tile(2.0, axis='x')
    u, v = project_uv((0.0, 1.0, 0.0), xt)
    assert abs(u - 0.5) < EPS and abs(v - 0.0) < EPS, 'x 轴投影：u 沿 Y'
    print('  ok: 恒等/旋转 90°/from_tile 密度换算')


def test_uv_to_world_roundtrip():
    rng = random.Random(42)
    cases = [UvProjection('z', (0.005, 0.005), 0.0, (0.0, 0.0)),
             UvProjection('x', (1.7, 0.3), 0.9, (12.0, -4.0)),
             UvProjection('y', (0.02, -0.02), -2.1, (0.5, 0.5))]
    worst = 0.0
    for proj in cases:
        d = axis_vector(proj.axis)
        for _ in range(50):
            p = (rng.uniform(-500, 500), rng.uniform(-500, 500), rng.uniform(-200, 200))
            uv = project_uv(p, proj)
            plane = p[0] * d[0] + p[1] * d[1] + p[2] * d[2]
            q = uv_to_world(uv, proj, plane)
            worst = max(worst, max(abs(q[i] - p[i]) for i in range(3)))
    assert worst < 1e-9, f'UV→世界 往返误差 {worst:.3e}'
    # plane 参数：投影轴坐标被固定
    q = uv_to_world(project_uv((7.0, 8.0, 9.0), cases[0]), cases[0], 0.0)
    assert abs(q[2]) < EPS, 'plane=0 应落回投影面'
    print(f'  ok: uv_to_world 往返 150 组（最大误差 {worst:.2e}）')


def test_gradients_texinfo_equivalence():
    proj = UvProjection('y', (2.0, -0.75), 0.4, (3.0, -2.0))
    gu, gv = proj.gradients()
    u_dir, v_dir = proj.directions()
    for i in range(3):     # 梯度 = 方向 × scale（texinfo 的 tv 向量构造）
        assert abs(gu[i] - u_dir[i] * proj.scale[0]) < EPS
        assert abs(gv[i] - v_dir[i] * proj.scale[1]) < EPS
    rng = random.Random(7)
    for _ in range(20):
        p = (rng.uniform(-10, 10), rng.uniform(-10, 10), rng.uniform(-10, 10))
        u_expect = gu[0] * p[0] + gu[1] * p[1] + gu[2] * p[2] + 3.0
        v_expect = gv[0] * p[0] + gv[1] * p[1] + gv[2] * p[2] - 2.0
        u, v = project_uv(p, proj)
        assert abs(u - u_expect) < EPS and abs(v - v_expect) < EPS, 'tv 向量口径不一致'
    print('  ok: gradients() == texinfo tv0/tv1（逐分量一致）')


def test_fit_recovers_projection():
    rng = random.Random(20261014)
    rot0, off0 = 0.35, (2.5, -1.25)
    scale0 = (1.0 / 180.0, 1.0 / 160.0)
    src = UvProjection('z', scale0, rot0, off0)
    pts, uvs = [], []
    for _ in range(300):
        p = (rng.uniform(-300, 300), rng.uniform(-200, 200), 5.0)
        pts.append(p)
        uvs.append(project_uv(p, src))
    res = fit_projection(pts, uvs)
    assert res.mode == 'planar', f'共面点集应走平面拟合，实际 {res.mode}'
    assert res.ok, f'应能收敛出投影：{res.reason}'
    assert res.rms_u < 1e-9 and res.rms_v < 1e-9, \
        f'无噪声数据残差应≈0（{res.rms_u:.2e}, {res.rms_v:.2e}）'
    proj = res.projection
    assert proj.axis == 'z'
    assert abs(abs(proj.scale[0]) - scale0[0]) < 1e-9, proj.scale
    assert abs(abs(proj.scale[1]) - scale0[1]) < 1e-9, proj.scale
    assert abs(math.cos(proj.rotation) - math.cos(rot0)) < 1e-9
    assert abs(math.sin(proj.rotation) - math.sin(rot0)) < 1e-9
    assert abs(proj.offset[0] - off0[0]) < 1e-9 and abs(proj.offset[1] - off0[1]) < 1e-9
    assert res.axis_align > 0.9999 and res.ortho < 1e-6
    print(f'  ok: 反解 scale/rotation/offset（rms {res.rms_u:.1e}，align {res.axis_align:.4f}）')


def test_fit_noise_and_rejection():
    rng = random.Random(3)
    src = UvProjection('z', (0.005, 0.005), 0.0, (0.0, 0.0))
    pts, uvs = [], []
    for _ in range(200):
        p = (rng.uniform(-400, 400), rng.uniform(-400, 400), 0.0)
        pts.append(p)
        u, v = project_uv(p, src)
        uvs.append((u + rng.uniform(-1e-3, 1e-3), v + rng.uniform(-1e-3, 1e-3)))
    res = fit_projection(pts, uvs)
    assert res.ok, f'噪声 1e-3 下仍应收敛：{res.reason}'
    assert res.rms_u < 5e-3 and res.rms_v < 5e-3, (res.rms_u, res.rms_v)

    # 剪切数据（u 混入 y）——不该被硬塞成轴向投影
    shear = []
    for p in pts:
        shear.append((p[0] / 180.0 + 0.3 * p[1] / 180.0 + 2.5, p[1] / 160.0))
    res2 = fit_projection(pts, shear)
    assert res2.projection is None and res2.reason, '剪切必须被拒'
    reason_shear = res2.reason

    # 斜投影（体积点集上 ∇v 带 z 分量、且与 ∇u 夹角偏离 90°）——被拒
    vol = [(rng.uniform(-100, 100), rng.uniform(-100, 100), rng.uniform(-100, 100))
           for _ in range(200)]
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    tilt = []
    for p in vol:
        tilt.append((p[0] / 180.0 + 2.5, (p[1] * c + p[2] * s) / 160.0 - 1.0))
    res3 = fit_projection(vol, tilt)
    assert res3.mode == 'volume', f'体积点集应走 3D 拟合，实际 {res3.mode}'
    assert res3.projection is None and res3.reason, '斜投影必须被拒'
    print(f'  ok: 噪声可收敛（rms {res.rms_u:.1e}）；剪切被拒（{reason_shear[:18]}…）；'
          f'斜投影被拒（{res3.reason[:14]}…）')


def _cube8():
    """8 共享顶点盒子（2×1×3）+ 12 三角形（用于验证顶点拆分）。"""
    pos = [0, 0, 0, 2, 0, 0, 2, 1, 0, 0, 1, 0,
           0, 0, 3, 2, 0, 3, 2, 1, 3, 0, 1, 3]
    idx = [0, 3, 2, 0, 2, 1,      # 底 z=0
           4, 5, 6, 4, 6, 7,      # 顶 z=3
           0, 1, 5, 0, 5, 4,      # 前 y=0
           3, 7, 6, 3, 6, 2,      # 后 y=1
           0, 4, 7, 0, 7, 3,      # 左 x=0
           1, 2, 6, 1, 6, 5]      # 右 x=2
    return MeshData(pos, None, None, None, idx, None, 'cube8')


def test_uv_for_faces_cube_split():
    mesh = _cube8()
    rules = {ax: UvProjection.from_tile(2.0, axis=ax) for ax in 'xyz'}
    out = uv_for_faces(mesh, rules)
    assert out.vertex_count == 24, f'8 角 × 3 轴 = 24 顶点，实际 {out.vertex_count}'
    assert len(out.indices) == 36 and len(out.uvs) == 48
    # 每个三角形三顶点同面；逐一验证 UV 与规则一致
    pos, uvs = out.positions, out.uvs
    checked = 0
    for t in range(0, len(out.indices) - 2, 3):
        i0, i1, i2 = out.indices[t:t + 3]
        zs = {pos[i0 * 3 + 2], pos[i1 * 3 + 2], pos[i2 * 3 + 2]}
        xs = {pos[i0 * 3], pos[i1 * 3], pos[i2 * 3]}
        ys = {pos[i0 * 3 + 1], pos[i1 * 3 + 1], pos[i2 * 3 + 1]}
        for i in (i0, i1, i2):
            x, y, z = pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]
            if len(zs) == 1:                        # ±z 面：u=x/2, v=y/2
                eu, ev = x / 2.0, y / 2.0
            elif len(ys) == 1:                      # ±y 面：u=z/2, v=x/2
                eu, ev = z / 2.0, x / 2.0
            else:                                   # ±x 面：u=y/2, v=z/2
                eu, ev = y / 2.0, z / 2.0
            assert abs(uvs[i * 2] - eu) < EPS and abs(uvs[i * 2 + 1] - ev) < EPS, \
                f'三角形 {t // 3} 顶点 {i} UV 不符：({uvs[i * 2]:.3f},{uvs[i * 2 + 1]:.3f})'
            checked += 1
    assert checked == 36
    print(f'  ok: 顶点拆分（8→{out.vertex_count}）且 12 面 UV 全部符合投影规则')


def test_uv_for_faces_no_split_and_callable():
    mesh = _cube8()
    rules = {ax: UvProjection.from_tile(2.0, axis=ax) for ax in 'xyz'}
    flat = uv_for_faces(mesh, rules, split=False)
    assert flat.vertex_count == 8 and len(flat.uvs) == 16, '不拆分应保持顶点数'
    assert len(flat.indices) == 36

    callable_rules = lambda n: rules[dominant_axis(n)]  # noqa: E731
    out2 = uv_for_faces(mesh, callable_rules)
    out1 = uv_for_faces(mesh, rules)
    assert out2.uvs == out1.uvs and out2.positions == out1.positions, \
        'callable 与 dict 分派必须同源同值'
    print('  ok: 不拆分模式（共享顶点）+ callable 分派与 dict 等价')


def test_wrap_modes():
    assert abs(wrap_uv(1.25) - 0.25) < EPS
    assert abs(wrap_uv(-0.25, TexWrap.REPEAT) - 0.75) < EPS
    assert wrap_uv(-1.0, TexWrap.CLAMP) == 0.0 and wrap_uv(2.0, TexWrap.CLAMP) == 1.0
    assert abs(wrap_uv(0.4, TexWrap.CLAMP) - 0.4) < EPS
    assert abs(wrap_uv(1.25, TexWrap.MIRROR) - 0.75) < EPS
    assert abs(wrap_uv(1.75, TexWrap.MIRROR) - 0.25) < EPS
    assert abs(wrap_uv(-0.25, TexWrap.MIRROR) - 0.25) < EPS
    assert TexWrap.gl_name(TexWrap.REPEAT) == 'GL_REPEAT'
    assert TexWrap.gl_name(TexWrap.MIRROR) == 'GL_MIRRORED_REPEAT'
    print('  ok: wrap 三模式（repeat/clamp/mirror 边界值）+ GL 名映射')


def test_atlas_grid_and_remap():
    atlas = Atlas(['sand', 'brick', 'wood', 'metal'], cols=2, rows=2)
    assert atlas.rect('sand') == (0.0, 0.5, 0.5, 1.0), atlas.rect('sand')
    assert atlas.rect('metal') == (0.5, 0.0, 1.0, 0.5), atlas.rect('metal')
    u, v = atlas.remap(0.5, 0.5, 'sand')
    assert abs(u - 0.25) < EPS and abs(v - 0.75) < EPS, '格中心'
    u2, v2 = atlas.remap(1.5, 2.5, 'sand')
    assert abs(u2 - u) < EPS and abs(v2 - v) < EPS, '大 UV 先取小数（格内平铺）'
    u3, v3 = atlas.remap(0.0, 0.0, 'sand', padding=0.1)
    assert abs(u3 - 0.05) < EPS and abs(v3 - 0.55) < EPS, 'padding 内缩'
    print('  ok: 图集网格排布 + 格内平铺 + padding 内缩')


def test_texel_scale_relationship():
    s = texel_scale(256.0, 512.0)
    assert abs(s - 0.5) < 1e-15
    assert abs(s * 512.0 - 256.0) < 1e-12, 'scale × 贴图像素 = 像素密度'
    proj = UvProjection('z', (s, s))
    u, _ = project_uv((2.0, 0.0, 0.0), proj)
    assert abs(u - 1.0) < EPS, '256px/世界单位 × 2 世界单位 = 一整张 512 贴图'
    print('  ok: texel_scale 换算（像素密度 ↔ UV 密度）')


def test_density_report_outlier():
    pos = [0, 0, 0, 200, 0, 0, 0, 200, 0, 200, 200, 0]
    idx = [0, 1, 2, 1, 3, 2]
    uvs = [0, 0, 1, 0, 0, 1, 1, 1]          # = 世界坐标 / 200
    mesh = MeshData(pos, None, uvs, None, idx, None, 'grid')
    rep = density_report(mesh)
    assert rep.valid == 2 and rep.skipped == 0, (rep.valid, rep.skipped)
    assert abs(rep.median - 200.0) < 1e-6, f'密度应为 200，实际 {rep.median}'
    assert rep.outliers == [], '规则网格不应有离群'
    # 追加一个 UV 缩了 10 倍的坏面（world/uv = 20）
    pos2 = pos + [400, 0, 0, 600, 0, 0, 400, 200, 0]
    idx2 = idx + [4, 5, 6]
    uvs2 = uvs + [20, 0, 30, 0, 20, 10]     # = 世界坐标 / 20
    mesh2 = MeshData(pos2, None, uvs2, None, idx2, None, 'grid+bad')
    rep2 = density_report(mesh2)
    assert rep2.valid == 3
    assert rep2.outliers == [2], f'坏面应被抓出，实际 {rep2.outliers}'
    assert abs(rep2.median - 200.0) < 1e-6
    print(f'  ok: 密度体检（median={rep2.median:.0f}，离群 {len(rep2.outliers)} 面被抓出）')


def main():
    print('test_axis_basis_and_dominant');            test_axis_basis_and_dominant()
    print('test_projection_identity_rotation_tile');  test_projection_identity_rotation_tile()
    print('test_uv_to_world_roundtrip');              test_uv_to_world_roundtrip()
    print('test_gradients_texinfo_equivalence');      test_gradients_texinfo_equivalence()
    print('test_fit_recovers_projection');            test_fit_recovers_projection()
    print('test_fit_noise_and_rejection');            test_fit_noise_and_rejection()
    print('test_uv_for_faces_cube_split');            test_uv_for_faces_cube_split()
    print('test_uv_for_faces_no_split_and_callable'); test_uv_for_faces_no_split_and_callable()
    print('test_wrap_modes');                         test_wrap_modes()
    print('test_atlas_grid_and_remap');               test_atlas_grid_and_remap()
    print('test_texel_scale_relationship');           test_texel_scale_relationship()
    print('test_density_report_outlier');             test_density_report_outlier()
    print('ALL_TEXPROJ_TESTS_PASSED')


if __name__ == '__main__':
    main()

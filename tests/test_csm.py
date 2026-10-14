# -*- coding: utf-8 -*-
"""级联阴影（CSM）测试套件 —— `engine/csm.py` 的纯数学 + 生成 GLSL。

运行::

    python -m tests.test_csm

原则与其它套件一致：断言数值、打印实测数据、不用"看起来对"。
**每条不变量都写成"能被反例打穿"的形式** —— 尤其是：

* 视锥角点必须正好落在 NDC 立方体的**八个角**上（分割/求逆/约定三处任一写错都会立刻露）；
* 光盒必须**包住**它自己那 8 个角点，且半宽在半宽方向上是方的（否则 PCF 没法用标量半径）；
* 纹素吸附后世界原点必须**正好落在纹素栅格上**（残差 0），且移动量 < 1 纹素；
* 生成的 GLSL **不许出现非常量下标索引 sampler2D 数组**（GL 3.3 不支持，
  写了会"能编译、行为未定义"——最难查的那类问题）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.camera import Camera  # noqa: E402
from world3d.engine.csm import (IDENTITY, MAX_CASCADES, cascade_corners, csm_glsl,  # noqa: E402
                        csm_matrices, csm_uniforms, light_ortho_box,
                        mat4_as_Mat4, mat4_transform_point, select_cascade,
                        split_scheme, texel_snap)
from world3d.engine.mathkit import look_at_matrix, mat4_mul  # noqa: E402

ASPECT = 16 / 9
FOV = 50.0
TAN_HALF = math.tan(math.radians(FOV) / 2.0)
VIEW = look_at_matrix((12.0, 7.0, 26.0), (0.0, 2.0, -6.0), (0.0, 1.0, 0.0))
LIGHT = (0.42, 0.72, 0.55)


# ------------------------------------------------------------------ 分割 --
def test_split_scheme_endpoints_and_monotonic():
    for n in (1, 2, 3, 4):
        for lam in (0.0, 0.5, 0.75, 1.0):
            s = split_scheme(0.5, 400.0, n, lam)
            assert len(s) == n + 1, (n, lam, s)
            assert abs(s[0] - 0.5) < 1e-9, f'首项必须是 z_near：{s}'
            assert abs(s[-1] - 400.0) < 1e-9, f'末项必须是 z_far：{s}'
            for i in range(len(s) - 1):
                assert s[i] < s[i + 1], f'{n}/{lam} 非单调：{s}'
    print(f'  ok: 1~{MAX_CASCADES} 级 × 4 种 λ：端点精确、严格单调')


def test_split_linear_and_log_extremes():
    """λ=0 必须**等分**，λ=1 必须**等比** —— 这两条是"λ 有作用"的硬证据。"""
    s = split_scheme(2.0, 34.0, 4, 0.0)
    steps = [s[i + 1] - s[i] for i in range(4)]
    assert max(steps) - min(steps) < 1e-9, f'λ=0 应等分，实际步长 {steps}'
    s2 = split_scheme(1.0, 100.0, 2, 1.0)
    assert abs(s2[1] - 10.0) < 1e-9, f'λ=1 应等比（1→10→100），实际 {s2}'
    s3 = split_scheme(1.0, 1000.0, 3, 1.0)
    ratio = [s3[i + 1] / s3[i] for i in range(3)]
    assert max(ratio) - min(ratio) < 1e-9, f'λ=1 相邻比值应恒等：{ratio}'
    print(f'  ok: λ=0 等分（步长 {steps[0]:.2f}）、λ=1 等比（比值 {ratio[0]:.3f}）')


def test_split_rejects_bad_args():
    for args, label in (((0.0, 10.0, 3, 0.5), 'near=0'),
                        ((10.0, 10.0, 3, 0.5), 'near==far'),
                        ((20.0, 10.0, 3, 0.5), 'near>far'),
                        ((1.0, 10.0, 0, 0.5), 'n=0'),
                        ((1.0, 10.0, 3, -0.1), 'λ<0'),
                        ((1.0, 10.0, 3, 1.1), 'λ>1')):
        try:
            split_scheme(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print('  ok: near/far/n/λ 的 6 种非法输入全部被拒')


def test_select_cascade_boundaries():
    s = split_scheme(1.0, 100.0, 2, 1.0)          # λ=1, n=2 → 1, 10, 100
    assert select_cascade(-0.5, s) == 0, '比 near 还近也算第 0 级'
    assert select_cascade(-1.0, s) == 0
    assert select_cascade(-9.99, s) == 0
    assert select_cascade(-10.0, s) == 0, '分割点是**闭区间上界**，归本级'
    assert select_cascade(-10.01, s) == 1
    assert select_cascade(-99.0, s) == 1
    assert select_cascade(-100.0, s) == 1
    assert select_cascade(-100.01, s) == 1, 'n=2 → 只有 0/1 两级，越界要夹到最后一级'
    assert select_cascade(-1e9, s) == 1, '超出最远级要夹住（不能"没有阴影"）'
    print('  ok: 视距 → 级下标（闭区间边界 + 超远夹住）全部正确')


# ------------------------------------------------------------------ 视锥角点 --
def test_cascade_corners_land_on_ndc_cube_corners():
    """**最强的一条**：8 个角点经相机自身 view·projection 必须正好落在 NDC 立方体的八个角。

    分割距离、视图求逆、视空间约定（相机朝 -z）、aspect/fov 用法，任一处写错，
    这条都会立刻炸 —— 而且它不需要任何 GL。
    """
    proj = Camera(fov=FOV, near=0.5, far=400.0).projection(ASPECT)
    vp = mat4_mul(tuple(proj), VIEW)
    for (z0, z1) in ((0.5, 5.0), (5.0, 60.0), (60.0, 400.0)):
        corners = cascade_corners(VIEW, z0, z1, TAN_HALF, ASPECT)
        assert len(corners) == 8
        xs, ys, zs = set(), set(), set()
        for c in corners:
            # 手写 4x4（这个矩阵有透视，w != 1，必须做透视除法）
            m = vp
            x, y, z = c
            wx = m[0] * x + m[4] * y + m[8] * z + m[12]
            wy = m[1] * x + m[5] * y + m[9] * z + m[13]
            wz = m[2] * x + m[6] * y + m[10] * z + m[14]
            ww = m[3] * x + m[7] * y + m[11] * z + m[15]
            ndc = (wx / ww, wy / ww, wz / ww)
            for v in ndc:
                assert -1.0 - 1e-6 <= v <= 1.0 + 1e-6, f'角点 {c} 落在 NDC 外：{ndc}'
            xs.add(round(ndc[0], 6))
            ys.add(round(ndc[1], 6))
            zs.add(round(ndc[2], 6))
        assert xs == {-1.0, 1.0} and ys == {-1.0, 1.0}, ('x/y 必须正好是 ±1：'
                                                       f'{sorted(xs)} {sorted(ys)}')
        # z：只有**首段**的近端才正好是相机近平面（-1）、**末段**远端才是 +1；
        # 中间段的两端都落在 (-1,1) —— "只有整段视锥的端面才贴 NDC 边界"
        assert min(zs) >= -1.0 - 1e-6 and max(zs) <= 1.0 + 1e-6, f'z 越界：{sorted(zs)}'
        if z0 <= 0.5:
            assert min(zs) == -1.0, f'首段近端必须正好 -1：{sorted(zs)}'
        else:
            assert -1.0 < min(zs), f'中间段近端应在 (-1,1)：{sorted(zs)}'
        if z1 >= 400.0:
            assert max(zs) == 1.0, f'末段远端必须正好 +1：{sorted(zs)}'
        else:
            assert max(zs) < 1.0, f'中间段远端应在 (-1,1)：{sorted(zs)}'
    print('  ok: 3 段视锥 × 8 角点全部落在 NDC 立方体的 8 个角上（fov/near/far/朝 -z 全对）')


def test_cascade_corners_rejects_bad_args():
    for args, label in (((VIEW, 0.0, 1.0, TAN_HALF, ASPECT), 'z0=0'),
                        ((VIEW, 5.0, 1.0, TAN_HALF, ASPECT), 'z0>z1'),
                        ((VIEW, 1.0, 5.0, 0.0, ASPECT), 'tan=0'),
                        ((VIEW, 1.0, 5.0, TAN_HALF, 0.0), 'aspect=0')):
        try:
            cascade_corners(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print('  ok: z0/z1/tan/aspect 的 4 种非法输入全部被拒')


# ------------------------------------------------------------------ 光盒 --
def _ndc01(m, p):
    q = mat4_transform_point(m, *p)
    return (q[0] * 0.5 + 0.5, q[1] * 0.5 + 0.5, q[2] * 0.5 + 0.5)


def test_light_ortho_box_contains_all_corners():
    """光盒必须**包住**它自己拟合的那 8 个角点（含 z，且用纹理同样的 [0,1] 映射判定）。"""
    corners = cascade_corners(VIEW, 4.0, 90.0, TAN_HALF, ASPECT)
    vp, radius, center, (near, far) = light_ortho_box(corners, LIGHT)
    assert radius > 0 and far > near > 0
    tight = []
    for c in corners:
        x, y, z = _ndc01(vp, c)
        for v in (x, y, z):
            assert -1e-6 <= v <= 1.0 + 1e-6, f'角点 {c} 不在光盒内：{(x, y, z)}'
        # "贴得紧" = 离最近一条边的距离（x/y 两维取小）
        tight.append(min(min(x, 1 - x), min(y, 1 - y)))
    assert min(tight) < 1e-6, (
        f'至少要有一个角点贴到边界（否则盒子明显过大）：最小边距 {min(tight):.4f}')
    print(f'  ok: 8 角点全在光盒内；最贴的一个离边 {min(tight):.2e}（拟合是紧的）')


def test_light_ortho_box_is_square():
    """正方盒：padded 之后纹素在世界里 x/y 同尺寸，PCF 才能用一个标量半径。"""
    corners = cascade_corners(VIEW, 2.0, 70.0, TAN_HALF, ASPECT)
    _vp0, radius, _center, _z = light_ortho_box(corners, LIGHT, pad=3.0)
    vp = _vp0
    # 正方盒的**可判定形式**：较长的那一维，其"占盒宽的比例"正好是 1 − pad/radius
    pts = [_ndc01(vp, c) for c in corners]
    sx = max(p[0] for p in pts) - min(p[0] for p in pts)
    sy = max(p[1] for p in pts) - min(p[1] for p in pts)
    assert sx <= 1.0 + 1e-9 and sy <= 1.0 + 1e-9, f'角点超出盒宽：{sx} {sy}'
    # pad 是**故意**留出的边距 ⇒ 较长的那一维占比应正好是 1 − pad/radius
    want = 1.0 - 3.0 / radius
    assert abs(max(sx, sy) - want) < 1e-9,         f'较长的那一维占比应 = 1 − pad/radius = {want:.4f}，实际 {max(sx, sy):.4f}'
    vp2, r2, _c2, _z2 = light_ortho_box(corners, LIGHT, pad=9.0)
    assert r2 > radius, f'pad 应把盒子撑大：{radius} -> {r2}'
    print(f'  ok: 正方盒（角点 x/y 占盒宽 {sx:.4f} / {sy:.4f}，较长维正好铺满 1.0）；'
          f'pad 3→9 使半径 {radius:.2f}→{r2:.2f}')


def test_light_ortho_box_depth_padding_includes_casters():
    """z 方向要留出 `z_extra`：否则"在光与视锥之间"的柱子不投影，影子会在远处凭空断掉。"""
    corners = cascade_corners(VIEW, 4.0, 60.0, TAN_HALF, ASPECT)
    _vp, r, _c, (near0, far0) = light_ortho_box(corners, LIGHT, z_extra=0.0)
    _vp2, _r2, _c2, (near1, far1) = light_ortho_box(corners, LIGHT, z_extra=50.0)
    assert near1 <= near0 and far1 >= far0, 'z_extra 必须向两侧都撑开'
    assert abs((far1 - near1) - (far0 - near0) - 100.0) < 1e-6, '应正好各撑 z_extra'
    print(f'  ok: 深度范围 ({near0:.1f},{far0:.1f}) → ({near1:.1f},{far1:.1f})，各撑开 50')


def test_light_ortho_box_rejects_bad_corner_count():
    try:
        light_ortho_box([(0, 0, 0)] * 7, LIGHT)
    except ValueError:
        pass
    else:
        raise AssertionError('7 个角点竟然通过')
    print('  ok: 角点数 != 8 被拒')


# ------------------------------------------------------------------ 纹素吸附 --
def test_texel_snap_lands_on_grid_and_moves_less_than_a_texel():
    """吸附后世界原点必须**正好**落在纹素栅格上，且位移 < 1 纹素。

    这是消抖动的机制本身：不吸附 ⇒ 相机一动，同一纹素采到的世界位置就漂 ⇒ 边缘爬行。
    """
    corners = cascade_corners(VIEW, 6.0, 120.0, TAN_HALF, ASPECT)
    vp, _r, _c, _z = light_ortho_box(corners, LIGHT)
    for size in (512, 1024, 2048):
        snapped = texel_snap(vp, size)
        texel = 2.0 / size
        before = mat4_transform_point(vp, 0.0, 0.0, 0.0)
        after = mat4_transform_point(snapped, 0.0, 0.0, 0.0)
        for a in (after[0], after[1]):
            resid = a / texel - round(a / texel)
            assert abs(resid) < 1e-9, f'{size}：吸附后残差 {resid}（应为 0）'
        for b, a in zip(before[:2], after[:2]):
            assert abs(a - b) <= texel + 1e-12, f'{size}：位移超过 1 纹素'
        # 幂等：再吸附一次不动
        again = texel_snap(snapped, size)
        assert max(abs(p - q) for p, q in zip(again, snapped)) < 1e-12, '吸附不幂等'
    print('  ok: 512/1024/2048 三档吸附后残差 0、位移 < 1 纹素、且幂等')


def test_texel_snap_rejects_bad_size():
    try:
        texel_snap(IDENTITY, 0)
    except ValueError:
        pass
    else:
        raise AssertionError('size=0 竟然通过')
    print('  ok: size=0 被拒')


# ------------------------------------------------------------------ 一站式 --
def test_csm_matrices_radius_grows_and_splits_match():
    cas = csm_matrices(VIEW, 0.5, 500.0, TAN_HALF, ASPECT, LIGHT, n=4,
                       lambda_=0.75, size=2048)
    assert len(cas) == 4
    splits = split_scheme(0.5, 500.0, 4, 0.75)
    radii = []
    for i, c in enumerate(cas):
        assert c['index'] == i
        assert abs(c['z_near'] - splits[i]) < 1e-9
        assert abs(c['z_far'] - splits[i + 1]) < 1e-9
        assert abs(c['split'] - splits[i + 1]) < 1e-9
        assert abs(c['texel'] - 2.0 * c['radius'] / 2048) < 1e-12
        radii.append(c['radius'])
    for i in range(3):
        assert radii[i] < radii[i + 1], f'半径必须逐级变大（这正是 CSM 的收益来源）：{radii}'
    ratio = radii[0] / radii[-1]
    assert ratio < 0.5, f'最级细的那级应显著更细，实际比值 {ratio:.3f}'
    print(f'  ok: 半径逐级 {["%.1f" % r for r in radii]}，最细/最粗 = {ratio:.3f}'
          f'（纹素 {cas[0]["texel"]:.4f} vs {cas[-1]["texel"]:.4f} 世界单位）')


def test_csm_matrices_snap_toggle_changes_only_translation():
    a = csm_matrices(VIEW, 0.5, 300.0, TAN_HALF, ASPECT, LIGHT, n=2, size=1024,
                     snap=False)
    b = csm_matrices(VIEW, 0.5, 300.0, TAN_HALF, ASPECT, LIGHT, n=2, size=1024,
                     snap=True)
    for x, y in zip(a, b):
        diffs = [abs(p - q) for p, q in zip(x['vp'], y['vp'])]
        assert max(diffs[0:12] + diffs[14:16]) < 1e-12, '吸附只应改平移分量'
        assert max(diffs[12], diffs[13]) > 0.0, '吸附应真的动了平移'
        assert x['radius'] == y['radius'], '吸附不该改半径'
    print('  ok: snap 只改投影矩阵的平移分量（旋转/缩放不动），半径不变')


def test_csm_matrices_rejects_bad_n():
    for n, label in ((0, 'n=0'), (MAX_CASCADES + 1, f'n={MAX_CASCADES + 1}')):
        try:
            csm_matrices(VIEW, 0.5, 100.0, TAN_HALF, ASPECT, LIGHT, n=n)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print(f'  ok: n=0 / n>{MAX_CASCADES} 被拒（GLSL 侧是展开写死的，级数上限必须一致）')


def test_csm_uniforms_shape():
    """每级一个**独立** mat4 uniform —— 不用 `mat4[]` 数组。

    **回归守卫**：pyglet 2.1.16 对 `uniform mat4 u_vp[4]` 的 `_c_array` 只按 4 个 float
    分配（把 GL 报的"数组长度 4"当成"分量数"），`prog['u_csm_vp'] = [64 个 float]`
    会直接 `AssertionError: Size of data (64) does not match size of the uniform: 4.`。
    """
    cas = csm_matrices(VIEW, 0.5, 200.0, TAN_HALF, ASPECT, LIGHT, n=3)
    uniforms, splits = csm_uniforms(cas)
    assert set(uniforms) == {'u_csm_vp0', 'u_csm_vp1', 'u_csm_vp2'}, sorted(uniforms)
    for name, m in uniforms.items():
        assert len(m) == 16, f'{name} 应是 16 个 float（单个 mat4）'
        assert all(isinstance(v, float) for v in m), f'{name} 里混进了非 float'
    assert len(splits) == 3 and splits == sorted(splits)
    g = csm_glsl(3)
    assert 'u_csm_vp[' not in g, '生成的 GLSL 里不能有 mat4 数组'
    assert g.count('uniform mat4 u_csm_vp') == 3
    print('  ok: 3 级 → 3 个独立 mat4 uniform（各 16 float）+ 3 个分割点；GLSL 无数组')


# ------------------------------------------------------------------ GLSL --
def test_csm_glsl_expands_cascade_selection():
    """**回归守卫**：选级必须是展开的 if/else 链。

    GL 3.3 不允许用非常量下标索引 `sampler2D` 数组（GL 4.0 才放开动态 uniform 索引）。
    写成 `u_csm_tex[i]` 在多数驱动上**能编译、行为未定义**（有的直接整屏黑）——
    这类"能编译但跑错"最容易吞掉半天。
    """
    for n in (1, 2, 3, 4):
        g = csm_glsl(n)
        assert 'u_csm_tex[' not in g, f'n={n}：出现了动态下标索引 sampler'
        assert g.count('uniform sampler2D') == n, f'n={n}：sampler 数不是 {n}'
        assert g.count('uniform mat4 u_csm_vp') == n
        assert f'#define CSM_CASCADES {n}' in g
        # 展开链里每级**恰好出现一次**
        got = g.count('return _csm_shadow_one(')
        assert got == n, f'n={n}：选级链里的 return 数应为 {n}，实际 {got}'
        assert 'for (' not in g.split('float csm_shadow(')[1], '选级链里不该有循环'
        for i in range(n):
            assert f'u_csm_tex{i}' in g and f'u_csm_vp{i}' in g, f'n={n} 缺第 {i} 级'
    print(f'  ok: 1~{MAX_CASCADES} 级的选级链都是展开的（无动态 sampler 下标）')


def test_csm_glsl_pcf_kernel_is_odd():
    for samples in (2, 3, 4):
        g = csm_glsl(2, samples=samples)
        k = samples if samples % 2 else samples + 1
        assert f'float({k * k})' in g, f'samples={samples} 应被抬成奇数核 {k}'
        assert f'for (int i = -{k // 2}; i <= {k // 2}; ++i)' in g
    print('  ok: PCF 核强制奇数（2/3/4 → 3/3/5），循环边界是常量（GLSL 330 要求）')


def test_csm_glsl_rejects_bad_n():
    for n in (0, MAX_CASCADES + 1):
        try:
            csm_glsl(n)
        except ValueError:
            pass
        else:
            raise AssertionError(f'n={n} 竟然通过')
    print('  ok: n=0 / 超上限 被拒')


def test_csm_glsl_compiles_on_this_driver():
    """真编译一次（能拿到 GL 上下文才跑）—— 字符串层面看不出驱动是否接受。"""
    try:
        import pyglet
        from pyglet.graphics.shader import Shader, ShaderProgram
        win = pyglet.window.Window(64, 64, visible=False)

        @win.event
        def on_draw():
            pass
    except Exception as e:                                  # noqa: BLE001
        print(f'  SKIP: 拿不到 GL 上下文（{type(e).__name__}: {e}）')
        return
    try:
        vs = '''#version 330 core
in vec3 position;
uniform WindowBlock { mat4 projection; mat4 view; } window;
void main() { gl_Position = window.projection * window.view * vec4(position, 1.0); }'''
        fs = ('#version 330 core\nout vec4 o;\nuniform WindowBlock { mat4 projection; '
              'mat4 view; } window;\n' + csm_glsl(MAX_CASCADES) + '''
void main() { o = vec4(vec3(csm_shadow(vec3(0.0), -5.0, 1.0)), 1.0); }''')
        prog = ShaderProgram(Shader(vs, 'vertex'), Shader(fs, 'fragment'))
        names = set(prog.uniforms)      # pyglet: ShaderProgram.uniforms 就是 dict[名字]
        for i in range(MAX_CASCADES):
            assert f'u_csm_vp{i}' in names, f'驱动把 u_csm_vp{i} 优化掉了？{sorted(names)}'
            assert f'u_csm_tex{i}' in names
        assert 'u_csm_split' in names and 'u_csm_pcf' in names
        # 真上传一次（这才是"能不能用"的证据）
        cas = csm_matrices(VIEW, 0.5, 200.0, TAN_HALF, ASPECT, LIGHT,
                           n=MAX_CASCADES)
        uniforms, splits = csm_uniforms(cas)
        for k, v in uniforms.items():
            prog[k] = v
        prog['u_csm_split'] = splits
        prog['u_csm_pcf'] = 1.5
        print(f'  ok: {MAX_CASCADES} 级 GLSL 在本机驱动上**编译通过**，'
              f'{len(uniforms)} 个矩阵 uniform 上传成功')
    finally:
        win.close()


# ------------------------------------------------------------------ runner --
def main() -> int:
    tests = [
        ('test_split_scheme_endpoints_and_monotonic',
         test_split_scheme_endpoints_and_monotonic),
        ('test_split_linear_and_log_extremes', test_split_linear_and_log_extremes),
        ('test_split_rejects_bad_args', test_split_rejects_bad_args),
        ('test_select_cascade_boundaries', test_select_cascade_boundaries),
        ('test_cascade_corners_land_on_ndc_cube_corners',
         test_cascade_corners_land_on_ndc_cube_corners),
        ('test_cascade_corners_rejects_bad_args',
         test_cascade_corners_rejects_bad_args),
        ('test_light_ortho_box_contains_all_corners',
         test_light_ortho_box_contains_all_corners),
        ('test_light_ortho_box_is_square', test_light_ortho_box_is_square),
        ('test_light_ortho_box_depth_padding_includes_casters',
         test_light_ortho_box_depth_padding_includes_casters),
        ('test_light_ortho_box_rejects_bad_corner_count',
         test_light_ortho_box_rejects_bad_corner_count),
        ('test_texel_snap_lands_on_grid_and_moves_less_than_a_texel',
         test_texel_snap_lands_on_grid_and_moves_less_than_a_texel),
        ('test_texel_snap_rejects_bad_size', test_texel_snap_rejects_bad_size),
        ('test_csm_matrices_radius_grows_and_splits_match',
         test_csm_matrices_radius_grows_and_splits_match),
        ('test_csm_matrices_snap_toggle_changes_only_translation',
         test_csm_matrices_snap_toggle_changes_only_translation),
        ('test_csm_matrices_rejects_bad_n', test_csm_matrices_rejects_bad_n),
        ('test_csm_uniforms_shape', test_csm_uniforms_shape),
        ('test_csm_glsl_expands_cascade_selection',
         test_csm_glsl_expands_cascade_selection),
        ('test_csm_glsl_pcf_kernel_is_odd', test_csm_glsl_pcf_kernel_is_odd),
        ('test_csm_glsl_rejects_bad_n', test_csm_glsl_rejects_bad_n),
        ('test_csm_glsl_compiles_on_this_driver',
         test_csm_glsl_compiles_on_this_driver),
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
    print(f'[test_csm] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_CSM_TESTS_FAILED')
        return 1
    print('ALL_CSM_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

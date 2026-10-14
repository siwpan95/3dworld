# -*- coding: utf-8 -*-
"""景深（DoF）测试套件 —— `engine/dof.py`。

运行::

    python -m tests.test_dof

这一层的好处是**整条链都是 19 世纪的光学公式**，于是可以拿"摄影常识"当判据，
而且能把容差收到 1e-9：

* **超焦距恒等式**：``focus = H`` ⇒ ``near`` 恰为 ``H/2``、``far`` 为 ∞；
* **景深端点恒等式**：在 ``near`` / ``far`` 处算出的 CoC **恰好等于**容限 ``c``
  —— 这条同时钉住 CoC 公式与 near/far 公式，两边任一写错都会炸
  （本轮实踩：分母漏了 ``2f``，这条只剩 0.17% 精度；收紧到 1e-9 后立刻暴露）；
* **弥散圆渐近线**：``z → ∞`` 时 CoC 趋于 ``A·f/(z_f − f)``，**不会无限增大**；
* **散景盘面积均匀**：点的平均半径 ≈ 2/3（均匀取极角会得 1/2 ⇒ 盘心堆点、亮斑偏亮）。

⚠ **本套件里所有距离都用"毫米"且必须物理自洽**（焦距 50mm ⇒ 对焦 3000mm）。
最初我随手写了 ``focus=10mm + focal=50mm``（合焦面在镜头内部），公式照样给出数值、
符号却是反的 —— 测试当场把这种"看起来在算、其实在算不可能的东西"抓了出来，
内核因此补了一条显式守卫。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.dof import (DOF_GLSL_UNIFORMS, bokeh_disk, circle_of_confusion,  # noqa: E402
                        coc_pixels, dof_glsl, dof_limits, hyperfocal)

# 一组"摄影上说得通"的参数（单位统一用 mm）。50mm 镜头 / f2.8 / 焦深容限 0.03mm
FOCAL, F_NUMBER, COC_LIMIT = 50.0, 2.8, 0.03
APERTURE = FOCAL / F_NUMBER
H = hyperfocal(FOCAL, F_NUMBER, COC_LIMIT)
FOCUS = 3000.0                                  # 对焦 3m：必须 > 焦距，否则物理不成立


# ------------------------------------------------------------------ 弥散圆 --
def test_coc_zero_at_focus_sign_and_monotonic():
    assert circle_of_confusion(FOCUS, FOCUS, APERTURE, FOCAL) == 0.0, '合焦面必须恰好为 0'
    # 符号：前负后正（着色器靠符号区分"前景要 clamp"）
    for z in (500.0, 1500.0, 2999.0):
        assert circle_of_confusion(z, FOCUS, APERTURE, FOCAL) < 0.0, f'z={z} 应在前景'
    for z in (3001.0, 5000.0, 50000.0):
        assert circle_of_confusion(z, FOCUS, APERTURE, FOCAL) > 0.0, f'z={z} 应在背景'
    # 两侧各自单调（离对焦面越远越虚）
    front = [abs(circle_of_confusion(z, FOCUS, APERTURE, FOCAL))
             for z in (2999.0, 2500.0, 2000.0, 1000.0, 200.0)]
    assert all(front[i] < front[i + 1] for i in range(len(front) - 1)), f'前景非单调：{front}'
    back = [circle_of_confusion(z, FOCUS, APERTURE, FOCAL)
            for z in (3001.0, 3500.0, 5000.0, 20000.0, 200000.0)]
    assert all(back[i] < back[i + 1] for i in range(len(back) - 1)), f'背景非单调：{back}'
    print(f'  ok: 合焦面恰为 0；前景为负、背景为正；两侧各自单调'
          f'（前景 |CoC| {front[0]:.4f}→{front[-1]:.4f}，背景 {back[0]:.4f}→{back[-1]:.4f}）')


def test_coc_converges_to_far_asymptote():
    """``z → ∞`` 时 CoC 趋于 ``A·f/(z_f − f)`` —— **不会无限增大**（摄影常识）。"""
    want = APERTURE * FOCAL / (FOCUS - FOCAL)
    got = circle_of_confusion(1e12, FOCUS, APERTURE, FOCAL)
    rel = abs(got - want) / want
    assert rel < 1e-8, f'渐近值 {want:.8f} vs z=1e12 时 {got:.8f}（相对差 {rel:.2e}）'
    # 收敛是 1/z 的一阶：相对偏差应恰好约等于 z_f/z（这条同时排除了"凑巧接近"）
    for z in (1e6, 1e8, 1e12):
        r = abs(circle_of_confusion(z, FOCUS, APERTURE, FOCAL) - want) / want
        law = FOCUS / z
        assert abs(r / law - 1.0) < 0.01, f'z={z:.0e}: 相对差 {r:.2e} 不符合 z_f/z={law:.2e}'
    # 沿路不能"越过"渐近线（背景侧单调有界）
    for z in (3001.0, 5000.0, 50000.0, 1e6):
        assert circle_of_confusion(z, FOCUS, APERTURE, FOCAL) < want, f'z={z} 越过了渐近线'
    print(f'  ok: 背景 CoC 单调趋向渐近值 {want:.6f}mm（z=1e12 时相对差 {rel:.1e}）；'
          f'偏差严格遵从 z_f/z 的 1/z 律；且不越过渐近线')


def test_coc_scales_linearly_with_aperture():
    """光圈直径翻倍 ⇒ 弥散圆翻倍（"收缩两档 = 景深变深"的数学起点）。"""
    for z in (1500.0, 5000.0, 50000.0):
        a = circle_of_confusion(z, FOCUS, APERTURE, FOCAL)
        b = circle_of_confusion(z, FOCUS, APERTURE * 2.0, FOCAL)
        assert abs(b - 2.0 * a) < 1e-12 * max(1.0, abs(b)), f'z={z}: {a} → {b} 不是 2 倍'
    print('  ok: 光圈直径 ×2 ⇒ CoC ×2（3 个距离逐点验证）')


def test_coc_rejects_bad_args():
    cases = [((0.0, FOCUS, APERTURE, FOCAL), 'z=0'),
             ((-3.0, FOCUS, APERTURE, FOCAL), 'z<0'),
             ((1500.0, 0.0, APERTURE, FOCAL), 'focus=0'),
             ((1500.0, FOCUS, 0.0, FOCAL), 'aperture=0'),
             ((1500.0, FOCUS, APERTURE, 0.0), 'focal=0'),
             ((1500.0, FOCAL, APERTURE, FOCAL), 'focus==focal'),
             ((1500.0, 20.0, APERTURE, FOCAL), 'focus<focal（合焦面在镜头内部）')]
    for args, label in cases:
        try:
            circle_of_confusion(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print(f'  ok: {len(cases)} 种非法输入全部被拒（含"合焦面在镜头内部"这条物理守卫）')


def test_coc_pixels_scaling():
    """像素直径 = 弥散圆 / 传感器高 × 图像高 —— 分辨率翻倍，同一虚化翻倍。"""
    p1 = coc_pixels(z=1500.0, focus=FOCUS, aperture=APERTURE, focal=FOCAL,
                    sensor_height=24.0, image_height=720)
    p2 = coc_pixels(z=1500.0, focus=FOCUS, aperture=APERTURE, focal=FOCAL,
                    sensor_height=24.0, image_height=1440)
    assert abs(p2 - 2.0 * p1) < 1e-9, f'{p1} → {p2} 不是 2 倍'
    mm = abs(circle_of_confusion(1500.0, FOCUS, APERTURE, FOCAL))
    # ⚠ 符号要一起带上：`coc_pixels` 与 `circle_of_confusion` 一样**保留符号**
    # （前景为负），所以比对得取绝对值 —— 我第一版直接拿带符号的 p1 去比正数，
    # 差了个正负号，报出来的却是一条没带消息的断言。
    assert abs(abs(p1) - mm / 24.0 * 720) < 1e-12, '像素 CoC 的换算比例不对'
    assert p1 < 0.0, '前景的像素 CoC 应当仍为负（符号不能被悄悄丢掉）'
    for sensor, image, label in ((0.0, 720, 'sensor=0'), (24.0, 0, 'image=0')):
        try:
            coc_pixels(z=1500.0, focus=FOCUS, aperture=APERTURE, focal=FOCAL,
                       sensor_height=sensor, image_height=image)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print(f'  ok: 像素 CoC 随分辨率线性（{p1:.4f} → {p2:.4f} 像素）；sensor/image 为 0 被拒')


# ------------------------------------------------------------------ 超焦距 / 景深 --
def test_hyperfocal_formula_and_monotonicity():
    assert abs(H - (FOCAL * FOCAL / (F_NUMBER * COC_LIMIT) + FOCAL)) < 1e-9
    h_narrow = hyperfocal(FOCAL, 16.0, COC_LIMIT)
    assert h_narrow < H, '收缩光圈应该把超焦距拉近'
    h_long = hyperfocal(FOCAL * 2.0, F_NUMBER, COC_LIMIT)
    assert h_long > H * 3.5, f'焦距 ×2 让超焦距涨得远不止 2 倍：{H:.0f} → {h_long:.0f}'
    for args, label in (((0.0, F_NUMBER, COC_LIMIT), 'focal=0'),
                        ((FOCAL, 0.0, COC_LIMIT), 'N=0'),
                        ((FOCAL, F_NUMBER, 0.0), 'c=0')):
        try:
            hyperfocal(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print(f'  ok: H = f²/(N·c)+f = {H:.2f}mm；N 2.8→16 拉近到 {h_narrow:.2f}；'
          f'焦距 ×2 涨到 {h_long:.0f}（>3.5 倍）；三个参数为 0 被拒')


def test_dof_limits_identities_are_exact():
    """**头号测试**：超焦距恒等式 + 端点 CoC 恒等式，容差 1e-9。

    两条一起钉住 CoC 公式与 near/far 公式 —— 本轮就是靠它发现"分母漏了 2f"。
    """
    near, far = dof_limits(H, FOCAL, F_NUMBER, COC_LIMIT)
    assert abs(near - H / 2.0) < 1e-9, f'focus=H 时 near 必须恰为 H/2：{near} vs {H / 2}'
    assert far == math.inf, f'focus=H 时 far 必须是 ∞，实际 {far}'
    worst = 0.0
    for focus in (200.0, 1000.0, 3000.0, 10000.0, H * 0.999):
        n2, f2 = dof_limits(focus, FOCAL, F_NUMBER, COC_LIMIT)
        cn = abs(circle_of_confusion(n2, focus, APERTURE, FOCAL)) / COC_LIMIT
        cf = (abs(circle_of_confusion(f2, focus, APERTURE, FOCAL)) / COC_LIMIT
              if f2 != math.inf else 1.0)
        worst = max(worst, abs(cn - 1.0), abs(cf - 1.0))
    assert worst < 1e-9, f'端点 CoC/容限 应恰为 1，最大偏差 {worst:.2e}'
    assert dof_limits(H * 1.5, FOCAL, F_NUMBER, COC_LIMIT)[1] == math.inf
    print(f'  ok: focus=H ⇒ near 恰为 H/2（差 {abs(near - H / 2):.1e}）、far=∞；'
          f'5 个对焦距离的端点 CoC/容限 偏差 < {worst:.1e}；超 H 后 far 仍为 ∞')


def test_dof_limits_narrow_with_aperture():
    """光圈越大景深越浅（摄影里最实用的一条），并对"镜头内部对焦"报错。

    ⚠ **方向别写反**：近界是"离相机多远还算清晰"。景深变**浅**时，近界是**变大**
    （往对焦面靠），远界是**变小**。我第一版按"近界前移"写，方向正好相反。
    """
    widths = []
    for N in (1.4, 2.8, 5.6, 11.0):
        n2, f2 = dof_limits(FOCUS, FOCAL, N, COC_LIMIT)
        widths.append((N, n2, f2))
    for i in range(len(widths) - 1):
        n_a, n_b = widths[i][1], widths[i + 1][1]
        f_a, f_b = widths[i][2], widths[i + 1][2]
        assert n_a > n_b, f'N={widths[i][0]}→{widths[i + 1][0]} 近界应变小：{n_a} vs {n_b}'
        assert f_a < f_b, f'N={widths[i][0]}→{widths[i + 1][0]} 远界应变大：{f_a} vs {f_b}'
    try:
        dof_limits(0.02, FOCAL, F_NUMBER, COC_LIMIT)
    except ValueError:
        pass
    else:
        raise AssertionError('focus <= focal 竟然通过')
    span = [f'{N}:{n:.0f}~{f:.0f}' for N, n, f in widths]
    print(f'  ok: N 1.4→2.8→5.6→11 近界逐步变小、远界逐步变大 —— {"  ".join(span)}')


# ------------------------------------------------------------------ 散景盘 --
def test_bokeh_disk_area_uniform_and_deterministic():
    n = 64
    pts = bokeh_disk(n)
    assert all(math.hypot(x, y) <= 1.0 + 1e-12 for x, y in pts), '有点跑出单位盘'
    mean_r = sum(math.hypot(x, y) for x, y in pts) / n
    # 面积均匀时 E[r] = ∫₀¹ 2r² dr = 2/3；均匀取极角会得 1/2（盘心堆点）
    assert abs(mean_r - 2.0 / 3.0) < 0.02, f'平均半径 {mean_r:.4f} 偏离面积均匀的 2/3'
    assert len(set(pts)) == n, '有重复点'
    assert bokeh_disk(n) == pts, '必须确定性（剧照可复现）'
    assert bokeh_disk(n, rotation=90.0) != pts, '换旋转应真的变'
    assert len(bokeh_disk(n, blades=6)) == n, '叶数不改变点数'
    assert abs(math.hypot(*bokeh_disk(1)[0]) - math.sqrt(0.5)) < 1e-12, 'n=1 应在 r=√0.5'
    for args, label in (((0,), 'n=0'), ((-2,), 'n<0'), ((8, 2), 'blades=2')):
        try:
            bokeh_disk(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print(f'  ok: {n} 点平均半径 {mean_r:.4f}（面积均匀 ⇒ 2/3，非 1/2）、无重复、确定性、'
          f'可旋转；n=1 落在 √0.5；n=0 / blades=2 被拒')


def test_bokeh_polygon_is_clamped():
    """叶片数 ≥3 时采样点必须全落在正多边形内（内切圆半径 ``cos(π/blades)``）。

    法线角度必须与实现**逐字一致**（``k·2π/blades``）。我第一版在测试里多加了一个
    ``π/blades`` 的偏移，于是两套多边形差半个扇区 —— 测试把"对的实现"判成了错。
    """
    for blades in (3, 5, 6, 8):
        pts = bokeh_disk(48, blades=blades)
        inr = math.cos(math.pi / blades)
        worst = -9.0
        for x, y in pts:
            best = -9.0
            for k in range(blades):
                a = 2.0 * math.pi * k / blades
                best = max(best, x * math.cos(a) + y * math.sin(a))
            worst = max(worst, best)
        assert worst <= inr + 1e-9, \
            f'blades={blades}: 最大投影 {worst:.6f} 超过内切圆 {inr:.6f}'
        # 也不能夹过头：至少要有点贴到边上（否则这分支等于没生效）
        assert worst > inr - 0.25, f'blades={blades}: 点全挤在内部，夹取过猛（{worst:.3f}）'
    print('  ok: 3/5/6/8 叶片的点全部满足内切圆约束，且确实有点被夹到边上')


# ------------------------------------------------------------------ GLSL --
def test_dof_glsl_structure_and_uniforms():
    g = dof_glsl(16)
    for name in DOF_GLSL_UNIFORMS:
        assert f' {name};' in g, f'缺 uniform {name}'
    assert '#version' not in g, '片段里不能自带版本声明'
    assert g.count('uniform sampler2D') == 2, '应只声明 depth + color 两个采样器'
    assert 'const int   dof_apply_N = 16;' in g, '采样数应烘成常量'
    assert g.count('vec2(') >= 16, '散景盘应烘进着色器常量'
    # 两条"只在特定画面才看得出来"的正确性都在
    assert 'reach' in g, '缺"前景不能糊到背景上"的剔除'
    assert 'u_dof_max_coc' in g and 'clamp(' in g, '缺近场 CoC 上限'
    for args, label in (((0,), 'n=0'), ((4, 'dof', 0, [(0.0, 0.0)]), 'disk 点数不符')):
        try:
            dof_glsl(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print('  ok: 8 个 uniform 齐全、只有 2 个采样器、16 点散景盘烘成常量、'
          '前景剔除与 CoC 上限都在；n=0 与点数不符被拒')


def test_dof_glsl_compiles_and_composes_with_csm():
    """真编译：单独一次 + 与 CSM 拼在一起（证明两组 uniform 不撞名、可同帧共存）。"""
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
        head = '#version 330 core\nout vec4 o;\nuniform vec2 u_uv;\n'
        plans = [('单独', '', '    float s = 0.0;\n')]
        from world3d.engine.csm import csm_glsl
        plans.append(('与 CSM 拼', csm_glsl(2) + '\n',
                      '    float s = csm_shadow(vec3(0.0), -5.0, 1.0);\n'))
        for tag, extra, call in plans:
            # ⚠ 拼上 CSM 就得**真的调用它**：只声明不采样会被驱动优化掉，
            # 那样"缺 uniform"是假警报（这条坑本会话已经踩过两次）
            fs = (head + extra + dof_glsl(24, blades=6)
                  + '\nvoid main() {\n    float z = texture(u_dof_depth, u_uv).r;\n'
                  + call
                  + '    o = vec4(dof_apply(u_uv) * (1.0 + s) * step(0.0, z), 1.0);\n}\n')
            prog = ShaderProgram(Shader(vs, 'vertex'), Shader(fs, 'fragment'))
            names = set(prog.uniforms)
            need = list(DOF_GLSL_UNIFORMS)
            if extra:
                need += ['u_csm_vp0', 'u_csm_vp1', 'u_csm_tex0', 'u_csm_tex1']
            missing = [n for n in need if n not in names]
            assert not missing, f'{tag}：缺 uniform {missing}（现有 {sorted(names)}）'
            print(f'       {tag}：编译通过，{len(need)} 个 uniform 在位')
    finally:
        win.close()
    print('  ok: DoF 单独 + 与 CSM 组合，两种拼法都在本机驱动上编译并保留全部 uniform')


# ------------------------------------------------------------------ runner --
def main() -> int:
    tests = [
        ('test_coc_zero_at_focus_sign_and_monotonic',
         test_coc_zero_at_focus_sign_and_monotonic),
        ('test_coc_converges_to_far_asymptote',
         test_coc_converges_to_far_asymptote),
        ('test_coc_scales_linearly_with_aperture',
         test_coc_scales_linearly_with_aperture),
        ('test_coc_rejects_bad_args', test_coc_rejects_bad_args),
        ('test_coc_pixels_scaling', test_coc_pixels_scaling),
        ('test_hyperfocal_formula_and_monotonicity',
         test_hyperfocal_formula_and_monotonicity),
        ('test_dof_limits_identities_are_exact',
         test_dof_limits_identities_are_exact),
        ('test_dof_limits_narrow_with_aperture',
         test_dof_limits_narrow_with_aperture),
        ('test_bokeh_disk_area_uniform_and_deterministic',
         test_bokeh_disk_area_uniform_and_deterministic),
        ('test_bokeh_polygon_is_clamped', test_bokeh_polygon_is_clamped),
        ('test_dof_glsl_structure_and_uniforms',
         test_dof_glsl_structure_and_uniforms),
        ('test_dof_glsl_compiles_and_composes_with_csm',
         test_dof_glsl_compiles_and_composes_with_csm),
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
    print(f'[test_dof] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_DOF_TESTS_FAILED')
        return 1
    print('ALL_DOF_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

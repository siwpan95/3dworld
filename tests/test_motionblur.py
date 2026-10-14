# -*- coding: utf-8 -*-
"""运动模糊测试套件 —— `engine/motionblur.py`。

运行::

    python -m tests.test_motionblur

这一层的判据是**三条物理律**，而且它们恰好是"整屏方向模糊"做不到的事
（那种做法连深度都没有）：

===========================  ==========================================
相机状态                      运动矢量应当
===========================  ==========================================
**不动**                      **恒为 0**（不需要额外的"静止检测"）
**纯平移**                    ``|mv| ∝ 1/z`` —— 近处扫得远、远处几乎不动
**纯旋转**                    **与深度无关** —— 远处的山也会糊
===========================  ==========================================

⚠ 三条都要用**朝向固定**的两台相机来构造（``target = position + 固定前向``），
否则"平移"里混进一点转动，1/z 律立刻不成立 —— 我第一版就是这么把自己绕进去的
（默认 target 固定 ⇒ 其实是平移 + 微转，测出来"运动矢量与深度无关"）。
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.engine.camera import Camera  # noqa: E402
from world3d.engine.mathkit import mat4_inverse, mat4_mul  # noqa: E402
from world3d.engine.motionblur import (MB_GLSL_UNIFORMS, mb_glsl,  # noqa: E402
                               motion_vector, reconstruct_world, reproject,
                               shutter_weights)

ASPECT = 16 / 9
CUR = (0.5, 2.0, 10.0)
PREV = (0.0, 2.0, 10.0)              # 相机在 x 上右移了 0.5


def vp_fwd(pos, target=None, fov=50.0, near=0.1, far=3000.0):
    """朝向**固定为 (0,0,-1)** 的 view-projection —— 两帧之间才是纯平移。

    ``target`` 给了就是纯旋转（同位置、只改朝向）。
    """
    tgt = target if target is not None else (pos[0], pos[1], pos[2] - 1.0)
    cam = Camera(position=pos, target=tgt, fov=fov, near=near, far=far)
    return mat4_mul(tuple(cam.projection(ASPECT)), cam.view_matrix())


def _uv_grid():
    return [(u / 4.0, v / 4.0) for u in range(1, 4) for v in range(1, 4)]


# ------------------------------------------------------------------ 三条物理律 --
def test_static_camera_has_zero_motion():
    """相机不动 ⇒ 运动矢量**恒为 0**（这条同时也证明射线重建与投影是互逆的）。"""
    vp = vp_fwd(CUR)
    inv = mat4_inverse(vp)
    worst = 0.0
    for uv in _uv_grid():
        for z in (0.5, 3.0, 12.0, 200.0, 1e5):
            mv, ok = motion_vector(uv, z, inv, vp, CUR)
            assert ok, f'静止相机不该越界：{uv} z={z}'
            worst = max(worst, abs(mv[0]), abs(mv[1]))
    assert worst < 1e-12, f'静止相机的运动矢量应恒为 0，最大 {worst:.3e}'
    print(f'  ok: 9 个屏幕位置 × 5 个深度，静止相机的最大运动矢量 {worst:.1e}（= 0）')


def test_translation_motion_is_exactly_one_over_z():
    """**核心**：纯平移时 ``|mv|·z`` 必须是**常数**（∝ 1/z）。

    整屏方向模糊没有深度 ⇒ 它给不出这条，也就永远画不出"近处扫、远处不动"。
    """
    inv_cur = mat4_inverse(vp_fwd(CUR))
    vp_prev = vp_fwd(PREV)
    uv = (0.5, 0.5)
    vals = []
    for z in (5.0, 10.0, 20.0, 40.0, 80.0):
        mv, _ = motion_vector(uv, z, inv_cur, vp_prev, CUR)
        vals.append((z, abs(mv[0]) * z))
    ref = vals[0][1]
    worst = max(abs(v - ref) / ref for _, v in vals)
    assert worst < 1e-6, f'|mv|·z 应恒等，实测 {[(z, round(v, 6)) for z, v in vals]}'
    # 顺带确认"绝对值确实在随深度变小"（不然恒等式可能是两边都 0）
    mags = [abs(motion_vector(uv, z, inv_cur, vp_prev, CUR)[0][0])
            for z in (5.0, 10.0, 20.0, 40.0, 80.0)]
    assert all(mags[i] > mags[i + 1] for i in range(len(mags) - 1)), f'未随深度递减：{mags}'
    print(f'  ok: |mv|·z 恒为 {ref:.6f}（5 个深度相对差 < {worst:.1e}）；'
          f'|mv| 从 {mags[0]:.6f} 单调降到 {mags[-1]:.6f}')


def test_pure_translation_far_field_does_not_blur():
    """纯平移时"远处的山不该糊"：``z → ∞`` 时 |mv| → 0（整屏糊做不到这条）。"""
    inv_cur = mat4_inverse(vp_fwd(CUR))
    vp_prev = vp_fwd(PREV)
    uv = (0.5, 0.5)
    last = None
    for z in (1e3, 1e4, 1e5, 1e6, 1e7):
        mv, _ = motion_vector(uv, z, inv_cur, vp_prev, CUR)
        mag = abs(mv[0])
        if last is not None:
            assert mag < last, f'z={z:.0e} 未继续减小'
        last = mag
    assert last < 1e-7, f'极远处仍应有运动（实测 {last:.3e}）'
    # 符号也定死：相机右移 ⇒ 内容左移 ⇒ mv.x > 0
    mv, _ = motion_vector(uv, 20.0, inv_cur, vp_prev, CUR)
    assert mv[0] > 0.0, f'相机右移时 mv.x 应为正（内容往左跑），实测 {mv[0]}'
    print(f'  ok: z 1e3→1e7 时 |mv| 单调降到 {last:.2e}（远处不糊）；'
          f'相机右移 ⇒ mv.x = {motion_vector(uv, 20.0, inv_cur, vp_prev, CUR)[0][0]:+.6f} > 0')


def test_pure_rotation_motion_is_depth_independent():
    """纯旋转（同位置、只改朝向）⇒ 运动矢量**与深度无关** —— 远处的山也会糊。"""
    inv_cur = mat4_inverse(vp_fwd(CUR))
    vp_rot = vp_fwd(CUR, target=(CUR[0] + 0.25, CUR[1], CUR[2] - 1.0))
    vals = []
    for z in (5.0, 100.0, 1e4, 1e7):
        mv, _ = motion_vector((0.5, 0.5), z, inv_cur, vp_rot, CUR)
        vals.append((mv[0], mv[1]))
    for x, y in vals:
        assert abs(x - vals[0][0]) < 1e-9 and abs(y - vals[0][1]) < 1e-9, \
            f'纯旋转的运动矢量必须与深度无关，实测 {vals}'
    assert abs(vals[0][0]) > 0.01, f'这个转角下应该有明显的运动，实测 {vals[0]}'
    print(f'  ok: 4 个深度（5m~1e7m）的运动矢量**逐位相同** = '
          f'({vals[0][0]:.9f}, {vals[0][1]:.9f})')


# ------------------------------------------------------------------ 重建 / 边界 --
def test_reconstruct_world_lies_on_the_ray():
    """``reconstruct_world`` 的结果必须满足两条：**到相机的距离恰为 dist**、
    且**投回去正好是同一个 uv**（往返一致 —— 这是重建公式的完整定义）。"""
    vp = vp_fwd(CUR)
    inv = mat4_inverse(vp)
    worst_d = worst_uv = 0.0
    for uv in _uv_grid():
        for z in (0.5, 4.0, 60.0, 1000.0):
            w = reconstruct_world(uv, z, inv, CUR)
            d = math.dist(w, CUR)
            worst_d = max(worst_d, abs(d - z) / z)
            # 用**同一帧**的 vp 投回去：必须回到原 uv（说明重投影是零位移）
            mv, _ = motion_vector(uv, z, inv, vp, CUR)
            worst_uv = max(worst_uv, abs(mv[0]), abs(mv[1]))
    assert worst_d < 1e-12, f'重建点到相机的距离与 dist 不符：相对差 {worst_d:.2e}'
    assert worst_uv < 1e-12, f'重建→投影的往返不一致：{worst_uv:.2e}'
    print(f'  ok: 36 组 (uv, dist)：|重建点−相机| 与 dist 相对差 < {worst_d:.1e}、'
          f'往返投影误差 < {worst_uv:.1e}')


def test_reproject_rejects_bad_and_flags_offscreen():
    inv = mat4_inverse(vp_fwd(CUR))
    vp_prev = vp_fwd(PREV)
    for d in (0.0, -1.0):
        try:
            reproject((0.5, 0.5), d, inv, vp_prev, CUR)
        except ValueError:
            pass
        else:
            raise AssertionError(f'dist={d} 竟然通过')
    try:
        reconstruct_world((0.5, 0.5), 3.0, inv, (0.0, 0.0))
    except ValueError:
        pass
    else:
        raise AssertionError('cam_position 只有两个分量竟然通过')
    # 相机瞬移 4000 单位 ⇒ 上一帧的那个点跑到屏幕外 ⇒ valid=False
    vp_far = vp_fwd((4000.0, 2.0, 10.0))
    _uv, ok = reproject((0.5, 0.5), 10.0, inv, vp_far, CUR)
    assert ok is False, '瞬移后应标记越界，让调用方别采样'
    print('  ok: dist<=0 与坏 cam_position 被拒；相机瞬移后 valid=False（可跳过采样）')


# ------------------------------------------------------------------ 快门权重 --
def test_shutter_weights_sum_and_shape():
    for n in (1, 2, 4, 12, 64):
        box = shutter_weights(n, 'box')
        tent = shutter_weights(n, 'tent')
        assert abs(sum(box) - 1.0) < 1e-12 and abs(sum(tent) - 1.0) < 1e-12, (n, box, tent)
        assert max(box) - min(box) < 1e-12, 'box 必须等权'
        assert all(abs(tent[i] - tent[-1 - i]) < 1e-12 for i in range(n)), 'tent 必须对称'
        if n >= 4:
            mid = n // 2
            assert tent[mid] > tent[0] * 1.5, f'tent 中心应更重：{tent}'
    for args, label in (((0, 'box'), 'n=0'), ((4, 'nope'), '未知模式')):
        try:
            shutter_weights(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print('  ok: n=1/2/4/12/64 的 box 与 tent 都归一；box 等权、tent 对称且中心更重；'
          'n=0 与未知模式被拒')


# ------------------------------------------------------------------ GLSL --
def test_mb_glsl_structure_and_uniforms():
    g = mb_glsl(12)
    for name in MB_GLSL_UNIFORMS:
        assert f' {name};' in g, f'缺 uniform {name}'
    assert '#version' not in g, '片段里不能自带版本声明'
    assert g.count('uniform sampler2D') == 2, '应只声明 depth + color 两个采样器'
    assert 'const int   mb_apply_N = 12;' in g, '采样数应烘成常量'
    assert 'mb_apply_W[12]' in g, '快门权重应烘成常量'
    # 三条关键正确性
    assert 'mb_apply_prev_uv' in g, '缺逐像素重投影'
    assert 'u_mb_cam' in g, '重投影必须用相机位置（少了它会算出假运动）'
    assert 'depth_gap' in g, '缺前景拖影污染的剔除'
    assert 'u_mb_max_len' in g and '::' not in g.replace('vec2(', ''), '缺长度上限'
    for args, label in (((0,), 'n=0'),):
        try:
            mb_glsl(*args)
        except ValueError:
            pass
        else:
            raise AssertionError(f'{label} 竟然通过')
    print('  ok: 9 个 uniform 齐全、只有 2 个采样器、12 个采样与权重烘成常量、'
          '逐像素重投影 + 前景剔除 + 长度上限都在；n=0 被拒')


def test_mb_glsl_compiles_alone_and_with_csm_and_dof():
    """真编译：单独 / 与 CSM / 与 CSM+景深**三个模块拼在一起**。

    后者是 P3 帧图管线的预演：三个深度相关的效果共处一个片段时，
    uniform 不能撞名、采样器不能互相冲掉。
    """
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
        from world3d.engine.csm import csm_glsl
        from world3d.engine.dof import DOF_GLSL_UNIFORMS, dof_glsl
        vs = '''#version 330 core
in vec3 position;
uniform WindowBlock { mat4 projection; mat4 view; } window;
void main() { gl_Position = window.projection * window.view * vec4(position, 1.0); }'''
        head = '#version 330 core\nout vec4 o;\nuniform vec2 u_uv;\n'
        plans = [('单独', '', ['    float s = 0.0;\n', '    vec3 c = mb_apply(u_uv);\n'],
                  list(MB_GLSL_UNIFORMS))]
        plans.append(('+CSM', csm_glsl(2) + '\n',
                      ['    float s = csm_shadow(vec3(0.0), -5.0, 1.0);\n',
                       '    vec3 c = mb_apply(u_uv);\n'],
                      list(MB_GLSL_UNIFORMS)
                      + ['u_csm_vp0', 'u_csm_vp1', 'u_csm_tex0', 'u_csm_tex1']))
        plans.append(('+CSM+DoF', csm_glsl(2) + '\n' + dof_glsl(16) + '\n',
                      ['    float s = csm_shadow(vec3(0.0), -5.0, 1.0);\n',
                       '    vec3 c = mb_apply(u_uv) + dof_apply(u_uv);\n'],
                      list(MB_GLSL_UNIFORMS) + list(DOF_GLSL_UNIFORMS)
                      + ['u_csm_vp0', 'u_csm_vp1', 'u_csm_tex0', 'u_csm_tex1']))
        for tag, extra, body_lines, need in plans:
            fs = (head + extra + mb_glsl(12) + '\nvoid main() {\n'
                  + '    float z = texture(u_mb_depth, u_uv).r;\n'
                  + ''.join(body_lines)
                  + '    o = vec4(c * (1.0 + s) * step(0.0, z), 1.0);\n}\n')
            prog = ShaderProgram(Shader(vs, 'vertex'), Shader(fs, 'fragment'))
            names = set(prog.uniforms)
            missing = [n for n in need if n not in names]
            assert not missing, f'{tag}：缺 uniform {missing}（现有 {sorted(names)}）'
            print(f'       {tag}：编译通过，{len(need)} 个 uniform 在位')
    finally:
        win.close()
    print('  ok: 运动模糊单独 / 与 CSM / 与 CSM+景深，三种拼法都在本机驱动上编译通过')


# ------------------------------------------------------------------ runner --
def main() -> int:
    tests = [
        ('test_static_camera_has_zero_motion', test_static_camera_has_zero_motion),
        ('test_translation_motion_is_exactly_one_over_z',
         test_translation_motion_is_exactly_one_over_z),
        ('test_pure_translation_far_field_does_not_blur',
         test_pure_translation_far_field_does_not_blur),
        ('test_pure_rotation_motion_is_depth_independent',
         test_pure_rotation_motion_is_depth_independent),
        ('test_reconstruct_world_lies_on_the_ray',
         test_reconstruct_world_lies_on_the_ray),
        ('test_reproject_rejects_bad_and_flags_offscreen',
         test_reproject_rejects_bad_and_flags_offscreen),
        ('test_shutter_weights_sum_and_shape', test_shutter_weights_sum_and_shape),
        ('test_mb_glsl_structure_and_uniforms',
         test_mb_glsl_structure_and_uniforms),
        ('test_mb_glsl_compiles_alone_and_with_csm_and_dof',
         test_mb_glsl_compiles_alone_and_with_csm_and_dof),
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
    print(f'[test_motionblur] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_MOTIONBLUR_TESTS_FAILED')
        return 1
    print('ALL_MOTIONBLUR_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

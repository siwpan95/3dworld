# -*- coding: utf-8 -*-
"""逻辑层无窗口单元测试（零依赖，直接运行）：

    python -m tests.run_tests

覆盖：相机轨道/钳制/视图矩阵性质、事件总线派发。
全程不创建 GL 窗口 —— 这是"逻辑层不 import pyglet.gl"的验收。
"""
import math
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyglet.math import Vec3, Quaternion
from logic.animation import (Sampler, Channel, Timeline, quat_from_axis_angle,
                             quat_to_mat4, slerp, build_demo_timeline)
from logic.camera import OrbitCamera, look_at_matrix, PITCH_MIN, PITCH_MAX, DIST_MIN, DIST_MAX
from logic.events import EventBus
from logic.playback import PlaybackController


def test_camera_initial_pose():
    cam = OrbitCamera()
    assert (cam.yaw, cam.pitch, cam.dist) == (45.0, 30.0, 18.0)
    eye = cam.eye
    yr, pr = math.radians(45.0), math.radians(30.0)
    assert abs(eye.x - 18 * math.cos(pr) * math.cos(yr)) < 1e-9
    assert abs(eye.y - (1.0 + 18 * math.sin(pr))) < 1e-9
    print('  ok: initial pose (yaw=45, pitch=30, dist=18)')


def test_camera_clamps():
    cam = OrbitCamera()
    cam.orbit(0.0, 10000.0)
    assert cam.pitch == PITCH_MAX, cam.pitch
    cam.orbit(0.0, -10000.0)
    assert cam.pitch == PITCH_MIN, cam.pitch
    cam.zoom(-10000.0)
    assert cam.dist == DIST_MAX, cam.dist
    cam.zoom(10000.0)
    assert cam.dist == DIST_MIN, cam.dist
    print('  ok: pitch/dist clamped to [%g,%g] / [%g,%g]' %
          (PITCH_MIN, PITCH_MAX, DIST_MIN, DIST_MAX))


def test_camera_advance():
    cam = OrbitCamera()
    before = cam.yaw
    cam.advance(0.5)
    assert abs(cam.yaw - (before + 4.0)) < 1e-9   # 8 deg/s
    print('  ok: advance(0.5s) -> yaw +4.0 deg')


def test_view_matrix_maps_eye_to_origin():
    """视图矩阵应把 eye 映射到原点、target 映射到 -Z 轴。"""
    cam = OrbitCamera()
    m = cam.view_matrix()
    eye, target = cam.eye, cam.target

    def apply(mat, v):
        x, y, z = v
        return (mat[0] * x + mat[4] * y + mat[8] * z + mat[12],
                mat[1] * x + mat[5] * y + mat[9] * z + mat[13],
                mat[2] * x + mat[6] * y + mat[10] * z + mat[14])

    ex, ey, ez = apply(m, (eye.x, eye.y, eye.z))
    assert abs(ex) < 1e-6 and abs(ey) < 1e-6 and abs(ez) < 1e-6, (ex, ey, ez)

    tx, ty, tz = apply(m, (target.x, target.y, target.z))
    assert abs(tx) < 1e-6 and abs(ty) < 1e-6, (tx, ty, tz)
    assert abs(tz - (-cam.dist)) < 1e-6, tz      # target 落在相机前方 -Z
    print('  ok: eye -> origin, target -> (0,0,-dist)')


def test_look_at_matches_multiple_poses():
    """不同位姿下 eye 均应映射到原点。"""
    for yaw in (0, 90, 200, 315):
        for pitch in (5, 60, 88):
            cam = OrbitCamera(yaw=yaw, pitch=pitch)
            m = cam.view_matrix()
            e = cam.eye
            z = m[2] * e.x + m[6] * e.y + m[10] * e.z + m[14]
            x = m[0] * e.x + m[4] * e.y + m[8] * e.z + m[12]
            y = m[1] * e.x + m[5] * e.y + m[9] * e.z + m[13]
            assert abs(x) < 1e-6 and abs(y) < 1e-6 and abs(z) < 1e-6
    print('  ok: 12 poses all map eye to origin')


def test_event_bus():
    bus = EventBus()
    frames, views, ends = [], [], []
    bus.push_handlers(on_frame=frames.append,
                      on_view_changed=lambda: views.append(True),
                      on_timeline_end=lambda: ends.append(True))
    bus.dispatch_event('on_frame', 0.016)
    bus.dispatch_event('on_frame', 0.016)
    bus.dispatch_event('on_view_changed')
    bus.dispatch_event('on_timeline_end')
    assert frames == [0.016, 0.016]
    assert len(views) == 1 and len(ends) == 1
    print('  ok: on_frame x2, on_view_changed x1, on_timeline_end x1 delivered')


def test_quat_axis_angle_and_slerp():
    q90 = quat_from_axis_angle((0, 1, 0), 90.0)
    m = quat_to_mat4(q90)
    def apply_x(mat):  # 变换 +X 单位向量
        return (mat[0], mat[1], mat[2])
    x, y, z = apply_x(m)
    assert abs(x) < 1e-6 and abs(y) < 1e-6 and abs(z + 1.0) < 1e-6, (x, y, z)
    # slerp 中点：identity -> 90°Y 应为 45°Y
    mid = slerp(Quaternion(), q90, 0.5)
    q45 = quat_from_axis_angle((0, 1, 0), 45.0)
    assert all(abs(a - b) < 1e-6 for a, b in zip(mid, q45)), (mid, q45)
    # 端点
    assert all(abs(a - b) < 1e-9 for a, b in zip(slerp(Quaternion(), q90, 0.0), Quaternion()))
    assert all(abs(a - b) < 1e-9 for a, b in zip(slerp(Quaternion(), q90, 1.0), q90))
    print('  ok: axis-angle 90Y maps X->-Z; slerp midpoint=endpoints')


def test_sampler_linear_step():
    s_lin = Sampler([0.0, 2.0], [Vec3(0, 0, 0), Vec3(2, 4, -2)], 'LINEAR')
    v = s_lin.sample(1.0)
    assert all(abs(a - b) < 1e-9 for a, b in zip(v, (1, 2, -1))), v
    assert s_lin.sample(-1.0) == Vec3(0, 0, 0)          # 下界钳制
    assert s_lin.sample(9.0) == Vec3(2, 4, -2)          # 上界钳制
    s_step = Sampler([0.0, 1.0, 2.0], [10.0, 20.0, 30.0], 'STEP')
    assert s_step.sample(0.5) == 10.0 and s_step.sample(1.5) == 20.0
    print('  ok: LINEAR midpoint + clamps; STEP holds previous key')


def test_timeline_wrap_and_sample():
    ch = Channel('cube_0', 'translation',
                 Sampler([0.0, 1.0, 2.0], [Vec3(0, 0, 0), Vec3(0, 1, 0), Vec3(0, 0, 0)]))
    tl = Timeline(2.0, [ch])
    assert tl.advance(0.5) is False and abs(tl.playhead - 0.5) < 1e-9
    assert tl.advance(2.0) is True                      # 越过时长 -> 回绕
    assert abs(tl.playhead - 0.5) < 1e-9                # 2.5 -> 0.5
    out = tl.sample()
    assert 'cube_0' in out and 'translation' in out['cube_0']
    print('  ok: advance wrap returns True; sample dict keyed by node/prop')


def test_demo_timeline_choreography():
    meta = [(f'ring_{i}', Vec3(5 * (1 if i % 2 else -1) if i < 2 else 0, 0.5, 0)) for i in range(10)]
    meta[0] = ('ring_0', Vec3(5, 0.5, 0))
    meta.append(('center', Vec3(0, 1.0, 0)))
    tl = build_demo_timeline(meta, duration=4.0)
    assert tl.duration == 4.0
    s0 = tl.sample()
    # 环形立方体：translation.y 在 [0.5, 1.0] 内浮动
    for i in range(10):
        y = s0[f'ring_{i}']['translation'].y
        assert 0.5 - 1e-9 <= y <= 1.0 + 1e-9, (i, y)
        assert 'rotation' in s0[f'ring_{i}']
    assert 'rotation' in s0['center'] and 'translation' not in s0['center']
    # 回绕连续性：t=4-ε 与 t=0 的 y 值一致（首尾关键帧相同）
    tl.seek(4.0 - 1e-6)
    y_end = tl.sample()['ring_0']['translation'].y
    tl.seek(0.0)
    y_start = tl.sample()['ring_0']['translation'].y
    assert abs(y_end - y_start) < 1e-3, (y_end, y_start)
    print('  ok: demo timeline ranges, channels, wrap continuity')


def _make_linear_timeline(duration=2.0):
    ch = Channel('cube_0', 'translation',
                 Sampler([0.0, duration], [Vec3(0, 0, 0), Vec3(0, 2, 0)]))
    return Timeline(duration, [ch])


def test_playback_states():
    tl = _make_linear_timeline()
    pb = PlaybackController(tl, looping=True)
    assert pb.playing is False
    assert pb.advance(0.5) is None                      # 暂停时不推进
    assert abs(tl.playhead) < 1e-9
    pb.play()
    assert pb.advance(0.5) is None and abs(tl.playhead - 0.5) < 1e-9
    pb.pause(); pb.play()                               # toggle 等价
    assert pb.playing is True
    print('  ok: paused no-op; play/toggle advance playhead')


def test_playback_loop_wrap():
    tl = _make_linear_timeline(duration=2.0)
    pb = PlaybackController(tl, looping=True)
    pb.play()
    assert pb.advance(1.2) is None
    assert pb.advance(1.2) == PlaybackController.WRAPPED  # 2.4 -> 0.4
    assert abs(tl.playhead - 0.4) < 1e-9
    print('  ok: looping wraps and returns WRAPPED')


def test_playback_end_pause():
    tl = _make_linear_timeline(duration=2.0)
    pb = PlaybackController(tl, looping=False)
    pb.play()
    assert pb.advance(3.0) == PlaybackController.ENDED  # 越界 -> 停在末尾
    assert pb.playing is False
    assert tl.playhead >= tl.duration - 1e-6
    assert abs(tl.sample()['cube_0']['translation'].y - 2.0) < 1e-3  # 末姿态
    print('  ok: non-loop clamps at end, auto-pauses, last pose sampled')


def test_playback_speed_and_seek():
    tl = _make_linear_timeline(duration=2.0)
    pb = PlaybackController(tl, looping=True, speed=2.0)
    pb.play()
    pb.advance(0.5)
    assert abs(tl.playhead - 1.0) < 1e-9                # 2x 速
    pb.set_speed(0.5)
    pb.advance(1.0)
    assert abs(tl.playhead - 1.5) < 1e-9                # 0.5x 速
    pb.seek(0.25)
    assert abs(tl.playhead - 0.25) < 1e-9               # 任意时刻 seek
    print('  ok: speed 2x/0.5x and arbitrary seek')


def test_ui_hit_testing():
    """UI 命中测试（staticmethod，无需 GL 实例）——回归 M3 的漏参缺陷。"""
    from presentation.timeline_ui import TimelineUI
    f = TimelineUI._in_rect
    assert f(50, 30, 12, 12, 100, 46, pad=0)       # 面板内部
    assert not f(5, 300, 12, 12, 100, 46, pad=0)   # 面板外
    assert not f(150, 30, 12, 12, 100, 46, pad=0)  # 右侧越界
    assert f(11, 11, 12, 12, 26, 26)               # pad=4 默认外扩命中
    assert not f(5, 5, 12, 12, 26, 26)             # pad 外扩仍不命中
    print('  ok: _in_rect point-in-rect with pad')


def test_model_timeline_channels():
    """M4：外部模型节点应有 translation/rotation 通道并参与采样。"""
    meta = [('ring_0', Vec3(5, 0.5, 0)), ('center', Vec3(0, 1.0, 0))]
    model_bases = [('model_0', Vec3(-3.5, 1.3, -3.5))]
    tl = build_demo_timeline(meta, model_bases, duration=4.0)
    tl.seek(1.0)
    s = tl.sample()['model_0']
    assert 'translation' in s and 'rotation' in s
    assert abs(s['translation'].x - (-3.5)) < 1e-9          # x 固定
    assert 1.3 - 1e-9 <= s['translation'].y <= 1.3 + 0.5 + 1e-9
    assert s['translation'].z == -3.5
    print('  ok: model_0 channels present, base anchored, y in bob range')


def test_obj_asset_decode():
    """M4：assets/gem.obj 应可被 pyglet 2.1.16 内置 OBJ 解码器解析（CPU 端，无窗口）。"""
    import os
    import pyglet
    path = os.path.join('assets', 'gem.obj')
    assert os.path.exists(path), 'assets/gem.obj 缺失（先跑 scripts/make_gem_obj.py）'
    scene = pyglet.model.load(path)
    assert scene is not None
    print('  ok: assets/gem.obj decoded headless ->', type(scene).__name__)


if __name__ == '__main__':
    print('test_camera_initial_pose');     test_camera_initial_pose()
    print('test_camera_clamps');           test_camera_clamps()
    print('test_camera_advance');          test_camera_advance()
    print('test_view_matrix_maps_eye_to_origin'); test_view_matrix_maps_eye_to_origin()
    print('test_look_at_matches_multiple_poses'); test_look_at_matches_multiple_poses()
    print('test_event_bus');               test_event_bus()
    print('test_quat_axis_angle_and_slerp');      test_quat_axis_angle_and_slerp()
    print('test_sampler_linear_step');            test_sampler_linear_step()
    print('test_timeline_wrap_and_sample');       test_timeline_wrap_and_sample()
    print('test_demo_timeline_choreography');     test_demo_timeline_choreography()
    print('test_playback_states');                test_playback_states()
    print('test_playback_loop_wrap');             test_playback_loop_wrap()
    print('test_playback_end_pause');             test_playback_end_pause()
    print('test_playback_speed_and_seek');        test_playback_speed_and_seek()
    print('test_ui_hit_testing');                 test_ui_hit_testing()
    print('test_model_timeline_channels');        test_model_timeline_channels()
    print('test_obj_asset_decode');               test_obj_asset_decode()
    print('ALL_LOGIC_TESTS_PASSED')

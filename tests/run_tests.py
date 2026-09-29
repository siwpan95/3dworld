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

from pyglet.math import Vec3
from logic.camera import OrbitCamera, look_at_matrix, PITCH_MIN, PITCH_MAX, DIST_MIN, DIST_MAX
from logic.events import EventBus


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
    frames, views = [], []
    bus.push_handlers(on_frame=frames.append,
                      on_view_changed=lambda: views.append(True))
    bus.dispatch_event('on_frame', 0.016)
    bus.dispatch_event('on_frame', 0.016)
    bus.dispatch_event('on_view_changed')
    assert frames == [0.016, 0.016]
    assert len(views) == 1
    print('  ok: on_frame x2, on_view_changed x1 delivered')


if __name__ == '__main__':
    print('test_camera_initial_pose');     test_camera_initial_pose()
    print('test_camera_clamps');           test_camera_clamps()
    print('test_camera_advance');          test_camera_advance()
    print('test_view_matrix_maps_eye_to_origin'); test_view_matrix_maps_eye_to_origin()
    print('test_look_at_matches_multiple_poses'); test_look_at_matches_multiple_poses()
    print('test_event_bus');               test_event_bus()
    print('ALL_LOGIC_TESTS_PASSED')

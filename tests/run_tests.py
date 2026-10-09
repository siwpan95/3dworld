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
                             quat_to_mat4, slerp)
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


def test_camera_custom_ranges():
    """M7：钳制范围可按实例放宽（太空场景要贴近黄道面、要拉得很远）。

    默认值必须原样不变——其余 demo 都依赖它（相机不入地、不贴脸）。
    """
    from logic.camera import OrbitCamera
    # 默认：与模块常量一致
    cam = OrbitCamera()
    assert cam.pitch_range == (PITCH_MIN, PITCH_MAX)
    assert cam.dist_range == (DIST_MIN, DIST_MAX)
    # 放宽：太阳系那种范围（近黄道面 + 装得下海王星轨道）
    wide = OrbitCamera(pitch_range=(2.0, 89.0), dist_range=(1.5, 200.0))
    wide.orbit(0.0, -10000.0)
    assert wide.pitch == 2.0, wide.pitch        # 能压到 2°（默认只能到 5°）
    wide.orbit(0.0, 10000.0)
    assert wide.pitch == 89.0, wide.pitch
    wide.zoom(-10000.0)
    assert wide.dist == 200.0, wide.dist        # 能拉到 200（默认 60）
    wide.zoom(10000.0)
    assert wide.dist == 1.5, wide.dist
    # 非法范围要立刻炸：pitch 上限到 90° 会让 look-at 的 up 与视线共线
    for kwargs in ({'pitch_range': (10.0, 90.0)},
                   {'pitch_range': (30.0, 10.0)},
                   {'dist_range': (5.0, 5.0)}):
        try:
            OrbitCamera(**kwargs)
        except AssertionError:
            pass
        else:
            raise AssertionError(f'{kwargs} 应被拒绝')
    print('  ok: custom pitch/dist ranges applied; defaults unchanged; bad ranges rejected')


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
    """M6：48s 五幕编排——通道齐全、时长正确、t=0 与 t=48 同帧（无缝循环）。"""
    from logic.choreography import DURATION, build_world_timeline
    meta = [(f'ring_{i}', Vec3(5, 0.5, 0)) for i in range(10)]
    meta.append(('center', Vec3(0, 1.0, 0)))
    meta += [(f'orb_{i}', Vec3(3.4, 2.2, 0)) for i in range(4)]
    tl = build_world_timeline(meta, [('model_0', Vec3(-3.5, 1.15, -3.5))])
    assert tl.duration == DURATION == 48.0
    props = {c.prop for c in tl.channels}
    assert props == {'translation', 'rotation', 'scale', 'tint', 'glow'}, props
    nodes = {c.node for c in tl.channels}
    assert 'ring_0' in nodes and 'center' in nodes and 'orb_3' in nodes
    assert 'turret' in nodes and 'seg1' in nodes and 'seg2' in nodes
    assert 'camera' in nodes and 'cam_target' in nodes and 'model_0' in nodes
    # 回绕连续：t=48 与 t=0 采样一致（位置逐分量、旋转同向）
    tl.seek(DURATION)
    s_end = tl.sample()
    tl.seek(0.0)
    s_start = tl.sample()
    for name in ('ring_3', 'center', 'orb_1', 'model_0'):
        p0, p1 = s_start[name]['translation'], s_end[name]['translation']
        assert all(abs(a - b) < 1e-6 for a, b in zip(p0, p1)), (name, p0, p1)
    for name in ('ring_3', 'center', 'orb_1'):        # 宝石是外部模型，无材质通道
        assert all(abs(a - b) < 1e-6 for a, b in
                   zip(s_start[name]['tint'], s_end[name]['tint'])), name
    print('  ok: 48s five-act timeline (5 prop kinds, 82 channels, loop-exact)')


def test_choreography_pose_continuity():
    """五幕姿态函数：端点连续 + 幕边界不跳变（逐函数直测，快照式断言）。"""
    from logic.choreography import (DURATION, center_pose, gem_pose, orb_pose,
                                    ring_pose, camera_pose, carry_weight)

    def quat_close(q0, q1):
        d = sum(a * b for a, b in zip(q0, q1))
        return abs(abs(d) - 1.0) < 1e-6

    for i in range(10):
        a, b = ring_pose(i, 0.0), ring_pose(i, DURATION)
        assert (a[0] - b[0]).length() < 1e-6 and quat_close(a[1], b[1])
        assert abs(a[3].x - b[3].x) < 1e-6 and abs(a[4] - b[4]) < 1e-6
    for fn in (center_pose, gem_pose, lambda t: orb_pose(2, t)):
        a, b = fn(0.0), fn(DURATION)
        assert (a[0] - b[0]).length() < 1e-6 and quat_close(a[1], b[1])
    e0, t0 = camera_pose(0.0)
    e1, t1 = camera_pose(DURATION)
    assert (e0 - e1).length() < 1e-6 and (t0 - t1).length() < 1e-6
    assert carry_weight(0.0) == 0.0 and carry_weight(DURATION) == 0.0
    assert carry_weight(23.0) > 0.99            # 搬运途中完全交给夹爪
    # 幕边界（10/20/30/40s）左右 1ms 的姿态差必须是小量，不是跳变
    for fn in (lambda t: ring_pose(4, t)[0], lambda t: center_pose(t)[0],
               lambda t: gem_pose(t)[0]):
        for tb in (10.0, 20.0, 30.0, 40.0):
            d = (fn(tb - 1e-3) - fn(tb + 1e-3)).length()
            assert d < 0.05, (tb, d)
    print('  ok: pose functions loop-exact and continuous across act borders')


def test_choreography_arm_ik():
    """第三幕角度由逆运动学解出：夹爪必须真的落在宝石/台座上（±0.3）。"""
    from logic.choreography import (ARM_BASE, GEM_PLACED, GEM_REST, L1, L2,
                                    SHOULDER_H, arm_pose)
    from logic.skeleton import Skeleton, build_arm, grip_world

    def grip_at(t):
        skel = Skeleton()
        build_arm(skel)
        turret, s1, s2 = arm_pose(t)
        skel.apply_sample({
            'turret': {'rotation': quat_from_axis_angle((0, 1, 0), turret)},
            'seg1': {'rotation': quat_from_axis_angle((0, 0, 1), s1)},
            'seg2': {'rotation': quat_from_axis_angle((0, 0, 1), s2)},
        })
        skel.compute()
        return grip_world(skel)

    for t, target, label in ((22.0, GEM_REST, '取件'), (26.5, GEM_PLACED, '放件')):
        d = (grip_at(t) - target).length()
        assert d < 0.30, (label, d, grip_at(t))
    # 臂长与几何常量一致（渲染几何改了这里必须同步）
    assert (L1, L2, SHOULDER_H) == (2.4, 1.8, 0.6)
    assert abs(ARM_BASE.x + 6.0) < 1e-9 and abs(ARM_BASE.z + 3.0) < 1e-9
    print('  ok: arm IK reaches gem (t=22) and plinth top (t=26.5) within 0.3')


def test_animation_material_channels():
    """M6：Channel 支持 tint/glow（材质通道），TRS 之外不误放行未知属性。"""
    from logic.animation import Channel, Sampler, Timeline
    ch_tint = Channel('n', 'tint', Sampler([0.0, 1.0], [Vec3(1, 1, 1), Vec3(2, 1, 0.5)]))
    ch_glow = Channel('n', 'glow', Sampler([0.0, 1.0], [0.0, 1.0]))
    tl = Timeline(1.0, [ch_tint, ch_glow])
    tl.seek(0.5)
    s = tl.sample()['n']
    assert abs(s['tint'].x - 1.5) < 1e-9 and abs(s['tint'].z - 0.75) < 1e-9
    assert abs(s['glow'] - 0.5) < 1e-9
    try:
        Channel('n', 'bogus', Sampler([0.0], [Vec3(0, 0, 0)]))
    except AssertionError:
        pass
    else:
        raise AssertionError('未知属性应被拒绝')
    print('  ok: tint/glow channels interpolate; unknown prop rejected')


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


def test_blocks_atlas_and_world():
    """方块世界 demo 的素材与地图**必须是确定性的**（两次运行逐位一致）。

    贴图是"生成规则"而不是图片文件 ⇒ 「同代码两次跑出同一张图」得靠测试锁住：
    一旦有人用了 `hash()`（带进程随机盐），截图与单测都会失去意义。
    """
    from logic import blocks as B

    w1, h1, px1, idx1 = B.atlas_pixels()
    w2, h2, px2, idx2 = B.atlas_pixels()
    assert (w1, h1) == (w2, h2) and px1 == px2, '图集两次生成不一致（确定性被破坏）'
    assert h1 == B.TILE and w1 == B.TILE * len(idx1), (w1, h1, len(idx1))
    assert set(idx1) == set(B.atlas_tiles()), '格名与图集不一致'
    assert len(set(idx1.values())) == len(idx1), '格号重复'
    for faces in B.BLOCK_FACES.values():
        for name in faces:
            assert name in idx1, name
    for name in B.atlas_tiles():
        data = B.tile_pixels(name)
        assert len(data) == B.TILE * B.TILE * 4, name
        if name == 'torch':
            continue                      # 火把本来就大面积透明，跳过
        assert len(set(data[i:i + 3] for i in range(0, len(data), 4))) > 1, \
            f'{name} 贴图是单色（生成规则没生效）'

    bl1, hh1, tr1, hs1 = B.generate_world()
    bl2, hh2, tr2, hs2 = B.generate_world()
    assert bl1 == bl2 and hh1 == hh2 and tr1 == tr2 and hs1 == hs2, '世界两次生成不一致'
    assert len(bl1) == B.WORLD_W * B.MAX_H * B.WORLD_W
    assert len(hh1) == B.WORLD_W * B.WORLD_W
    assert all(B.SEA_FLOOR <= h <= B.MAX_H - 4 for h in hh1), '地表高度越界'
    # 水必须真的存在：第一版地形整体高于水线，水面网格 0 面，湖一滴没有
    below = sum(1 for h in hh1 if h < B.WATER_LEVEL)
    assert below > 40, f'水下只有 {below} 列：水线低于地形，湖会消失'
    assert len(tr1) > 5, '树太少'
    assert len(hs1) == 3, '村庄应有 3 栋小屋'
    cx = cz = B.WORLD_W // 2
    ground = hh1[cz * B.WORLD_W + cx]
    assert ground > B.WATER_LEVEL, '村庄被水淹了'
    for dz in (-4, 0, 4):
        for dx in (-4, 0, 4):
            assert hh1[(cz + dz) * B.WORLD_W + cx + dx] == ground, '村庄台地没压平'

    import array
    pos, col, uv = array.array('f'), array.array('f'), array.array('f')
    faces, verts = B.mesh_world(bl1, idx1, pos=pos, col=col, uv=uv)
    assert len(pos) == verts * 3 and len(col) == verts * 4 and len(uv) == verts * 2, \
        (len(pos), len(col), len(uv), verts)
    assert faces == verts // 6, (faces, verts)
    solid = sum(1 for b in bl1 if b not in (0, B.WATER))
    assert faces < solid * 6 * 0.5, f'面剔除没生效：{faces} 面 vs {solid} 个方块'
    wpos = array.array('f')
    wfaces, _ = B.mesh_world(bl1, idx1, want_water=True, pos=wpos)
    assert wfaces > 100, f'水面只有 {wfaces} 面'
    print(f'  ok: 图集 {w1}x{h1}/{len(idx1)} 格 · 世界 {len(tr1)} 树 {len(hs1)} 屋 · '
          f'面 {faces}(水 {wfaces}) 剔除率 {100 * (1 - faces / (solid * 6)):.0f}%')


def test_blocks_sky_and_acts():
    """昼夜曲线与五幕：日出→正午→日落→入夜，且 t=0 与 t=48 同帧（循环无缝）。"""
    from logic import blocks as B

    dawn = B.sky_state(B.day_phase(0.0))
    noon = B.sky_state(B.day_phase(20.0))
    dusk = B.sky_state(B.day_phase(40.0))
    night = B.sky_state(B.day_phase(45.0))
    assert dawn['sun_visible'], '日出时太阳应当可见'
    assert noon['ambient'] > 0.95, f'正午天光应接近 1，实际 {noon["ambient"]}'
    assert night['ambient'] < 0.4, f'夜里天光应压暗，实际 {night["ambient"]}'
    assert night['stars'] > 0.5 > dawn['stars'], '星星应只在夜里出现'
    assert noon['sun'][1] > 0.9 > dusk['sun'][1] > -0.2 > night['sun'][1], \
        (noon['sun'][1], dusk['sun'][1], night['sun'][1])
    # 相位编排：日落必须落在第五幕（第一版用 t/DURATION，日落掉到第三幕 = 建造在夜里）
    assert B.act_at(40.0)[1].endswith('日落'), B.act_at(40.0)
    assert B.act_at(20.0)[1].endswith('建造'), B.act_at(20.0)
    assert abs(B.day_phase(0.0) - B.day_phase(B.DURATION)) < 1e-9, '昼夜相位不闭环'
    e0, g0 = B.camera_pose(0.0)
    e1, g1 = B.camera_pose(B.DURATION)
    assert e0 == e1 and g0 == g1, '相机首尾帧不同 ⇒ 循环会跳'
    assert len(B.ACTS) == 5, '五幕'
    for t in (0.0, 7.3, 19.9, 33.3, 47.9):
        s = B.sky_state(B.day_phase(t))['sun']
        assert abs(sum(v * v for v in s) - 1.0) < 1e-6, (t, s)
    print(f'  ok: 日出/正午/日落/夜间天光 {dawn["ambient"]:.2f}/{noon["ambient"]:.2f}/'
          f'{dusk["ambient"]:.2f}/{night["ambient"]:.2f} · 相位闭环 · 五幕')


def test_blocks_camera_and_mobs():
    """相机净空（不许插进方块、不许被贴着挡住）与生物步态。

    净空检查在本轮抓过三类真缺陷：镜头钻进树冠、注视点被屋顶挡、土路穿过房子。
    这类问题只有出了截图才看得见 ⇒ 必须机械化。
    """
    from logic import blocks as B

    bl, hh, tr, hs = B.generate_world()
    bad = B.camera_clearance(bl)
    assert not bad, f'相机净空违规 {len(bad)} 处，例如 {bad[:3]}'

    p0 = B.mob_pose('pig', 1.0)
    assert abs(p0['leg_front_l'] + p0['leg_front_r']) < 1e-9, '同侧前后腿应反相'
    assert abs(p0['leg_front_l'] - p0['leg_back_r']) < 1e-9, '对角腿应同相'
    angles = [B.mob_pose('pig', t / 20.0)['leg_front_l'] for t in range(40)]
    assert max(angles) > 20 and min(angles) < -20, '摆幅太小，看不出在走'
    cr = B.mob_pose('creeper', 1.0)
    assert abs(cr['leg_front_l']) < abs(p0['leg_front_l']), '爬行者步幅应更小'
    for t in range(0, 40):
        assert abs(B.mob_pose('pig', t / 10.0)['body_y']) <= 0.05, '起伏过大'

    times = [t for t, _ in B.build_schedule(104)]
    assert times == sorted(times), '方块落下的顺序不是时间序'
    assert min(times) >= 20.0 and max(times) <= 28.01, (min(times), max(times))
    shape = B.build_house_shape()
    assert len(shape) > 60, len(shape)
    xs = [b[0] for b in shape]
    assert max(xs) - min(xs) >= 4, '拼出来的房子太小'
    print(f'  ok: 相机净空 0 违规 · 步态摆幅 {max(angles):.0f}° · '
          f'拼装 {len(shape)} 块')


def test_model_timeline_channels():
    """M4/M6：外部模型（宝石）应有 translation/rotation 通道，且被夹持时段
    的位置由 carry_weight 交给夹爪——这里验证"静置点"与"落座点"两个关键帧。"""
    from logic.choreography import (DURATION, GEM_PLACED, GEM_REST,
                                    build_world_timeline, carry_weight)
    tl = build_world_timeline([('ring_0', Vec3(5, 0.5, 0))],
                              [('model_0', GEM_REST)])
    tl.seek(0.0)
    p0 = tl.sample()['model_0']['translation']
    assert all(abs(a - b) < 1e-6 for a, b in zip(p0, GEM_REST)), p0
    tl.seek(30.0)                                  # 已落座台座
    p1 = tl.sample()['model_0']['translation']
    assert all(abs(a - b) < 1e-6 for a, b in zip(p1, GEM_PLACED)), p1
    assert carry_weight(30.0) == 0.0               # 落座后不再被夹持
    tl.seek(DURATION)
    p2 = tl.sample()['model_0']['translation']
    assert all(abs(a - b) < 1e-6 for a, b in zip(p2, GEM_REST)), p2
    print('  ok: gem rest -> placed -> rest across the 48s show')


def test_obj_asset_decode():
    """M4：assets/gem.obj 应可被 pyglet 2.1.16 内置 OBJ 解码器解析（CPU 端，无窗口）。"""
    import os
    import pyglet
    path = os.path.join('assets', 'gem.obj')
    assert os.path.exists(path), 'assets/gem.obj 缺失（先跑 scripts/make_gem_obj.py）'
    scene = pyglet.model.load(path)
    assert scene is not None
    print('  ok: assets/gem.obj decoded headless ->', type(scene).__name__)


def test_skeleton_hierarchy():
    """M5/M6：父节点旋转必须带动子节点世界原点（骨骼继承的核心性质）。

    机械臂现在是四级：转台(Y) → 大臂(Z) → 小臂(Z) → 夹爪。
    """
    from logic.skeleton import Skeleton, build_arm, grip_world
    skel = Skeleton()
    build_arm(skel, base=Vec3(-6, 0, -3))
    assert skel.nodes['seg1'].parent is skel.nodes['turret']
    assert skel.nodes['seg2'].parent is skel.nodes['seg1']
    assert skel.nodes['grip'].parent is skel.nodes['seg2']
    # 大臂绕 Z 转 90°：子节点（肘关节，局部 (0,2.4,0)）应被带到 (-6-2.4, 0.6, -3)
    skel.apply_sample({'seg1': {'rotation': quat_from_axis_angle((0, 0, 1), 90.0)}})
    skel.compute()
    wt = skel.nodes['seg2'].world_translation()
    assert all(abs(a - b) < 1e-6 for a, b in zip(wt, (-8.4, 0.6, -3.0))), wt
    # 未旋转时：夹爪世界原点 = base + (0, 0.6+2.4+1.8, 0)
    skel2 = Skeleton(); build_arm(skel2, base=Vec3(-6, 0, -3)); skel2.compute()
    g = grip_world(skel2)
    assert all(abs(a - b) < 1e-6 for a, b in zip(g, (-6.0, 4.8, -3.0))), g
    # 转台 90° + 大臂 90° 的复合：大臂先把 (0,4.2,0) 折到局部 -X，
    # 转台再把这个方向转到世界 +Z（pyglet 约定：绕 Y 的 90° 把 +X 映到 -Z）
    skel3 = Skeleton(); build_arm(skel3, base=Vec3(-6, 0, -3))
    skel3.apply_sample({'turret': {'rotation': quat_from_axis_angle((0, 1, 0), 90.0)},
                        'seg1': {'rotation': quat_from_axis_angle((0, 0, 1), 90.0)}})
    skel3.compute()
    g3 = grip_world(skel3)
    assert all(abs(a - b) < 1e-6 for a, b in zip(g3, (-6.0, 0.6, 1.2))), g3
    print('  ok: 4-level arm hierarchy propagates parent rotation to gripper')


def test_arm_channels():
    """M6：机械臂通道来自 choreography（转台绕 Y、两段绕 Z），且首尾同姿态。"""
    from logic.choreography import DURATION, arm_pose, build_world_timeline
    chs = build_world_timeline([], [])
    arm = [c for c in chs.channels if c.node in ('turret', 'seg1', 'seg2')]
    assert {c.node for c in arm} == {'turret', 'seg1', 'seg2'}
    assert all(c.prop == 'rotation' for c in arm)
    a0, a1 = arm_pose(0.0), arm_pose(DURATION)
    assert all(abs(x - y) < 1e-6 for x, y in zip(a0, a1)), (a0, a1)
    # 静止幕：只有摆动（±7° 以内）；第三幕：明显大角度动作
    assert max(abs(v) for v in arm_pose(5.0)) < 7.0, arm_pose(5.0)
    assert max(abs(v) for v in arm_pose(24.0)) > 40.0, arm_pose(24.0)
    print('  ok: arm channels (turret/seg1/seg2), idle sway vs act-III reach')


def test_kkrieger_timeline():
    """M6+扩展：kkrieger 150s 六幕——相机键帧、场景对象循环连续、材质通道。"""
    from logic.kkrieger import (ACTS, CAM, CAM_TGT, DURATION, KNOT,
                                build_kkrieger_timeline)
    meta = ([('monument_ring_%d' % i, Vec3(0, 4.4, -66)) for i in range(5)]
            + [('monument_core', Vec3(0, 4.4, -66)), ('beam', Vec3(0, 4.4, -66)),
               ('holo_frame', Vec3(0, 4.4, -66)), ('lift', Vec3(0, 0.15, -58)),
               ('atrium_fan', Vec3(0, 8.4, -70))]
            + [('door%d_%s' % (i, t), Vec3(0, 0, -14.0 * (i + 1)))
               for i in range(3) for t in ('a', 'b')]
            + [('strip_%d' % i, Vec3(0, 4.18, -2 - 4 * i)) for i in range(14)]
            + [('alight_%d' % i, Vec3(0, 8.65, -61 - 10 * (i % 2))) for i in range(4)]
            + [('strobe_%d' % i, Vec3(2.9, 3.05, -6 - 6.5 * i)) for i in range(8)]
            + [('fan_%d' % i, Vec3(2.72, 2.3, -8 - 12 * i)) for i in range(4)]
            + [('drone_%d' % i, Vec3(0, 2.6, -4)) for i in range(3)]
            + [('conduit_%d' % i, Vec3(2.94, 0.62, -6 - 9 * i)) for i in range(6)]
            + [('spark_%d' % i, Vec3(0, 4.4, -66)) for i in range(8)]
            + [('knot', Vec3(*KNOT)), ('portal_ring', Vec3(0, 2.25, -76))]
            + [('seed_%d' % i, Vec3(*KNOT)) for i in range(6)]
            + [('sconduit_%d' % i, Vec3(0, 0, 0)) for i in range(4)])
    tl = build_kkrieger_timeline(meta)
    assert tl.duration == DURATION == 150.0
    assert len(ACTS) == 6 and ACTS[0][0] == 0.0 and ACTS[3][0] == 72.0 \
        and ACTS[4][0] == 96.0 and ACTS[5][0] == 118.0
    tl.seek(0.0)
    s0 = tl.sample()
    assert CAM in s0 and CAM_TGT in s0
    assert s0[CAM]['translation'] == Vec3(0, 9.0, 12.0)      # 开场高空位
    # 循环连续：t=150 与 t=0 的旋转/平移/材质一致
    tl.seek(DURATION)
    s1 = tl.sample()
    for name in ('monument_ring_0', 'monument_ring_4', 'monument_core', 'beam',
                 'holo_frame', 'lift', 'atrium_fan', 'door0_a', 'strip_0',
                 'alight_2', 'strobe_3', 'fan_1', 'drone_0', 'drone_2',
                 'conduit_0', 'spark_3', 'knot', 'seed_0', 'seed_5',
                 'sconduit_1', 'portal_ring'):
        q0, q1 = s0[name].get('rotation'), s1[name].get('rotation')
        if q0 is not None:
            if sum(a * b for a, b in zip(q0, q1)) < 0:
                q1 = tuple(-c for c in q1)
            assert all(abs(a - b) < 1e-6 for a, b in zip(q0, q1)), (name, 'rot')
        t0, t1 = s0[name].get('translation'), s1[name].get('translation')
        if t0 is not None:
            assert all(abs(a - b) < 1e-6 for a, b in zip(t0, t1)), (name, 'pos')
        g0, g1 = s0[name].get('glow'), s1[name].get('glow')
        if g0 is not None:
            assert abs(g0 - g1) < 1e-6, (name, 'glow')
    # 门：开场关、第一幕全开、撤离后重新关上
    from logic.kkrieger import DOOR_CLOSE_T, DOOR_OPEN_T
    for i in range(3):
        tl.seek(0.0)
        assert tl.sample()[f'door{i}_a']['translation'].x == 0.0
        tl.seek(DOOR_OPEN_T[i] + 2.5)
        assert abs(tl.sample()[f'door{i}_a']['translation'].x) > 1.5
        tl.seek(DOOR_CLOSE_T[i] + 2.5)
        assert abs(tl.sample()[f'door{i}_a']['translation'].x) < 0.01
        # 第五幕重启突入：舱门速开速闭（开/闭都晚于镜头过门时刻）
        from logic.kkrieger import V_DOOR_CLOSE_T, V_DOOR_OPEN_T
        tl.seek(V_DOOR_OPEN_T[i] + 1.5)
        assert abs(tl.sample()[f'door{i}_a']['translation'].x) > 1.5
        tl.seek(V_DOOR_CLOSE_T[i] + 1.5)
        assert abs(tl.sample()[f'door{i}_a']['translation'].x) < 0.01
    # 升降平台：第二幕升起、第四幕落回原位（否则循环回绕会瞬移）
    tl.seek(20.0)
    y_rest = tl.sample()['lift']['translation'].y
    tl.seek(40.0)
    assert tl.sample()['lift']['translation'].y > y_rest + 2.0
    tl.seek(96.0)
    assert abs(tl.sample()['lift']['translation'].y - y_rest) < 1e-6
    # 灯带：过载增亮 → 撤离熄灭 → 尾段回位
    tl.seek(60.0)
    lit = tl.sample()['strip_3']['glow']
    tl.seek(86.0)
    dark = tl.sample()['strip_3']['glow']
    tl.seek(96.0)
    back = tl.sample()['strip_3']['glow']
    assert lit > 0.3 and dark < 0.1 and abs(back - 1.15) < 1e-6, (lit, dark, back)
    # 第五幕：核心爆发白闪（glow 尖峰）+ 碎片飞散后归位
    tl.seek(117.6)
    assert tl.sample()['monument_core']['glow'] > 2.0
    tl.seek(115.0)
    assert tl.sample()['spark_0']['glow'] > 0.5
    tl.seek(150.0)
    assert tl.sample()['spark_0']['glow'] == 0.0
    # 第六幕：反应堆点火过载 + 种子收拢 + 门洞光环穿越高亮
    tl.seek(140.9)
    s = tl.sample()
    assert s['knot']['glow'] > 2.0, s['knot']['glow']
    d = s['seed_0']['translation']
    assert math.hypot(d.x - KNOT[0], d.z - KNOT[2]) < 1.2, d   # 收拢到反应堆
    tl.seek(118.5)
    assert tl.sample()['portal_ring']['glow'] > 1.0
    tl.seek(125.0)
    assert tl.sample()['knot']['glow'] > 0.5                    # 圣殿段已被点亮
    # 无人机折返巡逻始终在走廊/大厅范围内（第五幕收拢段单独校验）
    for i in range(0, 96, 3):
        tl.seek(float(i))
        d0 = tl.sample()['drone_0']['translation']
        assert -52.5 <= d0.z <= -3.5, (i, d0.z)
    tl.seek(112.0)
    d0 = tl.sample()['drone_0']['translation']
    assert math.hypot(d0.x, d0.z + 66.0) < 5.5, d0      # 收拢到纪念碑附近
    print('  ok: kkrieger 150s six acts (cam keys, loop-exact, doors, lift, '
          'lights, act-V finale, act-VI sanctum)')


def test_nordic_saga_timeline():
    """诺德之境 60s 五幕：相机/龙轨迹/符文/周期件全链路（无窗口）。"""
    from logic import nordic_saga as ns
    from core.nordic_scene import DRAGON_HOVER
    assert ns.DURATION == 60.0
    assert len(ns.ACTS) == 5 and ns.ACTS[0][0] == 0.0 and ns.ACTS[4][0] == 50.0
    # 相机循环闭合：t=0 与 t=60 完全一致
    e0, g0 = ns.camera_pose(0.0)
    e1, g1 = ns.camera_pose(60.0)
    gap = (max(abs(a - b) for a, b in zip((e0.x, e0.y, e0.z),
                                          (e1.x, e1.y, e1.z)))
           + max(abs(a - b) for a, b in zip((g0.x, g0.y, g0.z),
                                            (g1.x, g1.y, g1.z))))
    assert gap < 1e-9, gap
    # 周期性部件闭合（周期整除 60）
    for f, name in ((ns.campfire_scale, 'campfire'), (ns.rune_beam, 'beam'),
                    (ns.stars_bright, 'stars'), (ns.window_bright, 'win'),
                    (ns.moon_bright, 'moon'), (ns.fire_light, 'firelight')):
        assert abs(f(0.0) - f(60.0)) < 1e-9, name
    for k in range(4):
        a, b = ns.aurora_pose(k, 0.0), ns.aurora_pose(k, 60.0)
        assert abs(a['bright'] - b['bright']) < 1e-9
    # 龙：入场前/出场后都在画面外（距场景中心 > 55m）
    for t in (0.0, 10.0, 20.0, 60.0):
        p = ns.dragon_position(t)
        d = math.sqrt(p.x ** 2 + (p.z + 40.0) ** 2)
        assert d > 55.0, (t, p)
    # 悬停段：对准场景里的悬停位、朝向龙语墙（-z）
    h = ns.dragon_heading(43.5)
    assert h[2] < -0.9, h
    p = ns.dragon_position(43.5)
    for a, b in zip((p.x, p.y, p.z), DRAGON_HOVER):
        assert abs(a - b) < 0.6, (p, DRAGON_HOVER)
    # 翼扑动：相位在首尾整圈（sin 归零）
    assert abs(ns.wing_angle(0.0)) < 1e-9 and abs(ns.wing_angle(60.0)) < 1e-9
    # 龙淡入淡出：首尾完全透明、中段实体
    assert ns.dragon_alpha(0.0) == 0.0 and ns.dragon_alpha(60.0) == 0.0
    assert ns.dragon_alpha(35.0) == 1.0
    # 龙焰窗口（与 FIRE_T0/T1 一致）
    assert ns.fire_scale(40.0) < 0.1
    assert ns.fire_scale(46.5) > 1.0
    assert ns.fire_scale(52.0) < 0.1
    # 符文：喷火前不亮 → 点亮后强 → 黎明熄灭 → 尾帧归零
    assert ns.rune_glow(30.0) == 0.0
    assert ns.rune_glow(50.0) > 1.8
    assert ns.rune_glow(54.0) > 1.5
    assert ns.rune_glow(58.5) < 1.0
    assert ns.rune_glow(60.0) < 1e-6
    # 光柱随点亮升起、黎明前归零
    assert ns.rune_beam(40.0) == 0.0
    assert ns.rune_beam(50.0) > 0.9
    assert ns.rune_beam(60.0) < 1e-6
    print('  ok: nordic saga 60s timeline (loop-exact cam, dragon hover/takeoff, '
          'rune ignition, periodic parts)')


def test_nordic_camera_path_clearance():
    """诺德之境镜头净空：不穿地、不撞龙语墙/石柱/拱门，且不贴脸巨龙。"""
    from logic import nordic_saga as ns
    from core.nordic_scene import COL_ROWS, COL_X
    t = 0.0
    while t <= ns.DURATION:
        eye, _ = ns.camera_pose(t)
        assert eye.y > 1.0, (t, eye.y)               # 掠地不得入地
        # 龙语墙矩形（z=-43，9.4×5.4×1.4）不得穿入
        if abs(eye.z + 43.0) < 1.3:
            assert not (abs(eye.x) < 5.6 and eye.y < 6.4), (t, eye)
        # 石柱两排：水平距离 > 1.0
        for z0, _h in COL_ROWS:
            if abs(eye.z - z0) < 2.6:
                for sx in (-1.0, 1.0):
                    assert math.hypot(eye.x - sx * COL_X, eye.z - z0) > 1.0, (t, eye)
        # 左拱门（x=-11.6, z=-30）：过门时在门洞中间
        if abs(eye.z + 30.0) < 1.2 and abs(eye.x + 11.6) < 3.5:
            assert abs(eye.x + 11.6) < 1.5, (t, eye)
        # 巨龙：相机与龙保持距离（防"贴脸翼膜糊满屏"）
        dp = ns.dragon_position(t)
        d = math.sqrt((eye.x - dp.x) ** 2 + (eye.y - dp.y) ** 2
                      + (eye.z - dp.z) ** 2)
        assert d > 4.0, (t, tuple(eye), tuple(dp))
        t += 0.2
    print('  ok: nordic camera clears ground, word wall, columns and dragon')


def test_nordic_scene_geometry():
    """诺德之境静态几何：无窗口构建、UV 完整、规模合理（回归 merge 丢 UV）。"""
    from core import nordic_scene as nsc
    acc = {}
    nsc._add_ground(acc)
    nsc._add_mountains(acc)
    nsc._add_forest(acc)
    nsc._add_longhouse(acc)
    nsc._add_hut(acc)
    nsc._add_ruins(acc)
    nsc._add_campfire(acc)
    nsc._add_details(acc)
    from engine.geometry import merge_meshes
    total = 0
    tris = 0
    for key, mds in acc.items():
        for md in mds:
            assert md.vertex_count > 0, key
            assert len(md.uvs) >= md.vertex_count * 2, (key, md.name)
            total += md.vertex_count
            idx = md.indices if md.indices else range(md.vertex_count)
            tris += len(list(idx)) // 3
        m = merge_meshes(mds)                        # 合批后 UV 不得丢
        assert len(m.uvs) == m.vertex_count * 2, key
    assert 10000 < total < 400000, total
    # 龙的部件可构建
    body = nsc._dragon_body_mesh()
    wing = nsc._dragon_wing_mesh()
    assert body.triangle_count > 100 and wing.triangle_count > 8
    print(f'  ok: nordic scene geometry ({total} verts / {tris} tris, '
          f'{len(acc)} materials, UV kept through merge)')


def test_kkrieger_camera_path_clearance():
    """镜头不得穿进场景实体：抽查机位与立柱/门框/地面/顶棚的间距。

    这一条是给"看着还行"的运镜兜底的——早期机身位就卡在大厅立柱里。
    """
    from logic.kkrieger import camera_pose, DURATION
    from core.kkrieger_scene import (ATRIUM_H, CORRIDOR_H, CORRIDOR_W, DOOR_Z,
                                     MONUMENT, ATRIUM_Z0)
    columns = [(-5.5, -60), (5.5, -60), (-5.5, -72), (5.5, -72), (-7.0, -66), (7.0, -66)]
    # 两次"白闪传送"是全屏爆亮遮蔽下的刻意剪辑（117.9→118.2 进圣殿、
    # 141.3→141.8 回开场）——相机沿直线路径穿越墙体，但观众不可见，
    # 这两段窗口豁免间隙校验
    teleports = ((117.9, 118.2), (141.3, 141.8))
    t = 0.0
    while t <= DURATION:
        if any(a < t < b for a, b in teleports):
            t += 0.25
            continue
        eye, _ = camera_pose(t)
        # 高度：在地面之上；进入建筑后不得穿顶棚（z>0 是入口外的高空开场位）
        assert eye.y > 0.6, (t, eye.y)
        if eye.z <= 0.0:
            limit = ATRIUM_H if eye.z < ATRIUM_Z0 else CORRIDOR_H
            assert eye.y < limit - 0.15, (t, eye.y, limit)
        # 立柱：水平距离 > 0.9（柱半宽 0.55 + 余量）
        for cx, cz in columns:
            if abs(eye.z - cz) < 3.0:
                assert math.hypot(eye.x - cx, eye.z - cz) > 0.9, (t, eye, cx, cz)
        # 舱门：过门时必须在门洞内（|x| < 1.45）
        for dz in DOOR_Z:
            if abs(eye.z - dz) < 0.35:
                assert abs(eye.x) < 1.4, (t, eye, dz)
        # 纪念碑：常态保持在外接球外（最大环半径 2.7 + 余量）；
        # 第三幕结尾的"贴脸"俯冲允许逼近，但不得进入核心立方体内部。
        # 用三维距离而不是"到轴线的水平距离"——环绕时镜头会从正面横切，
        # 水平距离必然一度变小，但那并不代表穿模。
        d = (eye - Vec3(*MONUMENT)).length()
        assert d > (0.75 if t >= 63.0 else 2.9), (t, d)
        t += 0.25
    print('  ok: camera path clears columns, doorways, monument and ceiling')


def test_box_data_pivot():
    from core.geometry import box_data
    v, c = box_data(0.4, 2.4, 0.4, 0, 1.2, 0, (1, 1, 1))
    assert len(v) == 108 and len(c) == 144        # position vec3 / colors vec4
    ys = v[1::3]                                  # y 分量
    assert abs(min(ys) - 0.0) < 1e-9 and abs(max(ys) - 2.4) < 1e-9  # 枢轴在底部
    print('  ok: pivot box spans y in [0, 2.4]')


def test_boids_flock():
    """demos/boids 的逻辑层：可复现、有界、限速、捕食者会把鸟群推开。"""
    import time
    from logic.boids import Flock

    def run(seed, steps=40):
        f = Flock(count=60, bounds=30.0, seed=seed)
        for _ in range(steps):
            f.step(1 / 60.0)
        return [tuple(b.pos) for b in f.boids]

    a1, a2 = run(1234), run(1234)
    assert all(abs(x - y) < 1e-9 for p1, p2 in zip(a1, a2) for x, y in zip(p1, p2)), \
        '固定种子必须完全可复现'
    b = run(4321)
    assert any(abs(x - y) > 1e-6 for p1, p2 in zip(a1, b) for x, y in zip(p1, p2)), \
        '不同种子应产生不同轨迹'

    f = Flock(count=80, bounds=25.0, seed=7)
    t0 = time.perf_counter()
    for _ in range(120):
        f.step(1 / 60.0)
    ms = (time.perf_counter() - t0) / 120 * 1000
    assert f.spread() <= 25.0 * 2 + 1e-6, f.spread()        # 不出界
    assert max(b.vel.length() for b in f.boids) <= f.max_speed + 1e-6, '速度必须限幅'

    # 捕食者：放到群心，若只鸟应被推开（离捕食者更远）
    center = Vec3(sum(b.pos.x for b in f.boids) / len(f.boids), 0.0,
                  sum(b.pos.z for b in f.boids) / len(f.boids))
    f.set_predator(center)
    assert f.predator is not None
    before = sum((b.pos - center).length() for b in f.boids) / len(f.boids)
    for _ in range(60):
        f.step(1 / 60.0)
    after = sum((b.pos - center).length() for b in f.boids) / len(f.boids)
    f.set_predator(None)
    assert f.predator is None
    assert after > before, (before, after)
    print(f'  ok: boids deterministic/bounded/speed-capped (step={ms:.2f}ms, '
          f'scatter {before:.1f}->{after:.1f})')


def test_waves_math():
    """demos/water_waves 的逻辑层：Gerstner 波的高度/梯度/法线自洽，且 CPU 侧
    与着色器共用同一套公式（这里锁死解析性质）。"""
    from logic.waves import (default_waves, gerstner_gradient, gerstner_height,
                             gerstner_normal, sample_surface, scaled_preset,
                             wave_presets, Wave)

    waves = default_waves()
    assert len(waves) == 8
    # 数值梯度 vs 解析梯度（中心差分），容差按振幅量级给
    x, z, t = 1.7, -0.9, 3.1
    h = 1e-4
    gx_num = (gerstner_height(x + h, z, t, waves) - gerstner_height(x - h, z, t, waves)) / (2 * h)
    gz_num = (gerstner_height(x, z + h, t, waves) - gerstner_height(x, z - h, t, waves)) / (2 * h)
    gx, gz = gerstner_gradient(x, z, t, waves)
    assert abs(gx - gx_num) < 1e-6 and abs(gz - gz_num) < 1e-6, (gx, gx_num, gz, gz_num)
    # 法线必须是单位向量，且 (-gx, 1, -gz) 归一化
    nx, ny, nz = gerstner_normal(x, z, t, waves)
    assert abs(nx * nx + ny * ny + nz * nz - 1.0) < 1e-9
    assert ny > 0.0                                   # 法线朝上
    hh, nn = sample_surface(x, z, t, waves)
    assert abs(hh - gerstner_height(x, z, t, waves)) < 1e-12 and nn == (nx, ny, nz)
    # 单个分量的时间周期性：推进自身周期 2π/ω 后回到原值
    w0 = waves[0]
    period = 2 * math.pi / w0.omega
    assert abs(gerstner_height(x, z, t, [w0])
               - gerstner_height(x, z, t + period, [w0])) < 1e-9
    # 深水色散关系 w = sqrt(g·k)：波长越长，角频率越小（波越"慢"）
    assert waves[0].omega < waves[-1].omega
    assert abs(w0.omega - math.sqrt(9.81 * w0.wavenumber)) < 1e-12
    # 预设：3 档强度递增（RMS 高度），波数递增
    names = [p[0] for p in wave_presets()]
    assert len(names) == 3
    rms = []
    for i in range(3):
        _name, wl = scaled_preset(i)
        acc = sum(gerstner_height(0.3 * k, 0.7 * k, 0.5, wl) ** 2 for k in range(200))
        rms.append(math.sqrt(acc / 200))
        assert all(isinstance(w, Wave) for w in wl)
    assert rms[0] < rms[1] < rms[2], rms
    # 非法输入要炸，不能静默产出错数据（非正波长 / 零方向向量）
    for bad_wavelength in (0.0, -2.0):
        try:
            Wave(0.5, bad_wavelength)
        except ValueError:
            pass
        else:
            raise AssertionError(f'波长 {bad_wavelength} 应抛 ValueError')
    try:
        Wave(0.5, 3.0, (0.0, 0.0))
    except ValueError:
        pass
    else:
        raise AssertionError('零方向向量应抛 ValueError')
    print(f'  ok: waves analytic gradient==numeric, unit normal, presets '
          f'RMS {rms[0]:.2f}<{rms[1]:.2f}<{rms[2]:.2f}')


def test_terrain_row_emitter():
    """地形的逐行三角形装配（渐进重建的核心）：行序拼接 == 整块拼接。

    这是"渐进重建不会改变结果"的数学保证——每帧啃几行，与一次性建完的
    顶点/颜色数组必须逐位相同，否则重建完的地形会与同步版本不一致。
    """
    import array
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'demos'))
    from terrain_flyover import (LIGHT_DIR, _hash01, emit_row, height_at,
                                 terrain_color)

    n, step = 8, 2.0
    ox = oz = -0.5 * (n - 1) * step
    seed = 4242
    hs = [height_at(ox + i * step, oz + j * step, seed)
          for j in range(n) for i in range(n)]
    # 顶点色（与 demo 内 _vertex_color 同式，独立算一遍做交叉验证）
    lx, ly, lz = LIGHT_DIR
    ll = math.sqrt(lx * lx + ly * ly + lz * lz)
    cols = []
    for j in range(n):
        for i in range(n):
            k = j * n + i
            il, ir = max(0, i - 1), min(n - 1, i + 1)
            jd, ju = max(0, j - 1), min(n - 1, j + 1)
            dx = (hs[j * n + ir] - hs[j * n + il]) / ((ir - il) * step)
            dz = (hs[ju * n + i] - hs[jd * n + i]) / ((ju - jd) * step)
            nx, ny, nz = -dx, 1.0, -dz
            nl = math.sqrt(nx * nx + ny * ny + nz * nz)
            lam = max(0.0, (nx * lx + ny * ly + nz * lz) / (nl * ll))
            r, g, b = terrain_color(hs[k], 1.0 - 1.0 / nl, lam,
                                    _hash01(i, j, seed + 4241))
            cols.append((r, g, b, 1.0))
    # 逐行（渐进路径）
    pos_a, col_a = array.array('f'), array.array('f')
    for j in range(n - 1):
        emit_row(pos_a, col_a, hs, cols, n, step, ox, oz, j)
    # 逐格（朴素路径，直接展开四边形）
    pos_b, col_b = array.array('f'), array.array('f')
    for j in range(n - 1):
        z0, z1 = oz + j * step, oz + (j + 1) * step
        for i in range(n - 1):
            x0, x1 = ox + i * step, ox + (i + 1) * step
            a, b = j * n + i, j * n + i + 1
            c, d = a + n, b + n
            pos_b.extend((x0, hs[a], z0, x1, hs[b], z0, x1, hs[d], z1,
                          x0, hs[a], z0, x1, hs[d], z1, x0, hs[c], z1))
            for k in (a, b, d, a, d, c):
                col_b.extend(cols[k])
    assert pos_a == pos_b and col_a == col_b, '逐行拼接必须与朴素拼接逐位一致'
    assert len(pos_a) == (n - 1) ** 2 * 18 and len(col_a) == (n - 1) ** 2 * 24
    # 颜色必须是 4 元组：3 元组会让顶点缓冲少 1/4 的 float（踩过一次）
    assert all(len(c) == 4 for c in cols)
    # 高程确定性：同 seed 同点同值，换 seed 变值
    assert height_at(3.0, -7.0, seed) == height_at(3.0, -7.0, seed)
    assert height_at(3.0, -7.0, seed) != height_at(3.0, -7.0, seed + 1)
    print(f'  ok: terrain row emitter == naive ({len(pos_a)} floats), '
          f'colors vec4, deterministic heights')


def test_voxel_mesh_invariants():
    """体素世界（R 键换种子的底层）：换种子真的换世界，且面剔除必须成立。

    纯函数层直测（build_heights / build_mesh 不需要 GL），
    这样"R 键重建"这条交互也有回归保护。
    """
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'demos'))
    from voxel_terrain import MAXH, W, build_heights, build_mesh

    h1 = build_heights(1234)
    h2 = build_heights(1234)
    h3 = build_heights(5678)
    assert h1 == h2, '同种子必须完全可复现'
    assert h1 != h3, '换种子必须真的换地形'
    assert len(h1) == W * W and all(1 <= h <= MAXH for h in h1), \
        (len(h1), min(h1), max(h1))

    pos_c, col_c, faces_c = build_mesh(h1, 1234, True)     # 剔除（只画暴露面）
    pos_a, col_a, faces_a = build_mesh(h1, 1234, False)    # 全部面
    assert faces_c < faces_a, (faces_c, faces_a)
    assert len(pos_c) // 3 == faces_c * 6 and len(pos_a) // 3 == faces_a * 6
    assert len(col_c) == faces_c * 24 and len(col_a) == faces_a * 24
    cut = 1.0 - faces_c / faces_a
    assert cut > 0.5, f'剔除率 {cut:.2%} 明显偏低，面剔除可能失效'
    # 分段装配（R 键渐进重建走的路径）必须与整块装配逐位一致
    import array as _array
    seg_p, seg_c, seg_f = _array.array('f'), _array.array('f'), 0
    for z0 in range(0, W, 3):                      # 故意用不整除的段长
        _p, _c, _f = build_mesh(h1, 1234, True, seg_p, seg_c, z0, min(W, z0 + 3))
        seg_f += _f
    assert seg_f == faces_c, (seg_f, faces_c)
    assert list(seg_p) == list(pos_c) and list(seg_c) == list(col_c), \
        '分段装配必须与整块装配逐位一致'
    print(f'  ok: voxel seeds change world; culling {faces_c}/{faces_a} '
          f'= {cut:.1%} removed; arrays sized consistently')


def test_kid_a_templates_valid():
    """KID-A 房间模版：21×21 尺寸 + **声明出口与网格开口一致**。

    原版曾把 tech_nook/data_temple 放在 N/E/W 组（实现只开 N/S/E）——
    玩家从西侧进入会撞墙。此测试即防这类"分类错误"回归。
    """
    import random
    from logic import kid_a as K
    rng = random.Random(12345)
    exits_pos = {'N': ((0, 9), (0, 10), (0, 11)),
                 'S': ((20, 9), (20, 10), (20, 11)),
                 'W': ((9, 0), (10, 0), (11, 0)),
                 'E': ((9, 20), (10, 20), (11, 20))}
    count = 0
    for key, pool in K._ALL_TEMPLATES.items():
        for fn in pool:
            g = fn(rng)
            assert len(g) == K.CHUNK and all(len(r) == K.CHUNK for r in g), fn.__name__
            for d in key:
                for (r, c) in exits_pos[d]:
                    assert g[r][c] == '.', f'{fn.__name__} 出口 {d} 未开口 @({r},{c})'
            count += 1
    print(f'  ok: kid_a {count} 个模版尺寸/出口一致（21x21，声明==开口）')


def test_kid_a_worldmap_exits_connected():
    """相邻区块出口必须互连（我声明 N，北邻必须声明 S）——否则走出即撞墙。"""
    from logic import kid_a as K
    wm = K.WorldMap(seed=99)
    wm.ensure_neighbors(0, 0)
    for (cx, cy), ex in wm.exits.items():
        for d, (dx, dy) in K._DIR_DELTA.items():
            nb = (cx + dx, cy + dy)
            if nb in wm.exits and d in ex:
                assert K._OPPOSITE[d] in wm.exits[nb], f'互连破坏 {(cx, cy)} {d}'
    # 世界可复现：同种子同结果
    wm2 = K.WorldMap(seed=99)
    wm2.ensure_neighbors(0, 0)
    assert wm.chunks == wm2.chunks, '同种子世界必须逐位一致'
    print(f'  ok: kid_a worldmap {len(wm.chunks)} 区块出口互连且同种子可复现')


def test_kid_a_energy_and_fragments():
    """能量：保护期不扣、之后移动 1/s、红区 +3/s；碎片：同键不可重复收集。"""
    from logic import kid_a as K
    e = K.EnergySystem()
    for _ in range(60):                                    # 1s（保护期内）
        e.update(1 / 60, K.AIR, True, False)
    assert e.energy == K.ENERGY_MAX, e.energy
    e.grace = 0.0                                          # 跳过保护期
    for _ in range(120):                                   # 2s 移动
        e.update(1 / 60, K.AIR, True, False)
    assert abs(e.energy - (K.ENERGY_MAX - 2.0)) < 0.05, e.energy
    for _ in range(60):                                    # 1s 红区
        e.update(1 / 60, K.RED, False, False)
    assert abs(e.energy - (K.ENERGY_MAX - 5.0)) < 0.05, e.energy
    f = K.FragmentSystem()
    assert f.collect(K.GLOW, (0, 0, 3, 3))
    assert not f.collect(K.GLOW, (0, 0, 3, 3))              # 同键拒绝
    assert f.collect(K.GOLD, (0, 0, 4, 4))
    print(f'  ok: kid_a 能量={e.energy:.1f}（-5.0）碎片 total={f.total()} '
          f'types={f.unique_types()}')


def test_kid_a_doors_connected():
    """门洞互通 + 出生点可离开区块（防"通而不达"与"出生点牢笼"）。

    这一条是反哺 arcade 原版时新增的第三类缺陷检测：energy_core /
    portal_gate 的门洞与大厅之间隔着两行墙（四门通而不达、整区块隔离）、
    generator_room 西侧门廊被发电机块封死（西门死路）。原版已同步修复。
    """
    import collections
    import random
    from logic import kid_a as K
    inner = {'N': (1, 10), 'S': (19, 10), 'W': (10, 1), 'E': (10, 19)}

    def reach(grid, start):
        walk = {(r, c) for r in range(K.CHUNK) for c in range(K.CHUNK)
                if grid[r][c] != '#'}
        if start not in walk:
            return set()
        seen = {start}
        q = collections.deque([start])
        while q:
            r, c = q.popleft()
            for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (r + dr, c + dc)
                if nxt in walk and nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        return seen

    rng = random.Random(2026)
    for key, pool in K._ALL_TEMPLATES.items():
        for fn in pool:
            grid = fn(rng)
            for d in key:
                comp = reach(grid, inner[d])
                assert comp, f'{fn.__name__} {d} 门内侧就是墙'
                for d2 in key:
                    if d2 != d:
                        assert inner[d2] in comp, \
                            f'{fn.__name__}：{d} 门进来走不到 {d2} 门'
    # 出生点可离开区块（跨区块可达 >= 500 格且确实出块）
    for seed in (1, 7, 42):
        wm = K.WorldMap(seed=seed)
        for dcy in (-1, 0, 1):
            for dcx in (-1, 0, 1):
                wm.ensure_neighbors(dcx, dcy)
        all_walk = set()
        for (cx, cy), grid in wm.chunks.items():
            for ly in range(K.CHUNK):
                for lx in range(K.CHUNK):
                    if grid[ly][lx] != '#':
                        all_walk.add((cx * K.CHUNK + lx, cy * K.CHUNK + ly))
        s = wm.find_safe_spawn(0, 0)
        s = (int(s[0]), int(s[1]))
        seen = {s}
        q = collections.deque([s])
        while q:
            x, z = q.popleft()
            for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nxt = (x + dx, z + dz)
                if nxt in all_walk and nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        assert len(seen) >= 500, (seed, len(seen))
        cc = (s[0] // K.CHUNK, s[1] // K.CHUNK)
        assert any((p[0] // K.CHUNK, p[1] // K.CHUNK) != cc for p in seen), \
            f'seed={seed} 出生点走不出区块'
    print('  ok: kid_a 全模版各门互通 + 出生点可离开区块（3 种子）')


if __name__ == '__main__':
    print('test_camera_initial_pose');     test_camera_initial_pose()
    print('test_camera_clamps');           test_camera_clamps()
    print('test_camera_custom_ranges');   test_camera_custom_ranges()
    print('test_camera_advance');          test_camera_advance()
    print('test_view_matrix_maps_eye_to_origin'); test_view_matrix_maps_eye_to_origin()
    print('test_look_at_matches_multiple_poses'); test_look_at_matches_multiple_poses()
    print('test_event_bus');               test_event_bus()
    print('test_quat_axis_angle_and_slerp');      test_quat_axis_angle_and_slerp()
    print('test_sampler_linear_step');            test_sampler_linear_step()
    print('test_timeline_wrap_and_sample');       test_timeline_wrap_and_sample()
    print('test_demo_timeline_choreography');     test_demo_timeline_choreography()
    print('test_choreography_pose_continuity');   test_choreography_pose_continuity()
    print('test_choreography_arm_ik');            test_choreography_arm_ik()
    print('test_animation_material_channels');    test_animation_material_channels()
    print('test_playback_states');                test_playback_states()
    print('test_playback_loop_wrap');             test_playback_loop_wrap()
    print('test_playback_end_pause');             test_playback_end_pause()
    print('test_playback_speed_and_seek');        test_playback_speed_and_seek()
    print('test_ui_hit_testing');                 test_ui_hit_testing()
    print('test_model_timeline_channels');        test_model_timeline_channels()
    print('test_obj_asset_decode');               test_obj_asset_decode()
    print('test_skeleton_hierarchy');             test_skeleton_hierarchy()
    print('test_arm_channels');                   test_arm_channels()
    print('test_box_data_pivot');                 test_box_data_pivot()
    print('test_boids_flock');                    test_boids_flock()
    print('test_waves_math');                     test_waves_math()
    print('test_terrain_row_emitter');            test_terrain_row_emitter()
    print('test_voxel_mesh_invariants');          test_voxel_mesh_invariants()
    print('test_blocks_atlas_and_world');         test_blocks_atlas_and_world()
    print('test_blocks_sky_and_acts');            test_blocks_sky_and_acts()
    print('test_blocks_camera_and_mobs');         test_blocks_camera_and_mobs()
    print('test_kkrieger_timeline');              test_kkrieger_timeline()
    print('test_kkrieger_camera_path_clearance'); test_kkrieger_camera_path_clearance()
    print('test_nordic_saga_timeline');           test_nordic_saga_timeline()
    print('test_nordic_camera_path_clearance');   test_nordic_camera_path_clearance()
    print('test_nordic_scene_geometry');          test_nordic_scene_geometry()
    print('test_kid_a_templates_valid');          test_kid_a_templates_valid()
    print('test_kid_a_worldmap_exits_connected'); test_kid_a_worldmap_exits_connected()
    print('test_kid_a_energy_and_fragments');     test_kid_a_energy_and_fragments()
    print('test_kid_a_doors_connected');          test_kid_a_doors_connected()
    print('ALL_LOGIC_TESTS_PASSED')

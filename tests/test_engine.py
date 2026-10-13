# -*- coding: utf-8 -*-
"""engine 测试套件（无窗口部分为主，GL 部分可跑则跑）。

运行::

    python -m tests.test_engine

原则与 run_tests 一致：断言数值、打印实测数据，不用"看起来对"。
GL 相关用例在拿不到上下文时打印 SKIP 而不是失败（CI 无显示器的环境也能过）。
"""
import json
import math
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyglet.math import Quaternion, Vec3

from engine.camera import FlyCamera, OrbitCamera
from engine.frustum import AABB, Frustum, SpatialHash
from engine.geometry import box, ground_grid, merge_meshes, plane, sphere, torus
from engine.gltf import GLTF
from engine.mathkit import (look_at_matrix, mat4_from_trs, mat4_identity,
                            mat4_inverse, mat4_mul, normal_matrix,
                            quat_from_axis_angle, slerp, trs_decompose)
from engine.picking import Picker, ray_aabb, ray_from_screen, ray_sphere, ray_triangle
from engine.physics import Body, PhysicsWorld
from engine.scene import Node, Scene
from engine.serialize import SceneWatcher, dumps, loads
from engine.timeline import PlaybackController, Sampler, Channel, Timeline


# ------------------------------------------------------------ 数学 --
def test_trs_roundtrip():
    t, q, s = (1.0, -2.0, 3.0), quat_from_axis_angle((0, 1, 0), 37.0), (2.0, 1.5, 0.5)
    m = mat4_from_trs(t, q, s)
    t2, q2, s2 = trs_decompose(m)
    err = (max(abs(a - b) for a, b in zip(t, t2))
           + max(abs(a - b) for a, b in zip(s, s2))
           + max(abs(a - b) for a, b in zip(q, q2)))
    assert err < 1e-5, f'TRS 往返误差 {err}'
    print(f'  ok: TRS->matrix->TRS 往返误差 {err:.2e}')


def test_mat4_inverse():
    m = mat4_from_trs((1, 2, 3), quat_from_axis_angle((1, 1, 0), 23.0), (1, 2, 0.5))
    ident = mat4_mul(m, mat4_inverse(m))
    err = max(abs(ident[i] - (1.0 if i % 5 == 0 else 0.0)) for i in range(16))
    assert err < 1e-5, f'求逆误差 {err}'
    print(f'  ok: mat4 inverse 误差 {err:.2e}')


def test_normal_matrix_nonuniform_scale():
    nm = normal_matrix(mat4_from_trs((0, 0, 0), (1, 0, 0, 0), (1, 4, 1)))
    # 缩放 (1,4,1) 的逆转置应为 (1, 0.25, 1)
    assert abs(nm[4] - 0.25) < 1e-6, f'法线矩阵错误: {nm}'
    print('  ok: 非均匀缩放的法线矩阵 = 逆转置')


def test_frustum_planes():
    from pyglet.math import Mat4
    proj = tuple(Mat4.perspective_projection(16 / 9, z_near=0.1, z_far=100, fov=60))
    view = look_at_matrix((0, 0, 10), (0, 0, 0), (0, 1, 0))
    f = Frustum.from_view_projection(mat4_mul(proj, view))
    assert f.contains_point((0, 0, 0))                 # 相机正前方
    assert f.contains_point((0, 0, -50))               # 远平面内
    assert not f.contains_point((0, 0, 200))           # 相机背后
    assert not f.contains_point((0, 0, -200))          # 远平面外
    assert not f.contains_point((60, 0, 0))            # 侧面外
    assert f.intersects_aabb(AABB.from_center_half((0, 0, 0), (1, 1, 1)))
    assert not f.intersects_aabb(AABB.from_center_half((0, 0, 150), (1, 1, 1)))
    assert not f.intersects_aabb(AABB.from_center_half((60, 0, 0), (1, 1, 1)))
    print('  ok: 视锥六平面（Gribb-Hartmann）内/外判定')


def test_spatial_hash():
    grid = SpatialHash(cell=4.0)
    bounds = {}
    for i in range(500):
        b = AABB.from_center_half(((i % 20) * 3.0, 0, (i // 20) * 3.0), (0.5, 0.5, 0.5))
        bounds[i] = b
        grid.insert(i, b)
    picked = grid.candidate_indices(AABB.from_center_half((10, 0, 10), (1, 1, 1)))
    assert 0 < len(picked) < 60, f'候选数异常: {len(picked)}'
    print(f'  ok: 空间哈希 500 物体，局部查询候选 {len(picked)}/500')


# ---------------------------------------------------------- 时间轴 --
def test_timeline_interpolations():
    lin = Sampler([0, 1], [(0, 0, 0), (10, 0, 0)], 'LINEAR')
    assert abs(lin.sample(0.25).x - 2.5) < 1e-6
    stp = Sampler([0, 1], [0.0, 5.0], 'STEP')
    assert stp.sample(0.99) == 0.0 and stp.sample(1.0) == 5.0
    cubic = Sampler([0, 1, 2], [((0, 0, 0), (0, 0, 0), (1, 0, 0)),
                                ((1, 0, 0), (10, 0, 0), (1, 0, 0)),
                                ((1, 0, 0), (20, 0, 0), (0, 0, 0))], 'CUBICSPLINE')
    mid = cubic.sample(0.5)
    assert abs(mid.x - 5.0) < 1e-6, f'CUBICSPLINE 中点 {mid}'
    print('  ok: LINEAR/STEP/CUBICSPLINE 三种插值')


def test_playback():
    tl = Timeline(4.0, [Channel('n', 'translation',
                                Sampler([0, 4], [(0, 0, 0), (4, 0, 0)]))])
    pb = PlaybackController(tl, looping=False)
    pb.play()
    pb.set_speed(2.0)
    ended = False
    for _ in range(200):
        r = pb.advance(0.03)
        if r == PlaybackController.ENDED:
            ended = True
            break
    assert ended and not pb.playing
    assert tl.playhead >= 4.0 - 1e-3
    # 循环模式：越界回绕
    tl2 = Timeline(2.0, [])
    pb2 = PlaybackController(tl2, looping=True)
    pb2.play()
    pb2.seek(1.9)
    pb2.advance(0.3)
    assert tl2.playhead < 0.3, f'未回绕: {tl2.playhead}'
    print('  ok: 播放机 倍速/ENDED 自动暂停/循环回绕')


# ------------------------------------------------------------ 几何 --
def test_geometry_normals_and_bounds():
    s = sphere(1.0, 16, 12)
    assert s.vertex_count > 0 and s.triangle_count > 0
    n = s.normals
    for i in range(0, 3 * s.vertex_count, 3):
        l = math.sqrt(n[i] ** 2 + n[i + 1] ** 2 + n[i + 2] ** 2)
        assert abs(l - 1.0) < 1e-5, '球法线未归一化'
    b = s.aabb()
    assert abs(b.min[0] + 1.0) < 1e-5 and abs(b.max[0] - 1.0) < 1e-5
    c, r = s.bounding_sphere()
    assert abs(r - 1.0) < 1e-5
    print(f'  ok: 球网格 {s.vertex_count} 顶点 法线归一 包围盒/球')


def test_merge_and_transform():
    a = box(1, 1, 1, (0, 0, 0), (1, 0, 0))
    b = box(1, 1, 1, (5, 0, 0), (0, 1, 0))
    m = merge_meshes([a, b])
    assert m.vertex_count == a.vertex_count + b.vertex_count
    m.transform(mat4_from_trs((0, 10, 0), (1, 0, 0, 0), (2, 2, 2)))
    bb = m.aabb()
    assert bb.min[1] > 8.5 and bb.max[1] > 10.9, f'变换后包围盒 {bb}'
    print('  ok: 网格合并 + 变换后包围盒')


# ------------------------------------------------------------ glTF --
def _make_gltf_files(tmpdir):
    """手工构造一个最小但覆盖面全的 glTF（含 sparse 与动画）。"""
    positions = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
    pos_bytes = struct.pack('<9f', *positions)
    idx_bytes = struct.pack('<3H', 0, 1, 2)
    # sparse：把 0 号顶点的 x 改成 5.0
    sparse_pos = struct.pack('<3f', 5.0, 0.0, 0.0)
    sparse_idx = struct.pack('<1I', 0)
    # 动画：translation 两帧 LINEAR
    anim_t = struct.pack('<2f', 0.0, 1.0)
    anim_v = struct.pack('<6f', 0.0, 0.0, 0.0, 3.0, 0.0, 0.0)

    bin_data = (pos_bytes + idx_bytes + sparse_pos + sparse_idx + anim_t + anim_v)
    o1, o2 = 0, len(pos_bytes)
    o3, o4 = o2 + len(idx_bytes), o2 + len(idx_bytes) + len(sparse_pos)
    o5 = o4 + len(sparse_idx)
    o6 = o5 + len(anim_t)
    gltf = {
        'asset': {'version': '2.0'},
        'scenes': [{'nodes': [0]}],
        'nodes': [{'name': 'tri', 'mesh': 0, 'translation': [0, 0, 0]},
                  {'name': 'mover'}],
        'meshes': [{'name': 'tri', 'primitives': [{
            'attributes': {'POSITION': 0}, 'indices': 1, 'material': 0}]}],
        'materials': [{'name': 'red', 'pbrMetallicRoughness': {
            'baseColorFactor': [0.8, 0.2, 0.1, 1.0],
            'metallicFactor': 0.1, 'roughnessFactor': 0.4}}],
        'buffers': [{'byteLength': len(bin_data)}],
        'bufferViews': [
            {'buffer': 0, 'byteOffset': o1, 'byteLength': len(pos_bytes)},
            {'buffer': 0, 'byteOffset': o2, 'byteLength': len(idx_bytes)},
            {'buffer': 0, 'byteOffset': o3, 'byteLength': len(sparse_pos)},
            {'buffer': 0, 'byteOffset': o4, 'byteLength': len(sparse_idx)},
            {'buffer': 0, 'byteOffset': o5, 'byteLength': len(anim_t)},
            {'buffer': 0, 'byteOffset': o6, 'byteLength': len(anim_v)},
        ],
        'accessors': [
            {'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3',
             'sparse': {'count': 1,
                        'indices': {'bufferView': 3, 'componentType': 5125},
                        'values': {'bufferView': 2}}},
            {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'},
            {'bufferView': 4, 'componentType': 5126, 'count': 2, 'type': 'SCALAR'},
            {'bufferView': 5, 'componentType': 5126, 'count': 2, 'type': 'VEC3'},
        ],
        'animations': [{'name': 'move', 'channels': [{
            'sampler': 0, 'target': {'node': 1, 'path': 'translation'}}],
            'samplers': [{'input': 2, 'output': 3, 'interpolation': 'LINEAR'}]}],
    }
    bin_path = os.path.join(tmpdir, 'test.bin')
    gltf_path = os.path.join(tmpdir, 'test.gltf')
    glb_path = os.path.join(tmpdir, 'test.glb')
    with open(bin_path, 'wb') as f:
        f.write(bin_data)
    gltf['buffers'][0]['uri'] = 'test.bin'
    with open(gltf_path, 'w', encoding='utf-8') as f:
        json.dump(gltf, f)
    # GLB：12 字节头 + JSON 块(4 对齐) + BIN 块
    json_bytes = json.dumps(gltf).encode('utf-8')
    json_bytes += b' ' * ((4 - len(json_bytes) % 4) % 4)
    bin_bytes = bin_data + b'\x00' * ((4 - len(bin_data) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    glb = struct.pack('<III', 0x46546C67, 2, total)
    glb += struct.pack('<II', len(json_bytes), 0x4E4F534A) + json_bytes
    glb += struct.pack('<II', len(bin_bytes), 0x004E4942) + bin_bytes
    with open(glb_path, 'wb') as f:
        f.write(glb)
    return gltf_path, glb_path


def test_gltf_loader():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        gltf_path, glb_path = _make_gltf_files(td)
        for path in (gltf_path, glb_path):
            g = GLTF.load(path)
            scene, pending = g.build_scene()
            assert len(pending) == 1, f'{path}: 应有 1 个 primitive'
            md, mat_index = pending[0][1], pending[0][2]
            assert md.positions[0] == 5.0, f'sparse 未生效: {md.positions[0]}'
            assert md.indices == [0, 1, 2]
            assert mat_index == 0
            mat = g.material(0)
            assert abs(mat.base_color[0] - 0.8) < 1e-6 and abs(mat.metallic - 0.1) < 1e-6
            anims = g.animations()
            assert anims and abs(anims[0]['duration'] - 1.0) < 1e-6
            node_index, ppath, sampler = anims[0]['channels'][0]
            assert ppath == 'translation'
            assert abs(sampler.sample(0.5).x - 1.5) < 1e-6
        print('  ok: glTF + GLB 解析（sparse/材质/动画，两种容器一致）')


# ------------------------------------------------------------ 物理 --
def test_physics_land_and_slide():
    world = PhysicsWorld(gravity=-20.0, use_hash=False)
    world.add_static(AABB((-10, -0.5, -10), (10, 0.0, 10)), 'ground')
    world.add_static(AABB((4.0, 0.0, -1.0), (4.5, 3.0, 1.0)), 'wall')
    body = Body(position=(0, 3, 0), half=(0.4, 0.4, 0.4))
    world.add_body(body)
    # 下落 -> 落地
    for _ in range(400):
        world.step(1 / 60)
    assert body.on_ground and abs(body.position[1] - 0.4) < 0.02, \
        f'落地异常: {body.position}'
    # 正对墙走（仅 +x）-> 被挡在墙前
    body.velocity = (2.0, 0.0, 0.0)
    for _ in range(300):
        world.step(1 / 60)
    assert 3.1 < body.position[0] < 3.61, f'未挡在墙前: {body.position}'
    # 挡住的同时给 +z：应沿墙滑动（x 不动，z 前进）
    z0 = body.position[2]
    body.velocity = (2.0, 0.0, 1.5)
    for _ in range(60):
        world.step(1 / 60)
    assert 3.1 < body.position[0] < 3.61, f'滑动时穿墙: {body.position}'
    assert body.position[2] > z0 + 0.5, f'未沿墙滑动: {body.position}'
    print(f"  ok: 落地 y={body.position[1]:.3f}，挡墙 x={body.position[0]:.2f}，"
          f"沿墙滑动 z {z0:.2f}->{body.position[2]:.2f}")


# ------------------------------------------------------------ 拾取 --
def test_picking_math():
    ray = ((0, 0, -5), (0, 0, 1))
    # ray-AABB：盒子 z ∈ [1,3]，起点 z=-5 → 命中 t=6
    t_box = ray_aabb(ray[0], ray[1], AABB((-1, -1, 1), (1, 1, 3)))
    assert t_box is not None and abs(t_box - 6.0) < 1e-6
    assert ray_aabb(ray[0], ray[1], AABB((10, 10, 10), (11, 11, 11))) is None
    # ray-球：球心原点半径 1 → t=4
    t_s = ray_sphere(ray[0], ray[1], (0, 0, 0), 1.0)
    assert t_s is not None and abs(t_s - 4.0) < 1e-6
    assert ray_sphere(ray[0], ray[1], (9, 9, 9), 0.5) is None
    # ray-三角形：三角形在 z=0 平面 → t=5
    t = ray_triangle(ray[0], ray[1], (-1, -1, 0), (1, -1, 0), (0, 1, 0))
    assert t is not None and abs(t - 5.0) < 1e-6
    miss = ray_triangle((9, 9, -5), (0, 0, 1), (-1, -1, 0), (1, -1, 0), (0, 1, 0))
    assert miss is None
    print(f'  ok: ray-AABB t={t_box:.1f} / ray-球 t={t_s:.1f} / ray-三角形 t={t:.1f}')


def test_picking_screen_ray():
    """屏幕点 -> 世界射线 -> 命中指定网格（走完整 unproject 链路）。"""
    from pyglet.math import Mat4
    from engine.camera import Camera
    cam = Camera(position=(0, 2, 8), target=(0, 1, 0), fov=60)
    aspect = 16 / 9
    vp = cam.view_projection(aspect)
    # 相机正前方偏上的点应该命中以原点为中心的单位盒
    scene = Scene()
    m = _FakeMesh(AABB((-1, 0, -1), (1, 2, 1)))
    node = Node('box', drawable=m)
    scene.add(node)
    scene.update()
    # 直接用 ndc(0,0)（屏幕中心）反投影，射线应穿过 (0, ~1.?, 0) 附近
    inv = cam.inverse_view_projection(aspect)
    ray = ray_from_screen(800, 450, 1600, 900, inv, cam.position)
    from engine.picking import ray_mesh
    hit = ray_mesh(ray, m, precise=False)
    assert hit is not None, '屏幕中心射线未命中相机正前方的盒子'
    print(f'  ok: 屏幕射线命中 t={hit.distance:.2f} 点=({hit.point[0]:.2f},'
          f'{hit.point[1]:.2f},{hit.point[2]:.2f})')


class _FakeMesh:
    """只带包围体与 model 的最小可拾取物（避开 GL）。"""

    def __init__(self, aabb):
        self.world_aabb = aabb
        self.model = mat4_identity()
        self.data = None

    def world_sphere(self):
        return self.world_aabb.center, self.world_aabb.radius


# ------------------------------------------------------------ 场景 --
def test_scene_hierarchy_and_culling():
    scene = Scene()
    parent = Node('parent', translation=(10, 0, 0))
    child = Node('child', translation=(0, 5, 0))
    parent.add(child)
    scene.add(parent)
    scene.update()
    assert child.world_position() == (10.0, 5.0, 0.0)
    # 脏标记：只动父节点，子节点世界矩阵跟着走
    parent.set_translation((20, 0, 0))
    scene.update()
    assert child.world_position() == (20.0, 5.0, 0.0)
    # 视锥剔除统计
    from pyglet.math import Mat4
    cam = OrbitCamera(target=(0, 0, 0), dist=10)
    proj = tuple(Mat4.perspective_projection(1.6, 0.1, 100, 60))
    f = Frustum.from_view_projection(mat4_mul(proj, cam.view_matrix()))
    visible = scene.cull(f)
    assert scene.stats['drawables'] == 0
    print('  ok: 层级世界矩阵 / 脏标记传播 / 剔除统计')


def test_serialize_roundtrip():
    scene = Scene(name='rt')
    n = scene.add(Node('a', translation=(1, 2, 3),
                       rotation=quat_from_axis_angle((0, 1, 0), 30.0)))
    n.add(Node('b', scale=(2, 2, 2)))
    scene.root.find('a').user_data = {'k': 'v'}
    text = dumps(scene)
    scene2 = loads(text)
    a = scene2.root.find('a')
    assert a is not None and a.translation == (1.0, 2.0, 3.0)
    assert a.user_data == {'k': 'v'}
    assert [c.name for c in a.children] == ['b']
    print('  ok: 场景 JSON 往返（TRS/层级/user_data）')


def test_scene_watcher(tmpdir=None):
    import tempfile
    import time as _t
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'scene.json')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(dumps(Scene(name='v1')))
        fired = []
        w = SceneWatcher(path, on_reload=lambda s: fired.append(s.name),
                         min_interval=0.0)
        w.poll()
        _t.sleep(0.02)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(dumps(Scene(name='v2')))
        assert w.poll() and fired == ['v2'], f'热重载未触发: {fired}'
        print('  ok: 场景文件热重载触发一次回调')


# ------------------------------------------------------------ 相机 --
def test_cameras():
    oc = OrbitCamera()
    eye0 = oc.eye
    oc.orbit(40, 0)
    assert oc.eye.x != eye0.x
    oc.zoom(-5)
    assert oc.dist > 18.0
    m = oc.view_matrix()
    assert len(m) == 16
    fc = FlyCamera(position=(0, 1.7, 8), yaw=0.0, pitch=0.0)
    fc.look(100, 0)                      # 右转
    fc.move(forward=1.0)
    for _ in range(30):
        fc.update(1 / 60)
    assert fc.position != (0.0, 1.7, 8.0)
    # FlyCamera 朝向由 yaw 驱动：yaw=look 后的前向量应指向前方
    f = fc.forward()
    assert abs(math.sqrt(f[0] ** 2 + f[1] ** 2 + f[2] ** 2) - 1.0) < 1e-6
    print(f'  ok: 轨道/漫游相机 状态推进 后位置={tuple(round(v, 2) for v in fc.position)}')


# ------------------------------------------------------------- GL 用例 --
def _gl_available():
    try:
        import pyglet
        pyglet.options['dpi_scaling'] = 'real'
        w = pyglet.window.Window(64, 64, visible=False)
        return w
    except Exception as e:
        print(f'  SKIP: 无 GL 上下文（{e}）')
        return None


def test_gl_pipeline():
    w = _gl_available()
    if not w:
        return
    from pyglet.gl import (GL_RGBA, GL_UNSIGNED_BYTE, GL_FRAMEBUFFER,
                           glBindFramebuffer, glReadPixels)
    import ctypes
    from engine.env import LightEnvironment
    from engine.geometry import sphere
    from engine.material import Material, PRESETS
    from engine.mesh import InstancedMesh, Mesh
    from engine.postfx import PostSettings
    from engine.renderer import Renderer
    from engine.shaders import SOURCES, build
    from engine.targets import RenderTarget, MipTarget

    # 1) 全部着色器可编译
    for name in SOURCES:
        build(name)
    # 2) 烘焙 IBL
    env = LightEnvironment(sun_dir=(0.3, 0.9, 0.2)).bake()
    assert env.baked
    # 3) MRT 目标完整
    rt = RenderTarget(64, 64, color_count=2)
    assert rt.is_complete
    mt = MipTarget(64, 64, levels=4)
    mt.bind_level(2)
    from pyglet.image import Framebuffer
    assert Framebuffer.get_status() == 'Framebuffer is complete.'
    mt.unbind()
    # 4) 渲染一帧：金属球 + 实例化 + 地面，中心像素应非黑
    r = Renderer(64, 64, environment=env)
    scene = Scene(environment=env)
    scene.add_mesh(Mesh(sphere(1.0, 20, 14), r.program('pbr'),
                        PRESETS['gold'].clone()), name='gold',
                   translation=(0, 1, 0))
    inst = InstancedMesh(box(0.5, 0.5, 0.5), r.program('pbr_inst'),
                         capacity=8, name='inst')
    from engine.mathkit import mat4_from_trs
    inst.write_all([mat4_from_trs((3, 0.5, 0), (1, 0, 0, 0), (1, 1, 1))] * 4,
                   [(1, 1, 1, 0)] * 4)
    scene.add_mesh(inst, name='inst')
    scene.add_mesh(Mesh(plane(20, 20, 1), r.program('pbr'),
                        Material('ground', (0.4, 0.4, 0.4), 0.0, 0.8)),
                   name='ground')
    cam = OrbitCamera(target=(0, 1, 0), yaw=0.0, pitch=10.0, dist=7.0)
    scene.update()
    stats = r.render(cam, scene, 1.0, window=w)
    data = (ctypes.c_ubyte * 4)()
    glReadPixels(32, 40, 1, 1, GL_RGBA, GL_UNSIGNED_BYTE, data)
    px = tuple(data)
    assert px[:3] != (0, 0, 0), f'中心像素全黑: {px}'
    assert stats['draw_calls'] >= 3
    w.close()
    print(f'  ok: 着色器 {len(SOURCES)} 个 / IBL 烘焙 / MRT / 实例化 / '
          f'渲染一帧 中心像素={px}')


def test_model_uniform_is_program_scoped():
    """``model`` uniform 是 **program 级共享**状态，去重必须按 program 当前值。

    这是 2026-10-12 定位的一个**静默 P0** 的回归守卫：原实现把"上次上传的矩阵"
    记在**网格自己**身上（``self._uploaded_model``），于是静止网格在第 2 帧
    判定"矩阵没变"就跳过上传，而 uniform 里装的是**上一个网格**的矩阵 ——
    整场景图形塌到最后一个绘制物的变换上，且不报任何错。

    这里用假 program 记录实际写入，零 GL 依赖。
    """
    from engine.mathkit import mat4_from_trs
    from engine.mesh import _bind_model

    class FakeProgram:
        """只记录被写了哪些 uniform 名。"""

        def __init__(self):
            self.writes = []

        def __setitem__(self, key, value):
            self.writes.append(key)

    m_a = mat4_from_trs((1.0, 0.0, 0.0), (1, 0, 0, 0), (1, 1, 1))
    m_b = mat4_from_trs((9.0, 0.0, 0.0), (1, 0, 0, 0), (1, 1, 1))

    p = FakeProgram()
    _bind_model(p, m_a)
    assert p.writes == ['model', 'normal_mat'], p.writes
    n = len(p.writes)
    _bind_model(p, m_a)                       # 同一个 program + 同一个矩阵 → 去重
    assert len(p.writes) == n, '重复上传：去重失效'
    # 关键断言：换一个网格的矩阵**必须**重新上传（否则就是用别人的变换画自己）
    _bind_model(p, m_b)
    assert len(p.writes) == n + 2, '同一 program 下矩阵变了却没重新上传 → 复用别人的变换'
    # 模拟"同 program 下两个网格轮流画"一帧：
    p2 = FakeProgram()
    for frame in range(3):
        for m in (m_a, m_b):
            _bind_model(p2, m)
    # 3 帧 × 2 网格，每帧两次矩阵都在变（a→b→a…）⇒ 共 6 次上传，一次都不能省
    assert len(p2.writes) == 12, f'轮流绘制时漏传：{len(p2.writes)} 次写入'
    # 两个 program 各自独立
    q = FakeProgram()
    _bind_model(q, m_a)
    assert len(q.writes) == 2
    print('  ok: model uniform 按 program 去重（相邻同值省、换网格必传）')


def test_gl_multimesh_frame_stability():
    """多网格场景**连续两帧必须一致** —— 直接盯住上面那个 P0 的用户可见症状。

    原缺陷下第 1 帧正确、第 2 帧起静止网格被画到别的网格的变换上，
    所以只要"同相机、同场景渲染两帧，像素必须逐字节相同"就能抓住它。
    """
    w = _gl_available()
    if not w:
        return
    import ctypes

    from pyglet.gl import GL_RGBA, GL_UNSIGNED_BYTE, glReadPixels
    from engine.camera import Camera
    from engine.env import LightEnvironment
    from engine.geometry import box, ground_grid, sphere
    from engine.material import PRESETS, Material
    from engine.mesh import Mesh
    from engine.renderer import Renderer
    from engine.scene import Scene

    env = LightEnvironment(sun_dir=(0.3, 0.8, 0.4)).bake()
    r = Renderer(96, 96, environment=env)
    scene = Scene(environment=env)
    # 3 个**静止**网格（原缺陷的触发条件：多网格 + 物体不动）
    scene.add_mesh(Mesh(sphere(0.8, 16, 12), r.program('pbr'), PRESETS['gold'].clone()),
                   name='orb', translation=(0, 1.0, 0))
    scene.add_mesh(Mesh(box(1.0, 1.6, 1.0), r.program('pbr'),
                        Material('slab', (0.8, 0.8, 0.85), 0.0, 0.6)),
                   name='slab', translation=(2.4, 0.8, 0))
    scene.add_mesh(Mesh(ground_grid(6, 1.0), r.program('pbr'),
                        Material('ground', (0.3, 0.32, 0.4), 0.0, 0.9)),
                   name='ground')
    cam = Camera(position=(5.0, 3.2, 5.0), target=(0.8, 0.9, 0.0), fov=55.0)

    def one_frame():
        scene.update()
        r.render(cam, scene, 1.0, window=w)
        buf = (ctypes.c_ubyte * (96 * 96 * 4))()
        glReadPixels(0, 0, 96, 96, GL_RGBA, GL_UNSIGNED_BYTE, buf)
        return bytes(buf)

    f1 = one_frame()
    f2 = one_frame()
    f3 = one_frame()
    assert f1 == f2, '第 2 帧与第 1 帧不一致（多网格静止场景的 model uniform 复用缺陷）'
    assert f2 == f3, '第 3 帧与第 2 帧不一致'
    # 顺带确认"非黑"：避免退化到"两帧都是空场景"这种假通过
    assert sum(f1[0::4]) > 96 * 96 * 4, '画面几乎全黑，用例失去意义'
    w.close()
    print(f'  ok: 多网格静止场景 连续 3 帧逐字节一致（均值 {sum(f1[0::4]) / (96 * 96):.1f}）')


def test_fsm_timeline_and_events():
    """FSM（`engine/fsm.py`，P0-5 自研）：时间驱动 + 事件驱动 + 钩子 + 轨迹。

    重点盯一个易错点：**同一状态的非相邻段**必须是"多个时间窗"而不是一个大窗 ——
    否则中间的别的状态会被 `at()` 错判。
    """
    from engine.fsm import Machine, machine_from_keys

    m = machine_from_keys([(0, 'a'), (5, 'b'), (10, 'a')])
    assert m.windows('a') == ((0.0, 5.0), (10.0, float('inf'))), m.windows('a')
    assert m.at(-1.0) is None, '窗口覆盖之外应保持原状态（刚建时是 None）'
    assert m.at(2.0) == 'a'
    assert m.at(7.0) == 'b', '非相邻段被并成大窗 → 中间段错判'
    assert m.at(12.0) == 'a'
    assert m.at(3.0) == 'a', '窗口外（同一状态内回退）仍保持当前状态'

    log = []
    m2 = Machine('act')
    m2.state('idle', time=(0, 2), on_enter=lambda s, d, t: log.append(f'enter:{d}'))
    m2.state('run', time=(2, 5), on_exit=lambda s, d, t: log.append(f'exit:{s}'))
    m2.state('done')
    m2.transition('interrupt', 'run', 'done')
    m2.transition('reset', '*', 'idle')
    assert m2.at(1.0) == 'idle' and log == ['enter:idle'], log
    assert m2.at(3.0) == 'run' and log == ['enter:idle'], ("'run' 没声明 on_enter，不该有日志", log)
    assert m2.fire('interrupt', t=3.5) == 'done'
    assert log == ['enter:idle', 'exit:run'], ("离开 run 应触发它的 on_exit", log)
    assert m2.fire('reset', t=4.0) == 'idle'              # '*' 通配
    assert [h[3] for h in m2.history] == ['idle', 'run', 'done', 'idle']
    try:
        m2.fire('nope')
    except KeyError:
        pass
    else:
        raise AssertionError('未声明的事件应报错（不静默）')
    print(f'  ok: FSM 时间窗（含非相邻段）与事件转移/hook/轨迹；events={m2.events()}')


def test_orchestration_bus():
    """事件总线（`engine/orchestration.py`，P0-5 自研）：顺序确定 + 级联 + 环检测。"""
    from engine.orchestration import Bus

    seen = []
    bus = Bus('demo')
    bus.on('state_enter', lambda e, p: seen.append(('a', p.get('state'))))
    bus.on('state_enter', lambda e, p: seen.append(('b', p.get('state'))))
    bus.bind('state_enter', 'ember_step')
    bus.bind('ember_step', 'flicker')
    out = bus.emit('state_enter', state='orbit')
    assert [e for e, _ in out] == ['state_enter', 'ember_step', 'flicker'], out
    assert seen == [('a', 'orbit'), ('b', 'orbit')], '订阅者顺序必须等于注册顺序'
    assert [e for _, e in bus.log] == ['state_enter', 'ember_step', 'flicker']
    # 确定性的另一个含义：同序列 → 同结果（不藏随机）
    bus2 = Bus('demo')
    got = []
    bus2.on('x', lambda e, p: got.append(p['v']))
    bus2.emit('x', v=1)
    bus2.emit('x', v=1)
    assert got == [1, 1]
    # 环 → 报错（不静默截断）
    cyc = Bus('cycle')
    cyc.bind('a', 'b')
    cyc.bind('b', 'a')
    try:
        cyc.emit('a')
    except RecursionError as e:
        assert '级联' in str(e)
    else:
        raise AssertionError('a→b→a 应触发环检测')
    print(f'  ok: 事件总线 级联顺序/订阅顺序/日志/环检测；events={bus.events()}')


def test_audio_track_and_render():
    """Track 合成 + WAV 写盘：确定性、时长精确、归一化不削波。"""
    import tempfile
    from engine.audio import Track, render_wav

    def build():
        tr = Track(1.0, rate=8000)
        tr.tone(0.0, 0.5, 440.0, wave='sine', amp=0.5)
        tr.tone(0.25, 0.5, 660.0, 330.0, wave='square', amp=0.3, lp=0.2)
        tr.noise(0.5, 0.4, amp=0.3, decay=6.0, seed=7)
        return tr

    with tempfile.TemporaryDirectory() as td:
        p1, d1 = render_wav(build(), os.path.join(td, 'a.wav'), normalize=0.9)
        p2, d2 = render_wav(build(), os.path.join(td, 'b.wav'), normalize=0.9)
        assert d1 == d2 == 1.0
        with open(p1, 'rb') as f:
            b1 = f.read()
        with open(p2, 'rb') as f:
            b2 = f.read()
        assert b1 == b2, '合成不确定（同参数不同结果）'
        import wave as _w
        with _w.open(p1, 'rb') as w:
            assert w.getframerate() == 8000 and w.getnframes() == 8000
            import struct as _s
            samples = _s.unpack('<%dh' % w.getnframes(), w.readframes(w.getnframes()))
        peak = max(abs(s) for s in samples) / 32767.0
        assert 0.85 <= peak <= 0.92, f'归一化异常 peak={peak:.3f}'
        # attack 起步（0.004s=32 样本）：首样本应远小于满幅
        assert abs(samples[0]) < 4000, f'开头未做 attack：{samples[0]}'
    print('  ok: Track 合成确定性 / WAV 时长与归一化')


def test_audio_timeline_player():
    """TimelineAudio：seek 后播放位置跟随（无声卡环境自动跳过）。

    不用 TemporaryDirectory 的自动清理：XAudio2 流式播放会一直握着文件句柄，
    Windows 上清理目录会 WinError 32。手动建目录，收尾尽力而为。
    """
    import shutil
    import tempfile
    import time as _t
    from engine.audio import TimelineAudio, Track, render_wav
    if not audio_ok():
        print('  SKIP: 无音频驱动')
        return
    td = tempfile.mkdtemp(prefix='audio_test_')
    try:
        tr = Track(2.0, rate=8000)
        tr.tone(0.0, 2.0, 440.0, wave='sine', amp=0.4)
        p, _ = render_wav(tr, os.path.join(td, 't.wav'))
        a = TimelineAudio(p, loop=True, volume=0.4)
        assert a.ok and abs(a.duration - 2.0) < 0.05
        a.play()
        _t.sleep(0.25)
        a.seek(1.5)
        _t.sleep(0.15)
        assert a.time > 1.4, f'seek 未生效: {a.time}'
        a.toggle_mute()
        assert a.muted
        a.close()
        print('  ok: TimelineAudio 播放/seek/静音')
    finally:
        try:
            a.delete() if hasattr(a, 'delete') else None
        except Exception:
            pass
        try:
            shutil.rmtree(td, ignore_errors=True)
        except Exception:
            pass


def audio_ok():
    try:
        import pyglet
        from pyglet.media import get_audio_driver
        drv = get_audio_driver()
        return drv is not None and 'silent' not in type(drv).__module__
    except Exception:
        return False


def test_kkrieger_ost_structure():
    """kkrieger 配乐：短渲染走通全链路（音效事件越界安全截断）。"""
    import tempfile
    from logic.kkrieger_audio import build_ost
    with tempfile.TemporaryDirectory() as td:
        path, dur = build_ost(os.path.join(td, 'ost.wav'), duration=4.0)
        assert abs(dur - 4.0) < 1e-6
        import wave as _w
        with _w.open(path, 'rb') as w:
            assert w.getnframes() == int(4.0 * 32000)
            import struct as _s
            samples = _s.unpack('<%dh' % w.getnframes(), w.readframes(w.getnframes()))
        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32767.0
        assert rms > 0.01, f'配乐全静音？rms={rms:.4f}'
    print(f'  ok: kkrieger OST 链路（4s 短渲染 rms={rms:.3f}）')


def test_merge_meshes_keeps_uv():
    """静态合批不得丢 UV/法线——曾经"空 MeshData 累加器"把 UV 整列清空的回归。"""
    a = box(1.0, 1.0, 1.0)
    b = sphere(0.5, 8, 6)          # 索引网格：合并时会展开（顶点数只增不减）
    m = merge_meshes([a, b])
    assert m.vertex_count >= a.vertex_count + b.vertex_count
    assert len(m.uvs) == m.vertex_count * 2, (len(m.uvs), m.vertex_count * 2)
    assert len(m.normals) == m.vertex_count * 3
    # 单元素合并同样不得清空（bug 时代的重灾区）
    single = merge_meshes([box(1, 1, 1)])
    assert len(single.uvs) == single.vertex_count * 2
    # 索引网格参与合并时保持一致
    m2 = merge_meshes([sphere(0.4, 6, 4), box(0.5, 0.5, 0.5)])
    assert len(m2.uvs) == m2.vertex_count * 2
    print('  ok: merge_meshes keeps UV/normals（含单元素与索引输入）')


def test_nordic_ost_structure():
    """诺德之境配乐：短渲染走通全链路（事件越界安全截断）。"""
    import tempfile
    from logic.nordic_audio import build_ost
    with tempfile.TemporaryDirectory() as td:
        path, dur = build_ost(os.path.join(td, 'nordic.wav'), duration=4.0)
        assert abs(dur - 4.0) < 1e-6
        import wave as _w
        with _w.open(path, 'rb') as w:
            assert w.getnframes() == int(4.0 * 32000)
            import struct as _s
            samples = _s.unpack('<%dh' % w.getnframes(),
                                w.readframes(w.getnframes()))
        rms = math.sqrt(sum(s * s for s in samples) / len(samples)) / 32767.0
        assert rms > 0.01, f'配乐全静音？rms={rms:.4f}'
    print(f'  ok: nordic saga OST 链路（4s 短渲染 rms={rms:.3f}）')


def test_advanced_geometry():
    """新建模：lathe/tube/torus_knot——顶点数、单位法线、闭合无缝。"""
    from engine.geometry import lathe, torus_knot, tube
    # lathe：顶点 = (segments+1)*len(profile)，法线解析且单位长
    prof = [(0.0, 0.0), (0.5, 0.1), (0.3, 0.8), (0.6, 1.4)]
    md = lathe(prof, segments=20)
    assert md.vertex_count == 21 * 4
    for i in range(0, len(md.normals), 3):
        l = math.sqrt(md.normals[i] ** 2 + md.normals[i + 1] ** 2
                      + md.normals[i + 2] ** 2)
        assert abs(l - 1.0) < 1e-6
    # 接缝：第一列与最后一列顶点位置一致
    assert md.positions[0:3] == md.positions[20 * 4 * 3:20 * 4 * 3 + 3]
    # tube：平行传输标架扫掠螺旋，端帽齐全
    pts = [(math.cos(a), a * 0.3, math.sin(a)) for a in
           [i * 0.4 for i in range(20)]]
    tb = tube(pts, 0.1, 8)
    assert tb.vertex_count == 20 * 9 + 2 * 10
    # torus_knot：闭合曲线，首末环顶点精确重合（和乐角补偿后）
    for pq in ((2, 3), (3, 7)):
        k = torus_knot(*pq, major=1.5, tube_r=0.2, tubular=120, radial=8)
        assert k.vertex_count == 121 * 9
        f = k.positions[0:3]
        e = k.positions[9 * 120 * 3:9 * 120 * 3 + 3]
        gap = max(abs(a - b) for a, b in zip(f, e))
        assert gap < 1e-9, (pq, gap)
    print('  ok: lathe/tube/torus_knot（单位法线、闭合无缝 gap<1e-9）')


def test_proctex_patterns():
    """程序化纹理：确定性、尺寸正确、fbm 可平铺、法线图是切线空间。"""
    from engine.proctex import PATTERNS, fbm, height_to_normal, value_noise
    # 确定性：同参数两次生成逐字节一致
    for name, maker in PATTERNS.items():
        p1, p2 = maker(size=64), maker(size=64)
        assert p1['base'] == p2['base'], name
        for key in ('base', 'mr', 'normal'):
            assert len(p1[key]) == 64 * 64 * 4, (name, key)
        if 'emissive' in p1:
            assert len(p1['emissive']) == 64 * 64 * 4
    # 平铺：u=0 与 u=1 采样值一致（REPEAT 采样才无缝）
    assert abs(fbm(0.0, 0.37, 4) - fbm(1.0, 0.37, 4)) < 1e-12
    # 空间相干：相邻像素差异应远小于白噪声（~0.29）
    vals = [fbm(x / 64.0, 0.5, 4) for x in range(64)]
    mean_diff = sum(abs(vals[i + 1] - vals[i]) for i in range(63)) / 63
    assert mean_diff < 0.05, mean_diff
    # 法线图：z 分量为主导（切线空间），且中心取向一致
    h = [0.5] * (16 * 16)
    n = height_to_normal(h, 16)
    assert n[2] > 250 and n[0] == 127 and n[1] == 127   # 0.5→127 的整除编码
    print(f'  ok: 5 种程序化纹理（确定性/平铺/相干 mean_diff={mean_diff:.4f}）')


def test_proctex_gl_material():
    """proctex 的 GL 层：纹理上传 + 材质接线（无上下文则 SKIP）。"""
    w = _gl_available()
    if not w:
        return
    from engine.proctex import material_from
    m = material_from('marble', size=64)
    assert m.base_tex is not None and m.mr_tex is not None
    assert m.normal_tex is not None
    m2 = material_from('circuit', size=64)
    assert m2.emissive_tex is not None
    assert m2.emissive != (0.0, 0.0, 0.0)      # 发光贴图不被默认 0 灭杀
    # 缓存：同图案同尺寸返回同一张纹理
    m3 = material_from('marble', size=64)
    assert m3.base_tex is m.base_tex
    w.close()
    print('  ok: proctex GL 纹理上传 + Material 接线 + 缓存')


# ------------------------------------------------------------------ 跑 --
def main():
    tests = [
        test_trs_roundtrip, test_mat4_inverse, test_normal_matrix_nonuniform_scale,
        test_frustum_planes, test_spatial_hash,
        test_timeline_interpolations, test_playback,
        test_geometry_normals_and_bounds, test_merge_and_transform,
        test_merge_meshes_keeps_uv,
        test_advanced_geometry, test_proctex_patterns, test_proctex_gl_material,
        test_nordic_ost_structure,
        test_gltf_loader,
        test_physics_land_and_slide,
        test_picking_math, test_picking_screen_ray,
        test_scene_hierarchy_and_culling, test_serialize_roundtrip,
        test_scene_watcher, test_cameras,
        test_audio_track_and_render, test_audio_timeline_player,
        test_kkrieger_ost_structure,
        test_model_uniform_is_program_scoped,
        test_fsm_timeline_and_events,
        test_orchestration_bus,
        test_gl_pipeline,
        test_gl_multimesh_frame_stability,
    ]
    failed = []
    for t in tests:
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
        print(f'{len(failed)}/{len(tests)} FAILED')
        return 1
    print('ALL_ENGINE_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

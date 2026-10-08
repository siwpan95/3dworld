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


# ------------------------------------------------------------------ 跑 --
def main():
    tests = [
        test_trs_roundtrip, test_mat4_inverse, test_normal_matrix_nonuniform_scale,
        test_frustum_planes, test_spatial_hash,
        test_timeline_interpolations, test_playback,
        test_geometry_normals_and_bounds, test_merge_and_transform,
        test_gltf_loader,
        test_physics_land_and_slide,
        test_picking_math, test_picking_screen_ray,
        test_scene_hierarchy_and_culling, test_serialize_roundtrip,
        test_scene_watcher, test_cameras,
        test_gl_pipeline,
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

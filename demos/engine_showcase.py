# -*- coding: utf-8 -*-
"""engine 综合演示：PBR/IBL + Bloom + SSAO + 实例化 + 视锥剔除 + 拾取 + 物理 + 性能面板 + 场景序列化。

一个 demo 把引擎的每条能力都跑一遍，方便一眼验收，也方便做基准::

    python demos/engine_showcase.py                 正常跑
    python demos/engine_showcase.py --smoke 2       冒烟 2 秒后关闭
    python demos/engine_showcase.py --shot          抓帧到 docs/ 后关闭
    python demos/engine_showcase.py --bench 5       5 秒基准（关 vsync）
    python demos/engine_showcase.py --flag nostatics 关掉静态柱林（看剔除差异）

按键::

    ESC 退出    SPACE 暂停    F1 HUD    F2 性能面板
    B   Bloom 开关            O  SSAO 开关
    V   轨道相机 / 第一人称漫游切换
    1/2/3  最终画面 / AO 视图 / Bloom 视图
    K   把场景存成 docs/engine_scene.json（随后改动会被热重载感知）
    L   立刻从该文件重载场景
    G   实例化小方块阵的显隐（1 次 draw call vs 600 次）
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pyglet.math import Mat4, Vec3                                   # noqa: E402

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.app import EngineApp, run                                # noqa: E402
from engine.camera import FlyCamera, OrbitCamera                     # noqa: E402
from engine.env import LightEnvironment                              # noqa: E402
from engine.geometry import box, cylinder, ground_grid, plane, sphere, torus  # noqa: E402
from engine.material import PRESETS, Material                        # noqa: E402
from engine.mesh import InstancedMesh, Mesh                          # noqa: E402
from engine.physics import Body, PhysicsWorld                        # noqa: E402
from engine.picking import Picker                                    # noqa: E402
from engine.renderer import Renderer                                 # noqa: E402
from engine.scene import Node, Scene                                 # noqa: E402
from engine.serialize import SceneWatcher, save_scene                # noqa: E402
from engine.shaders import get                                       # noqa: E402

TITLE = '3dworld engine · PBR/IBL + Bloom + SSAO + instancing + culling'
HELP = ('ESC quit  SPACE pause  F2 perf  B bloom  O ssao  V camera  '
        '1/2/3 views  G grid  K save  L load  click=pick')
SHOTS = ((1.2, 'docs/engine_showcase_01.png'),
         (4.5, 'docs/engine_showcase_02.png'))
SCENE_JSON = 'docs/engine_scene.json'

FIELD_N = 24                      # 实例化阵列 24x24 = 576 个（1 次 draw call）
PILLAR_N = 80                     # 独立柱体数量（视锥剔除的样本）


class ShowcaseApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    DURATION = 24.0
    Z_FAR = 400.0

    # ------------------------------------------------------------ 构建 --
    def setup(self):
        w, h = self.window.width, self.window.height
        self.quad_programs = {}
        self.env = LightEnvironment(
            sun_dir=(0.42, 0.62, 0.30), sun_color=(1.0, 0.92, 0.80),
            sun_intensity=1.7, ibl_intensity=0.9,
            sky_zenith=(0.16, 0.34, 0.72), sky_horizon=(0.46, 0.62, 0.82),
            sky_ground=(0.10, 0.095, 0.09)).bake()
        if 'debugao' in self.args.flags:
            self.renderer = Renderer(w, h, environment=self.env)
            self.renderer.post.settings.debug = 1
        else:
            self.renderer = Renderer(w, h, environment=self.env)
        # 基准/排查用开关：--flag nossao / nobloom / nofield
        post = self.renderer.post.settings
        if 'nossao' in self.args.flags:
            post.ssao_enabled = False
        if 'nobloom' in self.args.flags:
            post.bloom_enabled = False
        self.scene = Scene(name='showcase', environment=self.env)
        self.camera = OrbitCamera(target=(0.0, 1.2, 0.0), yaw=38.0,
                                  pitch=24.0, dist=26.0,
                                  dist_range=(6.0, 90.0),
                                  fov=self.FOV, far=self.Z_FAR)
        self.fly = FlyCamera(position=(0.0, 2.2, 26.0), fov=self.FOV,
                             far=self.Z_FAR)
        self.use_fly = False
        self.picker = Picker()
        self.selected = None
        self.physics = PhysicsWorld(gravity=-22.0, cell=6.0)
        self._key_state = set()

        pbr = get('pbr')
        pbr_inst = get('pbr_inst')

        # --- 地面：AABB 同时进物理世界（角色能站上去） ---
        ground = Mesh(ground_grid(half=40, tile=2.0), pbr,
                      PRESETS['marble'].clone(name='ground'))
        ground.material.metallic = 0.0
        ground.material.roughness = 0.75
        self.scene.add_mesh(ground, name='ground')
        self.physics.add_static(ground.world_aabb, 'ground')

        # --- 主角物件：一圈不同 PBR 材质 ---
        self.heroes = []
        specs = [('gold', 0), ('copper', 1), ('steel', 2), ('iron', 3),
                 ('ceramic', 4), ('rubber', 5), ('marble', 6),
                 ('emerald_light', 7)]
        for i, (preset, slot) in enumerate(specs):
            ang = 2 * math.pi * i / len(specs)
            r = 7.0
            geo = (sphere(0.9, 28, 20) if i % 3 == 0
                   else (torus(0.75, 0.26, 28, 14) if i % 3 == 1
                         else box(1.5, 1.5, 1.5)))
            m = Mesh(geo, pbr, PRESETS[preset].clone(name=f'{preset}_{i}'))
            node = self.scene.add_mesh(
                m, name=f'hero_{preset}_{i}',
                translation=(math.cos(ang) * r, 1.1, math.sin(ang) * r))
            self.heroes.append((node, ang))

        # --- 半透明球（走 BLEND 队列：最后画 + 关深度写入） ---
        glass = Mesh(sphere(1.6, 32, 24), pbr,
                     Material('glass', (0.55, 0.75, 0.95), 0.0, 0.05,
                              alpha=0.45, alpha_mode='BLEND'))
        self.scene.add_mesh(glass, name='glass', translation=(0.0, 1.9, 0.0))
        self.glass = glass

        # --- 实例化阵列：576 个小方块，1 次 draw call ---
        field = InstancedMesh(box(0.42, 0.42, 0.42), pbr_inst,
                              PRESETS['plastic_red'].clone(name='field'),
                              capacity=FIELD_N * FIELD_N, name='field')
        self.field_matrices = []
        for ix in range(FIELD_N):
            for iz in range(FIELD_N):
                x = (ix - FIELD_N / 2) * 1.5
                z = (iz - FIELD_N / 2) * 1.5
                self.field_matrices.append((x, 0.6, z, ix, iz))
        self.field = field
        self.field_node = self.scene.add_mesh(field, name='field')
        self._field_flat = None
        self._write_field(0.0)
        # 整块给一个覆盖全场的世界包围盒：逐实例算包围盒没有意义（它们是一批）
        from engine.frustum import AABB
        half = FIELD_N * 1.5 * 0.5
        field.world_aabb = AABB((-half, -1.0, -half), (half, 3.0, half))
        self.show_field = True

        # --- 独立柱林：视锥剔除的"可见/剔除"计数来源 ---
        if 'nostatics' not in self.args.flags:
            cyl = cylinder(0.45, 4.2, 14)
            # 材质做 6 档复用而不是每柱一份：渲染器按"同材质相邻"跳过重复
            # 应用，逐柱独享材质会让材质应用次数退回每柱一次
            pillar_mats = [PRESETS['steel'].clone(name=f'steel_{k}',
                                                  roughness=0.15 + k * 0.09)
                           for k in range(6)]
            for i in range(PILLAR_N):
                ang = i * 2.399963                      # 黄金角螺旋，铺得均匀
                rad = 6.0 + i * 0.28
                m = Mesh(cyl, pbr, pillar_mats[i % 6])
                self.scene.add_mesh(
                    m, name=f'pillar_{i}',
                    translation=(math.cos(ang) * rad, 2.1,
                                 math.sin(ang) * rad))
                if i % 6 == 0:
                    self.physics.add_static(m.world_aabb, f'pillar_{i}')

        # --- 物理：一个会弹跳的球 ---
        self.body = Body(position=(3.0, 14.0, 3.0), half=(0.7, 0.7, 0.7),
                         gravity=-22.0, name='bouncer')
        self.physics.add_body(self.body)
        self.bouncer = Mesh(sphere(0.7, 24, 16), pbr,
                            PRESETS['neon'].clone(name='bouncer'))
        self.bouncer_node = self.scene.add_mesh(self.bouncer, name='bouncer')
        self.ground_h = 0.0

        # --- 点光源：3 盏绕场转的彩灯 ---
        self.renderer.set_point_lights([((0, 0, 0), (0, 0, 0))] * 3)

        # --- 场景序列化的热重载 ---
        self.watcher = SceneWatcher(os.path.join(ROOT, SCENE_JSON),
                                    on_reload=self._on_scene_reloaded)
        self.reload_msg = ''

        self.scene.update()

    def _write_field(self, t):
        """更新实例化阵列（波浪动画）。

        走 write_raw 快路径：单位旋转的矩阵列是常量，只有平移 y 和 glow
        在变——预先建好扁平列表，每帧只改 y 分量再整体上传。
        之前用 mat4_from_trs 逐实例构造，576 个实例每帧要多花几毫秒。
        """
        data = self.field_matrices
        n = min(len(data), self.field.capacity)
        flat = self._field_flat
        if flat is None:
            flat = self._field_flat = {
                'i_m0': [1.0, 0.0, 0.0, 0.0] * n,
                'i_m1': [0.0, 1.0, 0.0, 0.0] * n,
                'i_m2': [0.0, 0.0, 1.0, 0.0] * n,
                'i_m3': [0.0] * (n * 4),
                'i_tint': [0.0] * (n * 4),
            }
            for i, (x, y, z, _ix, _iz) in enumerate(data[:n]):
                flat['i_m3'][i * 4:i * 4 + 4] = [x, y, z, 1.0]
        m3, tint = flat['i_m3'], flat['i_tint']
        for i in range(n):
            x, y, z, ix, iz = data[i]
            wave = math.sin(t * 1.6 + (ix + iz) * 0.35) * 0.55
            m3[i * 4 + 1] = y + wave
            glow = max(0.0, wave) * 0.5
            tint[i * 4:i * 4 + 4] = [1.0, 0.55 + glow, 0.25, glow]
        self.field.write_raw(flat, n)

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        t = self.time
        # 相机：轨道自动环绕；第一人称由按键驱动
        if self.use_fly:
            self._update_fly(dt)
        else:
            self.camera.advance(dt * 0.35)

        # 主角物件自转 + 上下浮动
        for node, ang in self.heroes:
            node.set_rotation(_yaw_quat(math.degrees(t * 22.0) + ang * 40.0))
            node.set_translation((math.cos(ang) * 7.0,
                                  1.1 + math.sin(t * 1.3 + ang * 2.0) * 0.35,
                                  math.sin(ang) * 7.0))

        # 实例化阵列（动态写实例数据）
        if self.show_field:
            self._write_field(t)
        else:
            self.field.count = 0

        # 半透明球缓慢起伏
        self.glass.set_model(_trs((0.0, 1.9 + math.sin(t * 0.9) * 0.3, 0.0)))

        # 物理：弹跳球（落到地面就重置回空中，循环演示）
        self.physics.step(dt, substeps=2)
        if self.body.position[1] < 0.9 and self.body.velocity[1] <= 0.0:
            self.body.position = (3.0 + math.sin(t) * 2.0, 14.0,
                                  3.0 + math.cos(t) * 2.0)
            self.body.velocity = (0.0, 0.0, 0.0)
        self.bouncer_node.set_translation(self.body.position)
        self.bouncer.material.emissive = (0.0, 1.4 if self.body.on_ground else 0.5, 2.0)

        # 点光源绕场
        lights = []
        for i in range(3):
            a = t * 0.7 + i * 2.094
            r = 11.0
            lights.append(((math.cos(a) * r, 3.2 + math.sin(t * 1.1 + i) * 1.2,
                            math.sin(a) * r),
                           ((0.9, 0.35, 0.25) if i == 0 else
                            (0.25, 0.7, 0.95) if i == 1 else
                            (0.35, 0.95, 0.4))))
        self.renderer.set_point_lights(lights)

        self.scene.update(dt)
        # 热重载：改了 JSON 就自动套用（只套用 TRS，几何不重建）
        self.watcher.poll()

    def _update_fly(self, dt):
        ks = self._key_state
        f = (1.0 if 'W' in ks else 0.0) - (1.0 if 'S' in ks else 0.0)
        r = (1.0 if 'D' in ks else 0.0) - (1.0 if 'A' in ks else 0.0)
        u = (1.0 if 'E' in ks else 0.0) - (1.0 if 'Q' in ks else 0.0)
        self.fly.move(forward=f, right=r, up=u, sprint='SHIFT' in ks)
        self.fly.update(dt)

    # ------------------------------------------------------------ 绘制 --
    def render(self):
        cam = self.fly if self.use_fly else self.camera
        w, h = self.window.width, self.window.height
        self.renderer.resize(w, h)
        stats = self.renderer.render(cam, self.scene, w / float(h), self.window)
        self.perf.reset_counters()
        self.perf.draw_calls = stats['draw_calls']
        self.perf.triangles = stats['triangles']
        self.perf.visible = stats['visible']
        self.perf.culled = stats['culled']
        self.perf.instances = stats['instances']

    def on_resize_hook(self, width, height):
        self.renderer.resize(width, height)

    def status_text(self):
        base = super().status_text()
        sel = self.selected.name if self.selected else '-'
        mode = 'FLY' if self.use_fly else 'ORBIT'
        return f'{base}   [{mode}] pick={sel} {self.reload_msg}'

    def extra_perf_lines(self):
        return [f'light env baked={self.env.baked}']

    # ------------------------------------------------------------ 输入 --
    def on_key(self, symbol, modifiers):
        key = __import__('pyglet').window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        if name in ('W', 'A', 'S', 'D', 'Q', 'E', 'SHIFT', 'LSHIFT', 'RSHIFT'):
            self._key_state.add('SHIFT' if 'SHIFT' in name else name)
            return
        s = chr(symbol) if 32 <= symbol < 127 else ''
        s = s.upper()
        post = self.renderer.post.settings
        if s == 'B':
            post.bloom_enabled = not post.bloom_enabled
        elif s == 'O':
            post.ssao_enabled = not post.ssao_enabled
        elif s == 'V':
            self.use_fly = not self.use_fly
            if self.use_fly:
                self._sync_fly_to_orbit()
        elif s == '1':
            post.debug = 0
        elif s == '2':
            post.debug = 1
        elif s == '3':
            post.debug = 2
        elif s == 'G':
            self.show_field = not self.show_field
        elif s == 'K':
            path = save_scene(self.scene, os.path.join(ROOT, SCENE_JSON))
            self.reload_msg = f'saved {os.path.basename(path)}'
        elif s == 'L':
            from engine.serialize import load_scene
            try:
                loaded = load_scene(os.path.join(ROOT, SCENE_JSON))
                self._apply_loaded(loaded)
                self.reload_msg = 'loaded'
            except Exception as e:
                self.reload_msg = f'load failed: {e}'
        elif s == 'P':
            self.perf.cpu.reset()

    def on_key_release(self, symbol, modifiers):
        key = __import__('pyglet').window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        self._key_state.discard(name)
        self._key_state.discard('SHIFT')

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self.use_fly:
            self.fly.look(dx, dy)
        else:
            self.camera.orbit(dx, dy)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        if not self.use_fly:
            self.camera.zoom(scroll_y)

    def on_mouse_press(self, x, y, button, modifiers):
        """点击拾取：命中的物体发亮，名字显示在状态行。"""
        cam = self.fly if self.use_fly else self.camera
        w, h = self.window.width, self.window.height
        # pyglet 的鼠标 y 原点在下方，拾取用的是同一套约定（screen_to_ndc 已对齐）
        hit = self.picker.pick(x, y, w, h, cam, self.scene,
                               aspect=w / float(h), precise=True)
        if self.selected is not None:
            self.selected.material.emissive = (0.0, 0.0, 0.0)
        if hit is None:
            self.selected = None
            return
        self.selected = hit.drawable
        self.selected.material.emissive = (0.8, 0.6, 0.2)
        self.reload_msg = f'pick {hit.drawable.name} d={hit.distance:.2f}'

    # ------------------------------------------------------------ 热重载 --
    def _sync_fly_to_orbit(self):
        """切到第一人称时，从轨道相机当前视线接管朝向（避免画面突然跳转）。"""
        eye = tuple(self.camera.eye)
        tgt = tuple(self.camera.target)
        d = (eye[0] - tgt[0], eye[1] - tgt[1], eye[2] - tgt[2])
        l = math.sqrt(sum(c * c for c in d)) or 1.0
        d = (d[0] / l, d[1] / l, d[2] / l)
        self.fly.position = eye
        # forward = (cos p cos y, sin p, cos p sin y)
        self.fly.pitch = math.degrees(math.asin(max(-1.0, min(1.0, d[1]))))
        self.fly.yaw = math.degrees(math.atan2(d[2], d[0]))
        self.fly.update(0.0)

    def _on_scene_reloaded(self, loaded):
        """只套用节点 TRS（几何不重建：JSON 里没有顶点）。"""
        count = 0
        for src in loaded.root.traverse():
            dst = self.scene.root.find(src.name)
            if dst is None:
                continue
            dst.set_translation(src.translation)
            dst.set_rotation(src.rotation)
            dst.set_scale(src.scale)
            count += 1
        self.reload_msg = f'hot-reloaded {count} nodes'

    def _apply_loaded(self, loaded):
        self._on_scene_reloaded(loaded)


# ------------------------------------------------------------------ 工具 --
def _yaw_quat(deg):
    from engine.mathkit import quat_from_axis_angle
    return quat_from_axis_angle((0.0, 1.0, 0.0), deg)


def _trs(t):
    from engine.mathkit import mat4_from_trs
    return mat4_from_trs(t, (1.0, 0.0, 0.0, 0.0), (1.0, 1.0, 1.0))


if __name__ == '__main__':
    sys.exit(run(ShowcaseApp))

# -*- coding: utf-8 -*-
"""材质实验室：新建模（lathe/torus_knot/tube）× 新素材（proctex 纹理）× PBR。

引擎此前只有"纯色材质 + 基本体"——这个 demo 把两样新能力合到一处：
- 五只 lathe 旋转体花瓶（一条轮廓线决定全部形状）各配一种程序化纹理；
- 中央一座 (2,3) 环面结雕塑（rust_metal），平行传输标架保证闭合无缝；
- 一条 tube 螺旋线圈（brushed 拉丝金属）绕在基座上；
- 背景电路板墙（circuit，走线自发光）+ 六边形装甲板柱（hex_panel）。

用法::

    python examples/material_lab.py                正常跑
    python examples/material_lab.py --smoke 2      冒烟 2 秒后关闭
    python examples/material_lab.py --shot         抓帧到 docs/ 后关闭

按键：ESC 退出  SPACE 暂停  F2 性能面板  B Bloom  O SSAO  V 相机模式
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
from engine.geometry import box, cylinder, lathe, plane, torus_knot, tube  # noqa: E402
from engine.material import PRESETS                                  # noqa: E402
from engine.mesh import Mesh                                         # noqa: E402
from engine.proctex import material_from                             # noqa: E402
from engine.renderer import Renderer                                 # noqa: E402
from engine.scene import Scene                                       # noqa: E402
from engine.shaders import get                                       # noqa: E402

TITLE = '3dworld · 材质实验室 — lathe / torus_knot / tube × 程序化 PBR 纹理'
HELP = ('ESC quit  SPACE pause  F2 perf  B bloom  O ssao  V camera  '
        'auto-orbit tour')
SHOTS = ((6.0, 'docs/material_lab_01.png'),
         (16.5, 'docs/material_lab_02.png'))
DURATION = 30.0

# 花瓶轮廓（r, y）：束颈 → 鼓腹 → 敞口，一条线就是一只器皿
VASE_PROFILES = (
    ((0.00, 0.00), (0.42, 0.00), (0.55, 0.12), (0.34, 0.55),
     (0.30, 0.85), (0.46, 1.30), (0.40, 1.52), (0.30, 1.60)),   # 经典束颈
    ((0.00, 0.00), (0.30, 0.00), (0.52, 0.35), (0.60, 0.80),
     (0.44, 1.15), (0.18, 1.38), (0.16, 1.50), (0.26, 1.58)),   # 高脚杯
    ((0.00, 0.00), (0.46, 0.00), (0.58, 0.22), (0.62, 0.70),
     (0.50, 1.05), (0.36, 1.30), (0.40, 1.52), (0.52, 1.66)),   # 敞口瓮
    ((0.00, 0.00), (0.36, 0.00), (0.44, 0.30), (0.28, 0.70),
     (0.42, 1.10), (0.54, 1.36), (0.42, 1.55), (0.28, 1.62)),   # 葫芦
    ((0.00, 0.00), (0.50, 0.00), (0.52, 0.45), (0.38, 0.90),
     (0.26, 1.20), (0.24, 1.45), (0.34, 1.58), (0.44, 1.64)),   # 蒜头瓶
)
# 花瓶材质：程序化纹理 × 引擎预设各占一半
VASE_MATS = ('marble', 'rust_metal', 'gold', 'hex_panel', 'ceramic')


class MaterialLabApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    DURATION = DURATION
    Z_FAR = 300.0

    def setup(self):
        w, h = self.window.width, self.window.height
        # 暮色天光：压低 IBL 亮度，让自发光纹理与彩灯成为主角
        self.env = LightEnvironment(
            sun_dir=(-0.35, 0.55, 0.42), sun_color=(1.0, 0.86, 0.70),
            sun_intensity=1.4, ibl_intensity=0.5,
            sky_zenith=(0.05, 0.07, 0.15), sky_horizon=(0.28, 0.17, 0.13),
            sky_ground=(0.03, 0.03, 0.035)).bake()
        self.renderer = Renderer(w, h, environment=self.env)
        self.scene = Scene(name='material_lab', environment=self.env)
        self.camera = OrbitCamera(target=(0.0, 2.6, 0.0), yaw=62.0,
                                  pitch=22.0, dist=12.0,
                                  dist_range=(5.0, 60.0),
                                  fov=self.FOV, far=self.Z_FAR)
        self.fly = FlyCamera(position=(0.0, 3.0, 16.0), fov=self.FOV,
                             far=self.Z_FAR)
        self.use_fly = False
        pbr = get('pbr')

        # --- 地面：大理石纹（512px 大纹理 + 低频脉纹，才不像沙砾） ---
        ground_geo = plane(60.0, 60.0, segs=1)
        ground_geo.uvs = [v * 4.0 for v in ground_geo.uvs]
        ground = Mesh(ground_geo, pbr,
                      material_from('marble', size=512,
                                    pattern_kwargs={'vein_scale': 2.0}))
        self.scene.add_mesh(ground, name='ground')

        # --- 中央主角：(2,3) 环面结雕塑（锈蚀钢板），俯视为主才读得出结形 ---
        knot = Mesh(torus_knot(2, 3, major=2.2, tube_r=0.42,
                               tubular=200, radial=12),
                    pbr, material_from('rust_metal', size=512, seed=7))
        self.knot_node = self.scene.add_mesh(knot, name='knot',
                                             translation=(0.0, 3.4, 0.0))
        self._knot_tilt = _axis_quat((1.0, 0.0, 0.0), 38.0)
        # 结的基座 + 线圈：tube 螺旋（拉丝金属）
        pts = [(math.cos(a) * 2.7, 0.55 + a * 0.13, math.sin(a) * 2.7)
               for a in [i * 0.09 for i in range(96)]]
        coil = Mesh(tube(pts, 0.09, 8), pbr,
                    material_from('brushed', size=256))
        self.scene.add_mesh(coil, name='coil')
        pedestal = Mesh(cylinder(1.6, 0.5, 40), pbr, PRESETS['marble'])
        self.scene.add_mesh(pedestal, name='pedestal', translation=(0.0, 0.25, 0.0))

        # --- 五只 lathe 花瓶（环绕主角） ---
        self.vases = []
        for i, (prof, mat_name) in enumerate(zip(VASE_PROFILES, VASE_MATS)):
            ang = 2 * math.pi * i / len(VASE_PROFILES)
            r = 6.2
            if mat_name in ('gold', 'ceramic'):
                mat = PRESETS[mat_name].clone(name=f'vase_{mat_name}')
            else:
                mat = material_from(mat_name, size=256, seed=10 + i)
            m = Mesh(lathe(prof, segments=36, closed_bottom=True), pbr, mat)
            node = self.scene.add_mesh(
                m, name=f'vase_{i}',
                translation=(math.cos(ang) * r, 0.95, math.sin(ang) * r),
                scale=(1.3, 1.3, 1.3))
            # 展台
            stand = Mesh(cylinder(0.78, 0.95, 24), pbr, PRESETS['steel'])
            self.scene.add_mesh(stand, name=f'stand_{i}',
                                translation=(math.cos(ang) * r, 0.475,
                                             math.sin(ang) * r))
            self.vases.append((node, ang))

        # --- 背景墙：电路板面板（自发光走线） ×2 + 六边形装甲柱 ×4 ---
        wall_mat = material_from('circuit', size=256,
                                 emissive=(1.5, 1.5, 1.5))
        for k, (wx, wz, ry) in enumerate(((-8.0, -6.0, 34), (8.0, -6.0, -34))):
            wall_geo = box(7.5, 4.6, 0.4)
            wall_geo.uvs = [v * 2 for v in wall_geo.uvs]
            m = Mesh(wall_geo, pbr, wall_mat)
            node = self.scene.add_mesh(m, name=f'pcbwall_{k}',
                                       translation=(wx, 2.3, wz))
            node.set_rotation(_yaw_quat(ry))
        hex_mat = material_from('hex_panel', size=256,
                                emissive=(1.3, 1.3, 1.3))
        for k in range(4):
            ang = math.pi * (0.25 + 0.5 * k)
            cx, cz = math.cos(ang) * 11.5, math.sin(ang) * 11.5 - 2.0
            col = Mesh(cylinder(0.8, 6.6, 20), pbr, hex_mat)
            self.scene.add_mesh(col, name=f'hexcol_{k}',
                                translation=(cx, 3.3, cz))

        self.renderer.set_point_lights([((0, 0, 0), (0, 0, 0))] * 3)
        self.scene.update()

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        t = self.time
        if not self.use_fly:
            self.camera.advance(dt * 0.3)

        # 主角结：慢旋 + 呼吸起伏（倾斜基座姿态 × 绕 Y 自旋 = 立体翻滚）
        spin = _yaw_quat(math.degrees(t * 14.0))
        from engine.mathkit import quat_mul
        self.knot_node.set_rotation(quat_mul(spin, self._knot_tilt))
        self.knot_node.set_translation(
            (0.0, 3.4 + math.sin(t * 0.8) * 0.22, 0.0))

        # 花瓶：各自缓转
        for node, ang in self.vases:
            node.set_rotation(_yaw_quat(math.degrees(t * 9.0) + math.degrees(ang)))

        # 三盏彩灯绕场 + 一盏中心主光打亮主角结
        lights = [((0.0, 6.5, 2.0), (2.4, 2.2, 2.0))]
        for i in range(3):
            a = t * 0.55 + i * 2.094
            r = 8.5
            lights.append(((math.cos(a) * r, 3.8 + math.sin(t * 1.2 + i) * 0.9,
                            math.sin(a) * r),
                           ((2.6, 1.1, 0.4) if i == 0 else
                            (0.4, 1.9, 2.6) if i == 1 else
                            (2.2, 0.6, 1.8))))
        self.renderer.set_point_lights(lights)

        self.scene.update(dt)

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

    def on_resize_hook(self, width, height):
        self.renderer.resize(width, height)

    def status_text(self):
        mode = 'FLY' if self.use_fly else 'ORBIT'
        return f'{super().status_text()}   [{mode}]'

    def on_key(self, symbol, modifiers):
        key = __import__('pyglet').window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        if name in ('W', 'A', 'S', 'D', 'Q', 'E', 'SHIFT', 'LSHIFT', 'RSHIFT'):
            self._key_state = getattr(self, '_key_state', set())
            self._key_state.add('SHIFT' if 'SHIFT' in name else name)
            return
        s = chr(symbol) if 32 <= symbol < 127 else ''
        post = self.renderer.post.settings
        if s.upper() == 'B':
            post.bloom_enabled = not post.bloom_enabled
        elif s.upper() == 'O':
            post.ssao_enabled = not post.ssao_enabled
        elif s.upper() == 'V':
            self.use_fly = not self.use_fly
            if self.use_fly:
                self._sync_fly_to_orbit()

    def on_key_release(self, symbol, modifiers):
        key = __import__('pyglet').window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        getattr(self, '_key_state', set()).discard(name)
        getattr(self, '_key_state', set()).discard('SHIFT')

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        if self.use_fly:
            self.fly.look(dx, dy)
        else:
            self.camera.orbit(dx, dy)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        if not self.use_fly:
            self.camera.zoom(scroll_y)

    def _sync_fly_to_orbit(self):
        eye = tuple(self.camera.eye)
        tgt = tuple(self.camera.target)
        d = (eye[0] - tgt[0], eye[1] - tgt[1], eye[2] - tgt[2])
        l = math.sqrt(sum(c * c for c in d)) or 1.0
        d = (d[0] / l, d[1] / l, d[2] / l)
        self.fly.position = eye
        self.fly.pitch = math.degrees(math.asin(max(-1.0, min(1.0, d[1]))))
        self.fly.yaw = math.degrees(math.atan2(d[2], d[0]))
        self.fly.update(0.0)


# ------------------------------------------------------------------ 工具 --
def _yaw_quat(deg):
    from engine.mathkit import quat_from_axis_angle
    return quat_from_axis_angle((0.0, 1.0, 0.0), deg)


def _axis_quat(axis, deg):
    from engine.mathkit import quat_from_axis_angle
    return quat_from_axis_angle(axis, deg)


if __name__ == '__main__':
    sys.exit(run(MaterialLabApp))

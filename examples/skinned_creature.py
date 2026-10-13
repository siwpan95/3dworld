# -*- coding: utf-8 -*-
"""蒙皮骨骼动画 demo（P2-3）——「骨节蛇」。

加载由 ``scripts/make_skinned_gltf.py`` **程序化生成**的带 skin 的 glTF，
用蒙皮顶点着色器（``shaders.PBR_SKIN_VERT``）渲染，并驱动 8 节骨骼链摆动。

零外部素材：网格、骨骼、蒙皮权重、逆绑定矩阵全部由代码生成。

运行::

    python examples/skinned_creature.py
    python examples/skinned_creature.py --smoke     冒烟（~2s 自动关）
    python examples/skinned_creature.py --shot      抓帧到 docs/ 后关闭

按键：拖动=旋转视角  滚轮=缩放  SPACE=暂停  A=摆动开关  ESC=退出
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
from pyglet.math import Vec3                                    # noqa: E402

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.app import EngineApp, run                           # noqa: E402
from engine.camera import OrbitCamera                           # noqa: E402
from engine.env import LightEnvironment                         # noqa: E402
from engine.gltf import GLTF                                    # noqa: E402
from engine.material import PRESETS                             # noqa: E402
from engine.mesh import SkinnedMesh                             # noqa: E402
from engine.renderer import Renderer                            # noqa: E402
from engine.shaders import get                                  # noqa: E402
from engine.skin import make_skin_from_gltf                     # noqa: E402
from logic.animation import quat_from_axis_angle                # noqa: E402

GLTF_PATH = os.path.join(ROOT, 'assets', 'skinned_snake.gltf')

TITLE = 'skinned creature · 骨节蛇（P2-3 蒙皮骨骼动画）'
HELP = ('ESC quit  SPACE pause  A animate  drag orbit  wheel zoom')
SHOTS = ((0.8, 'docs/skinned_01.png'),
         (1.8, 'docs/skinned_02.png'),
         (2.8, 'docs/skinned_03.png'))


class SkinnedCreatureApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    Z_FAR = 120.0

    # ------------------------------------------------------------ 构建 --
    def setup(self):
        w, h = self.window.width, self.window.height

        self.env = LightEnvironment(
            sun_dir=(0.42, 0.68, 0.38), sun_color=(1.0, 0.95, 0.86),
            sun_intensity=1.9, ibl_intensity=0.9,
            sky_zenith=(0.15, 0.33, 0.70), sky_horizon=(0.45, 0.61, 0.82),
            sky_ground=(0.10, 0.095, 0.09)).bake()
        self.renderer = Renderer(w, h, environment=self.env)

        # ---- 载入程序化生成的蒙皮 glTF ----
        self.gltf = GLTF.load(GLTF_PATH)
        scene, pending, nmap = self.gltf.build_scene_ex()
        self.scene = scene
        self.scene.environment = self.env
        # 只对 root 调一次：update_world 会递归（逐节点调会丢父链）
        scene.root.update_world(force=True)

        self.skin = make_skin_from_gltf(self.gltf, nmap, 0)
        self.joints = self.skin.joints
        self._bind_translations = [j.translation for j in self.joints]

        # ---- 蒙皮网格（每个 primitive 一个 SkinnedMesh，挂到对应节点）----
        prog = get('pbr_skin')
        base_mat = PRESETS['emerald_light'].clone(name='snake_body')
        base_mat.metallic = 0.15
        base_mat.roughness = 0.32
        self.meshes = []
        for node, md, _mat_index in pending:
            m = SkinnedMesh(md, prog, self.skin, base_mat.clone(name='snake_body'))
            node.drawable = m                 # 直接挂到 glTF 节点上（关节同树）
            self.meshes.append(m)
        self.tri_count = sum(m.data.triangle_count for m in self.meshes)
        # ⚠️ Scene.drawables() 有缓存——setup 里新挂的 drawable 必须 invalidate 才会被收集，
        # 否则渲染器看不到任何东西（画面只剩天空）。
        self.scene.invalidate()

        self.camera = OrbitCamera(target=(0.0, 0.0, 3.5), yaw=36.0, pitch=14.0,
                                  dist=14.0, dist_range=(4.0, 45.0),
                                  fov=self.FOV, far=self.Z_FAR)

        self.t = 0.0
        self.animate = True
        self.paused = False
        self.scene.update()

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        self.t += dt
        if self.animate:
            # 每节绕 X 轴正弦摆动（相位沿链错开）→ 蛇身波动
            for i, j in enumerate(self.joints):
                ang = 0.55 * math.sin(self.t * 1.7 - i * 0.55)
                j.rotation = quat_from_axis_angle(Vec3(1.0, 0.0, 0.0), ang)
                j.mark_dirty()
        # 关节世界矩阵与蒙皮矩阵每帧刷新（SkinnedMesh.draw 里读）
        self.scene.root.update_world()

    def render(self):
        aspect = self.window.width / max(1, self.window.height)
        self.renderer.render(self.camera, self.scene, aspect=aspect,
                             window=self.window)

    # ------------------------------------------------------------ 交互 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.SPACE:
            self.paused = not self.paused
        elif symbol == key.A:
            self.animate = not self.animate
        elif symbol == key.R:
            for j, t0 in zip(self.joints, self._bind_translations):
                j.translation = t0
                j.rotation = (1.0, 0.0, 0.0, 0.0)
                j.mark_dirty()
            self.t = 0.0
        elif symbol == key.ESCAPE:
            self.close()
        else:
            return pyglet.event.EVENT_UNHANDLED
        return None

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.camera.orbit(dx, dy)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        self.camera.zoom(scroll_y)

    def status_text(self):
        return (f'关节 {len(self.joints)} · 三角 {self.tri_count} · '
                f'摆动 {"开" if self.animate else "关"} · '
                f'{"暂停" if self.paused else "播放"} t={self.t:.1f}s')


def main(argv=None):
    return run(SkinnedCreatureApp, argv)


if __name__ == '__main__':
    sys.exit(main())

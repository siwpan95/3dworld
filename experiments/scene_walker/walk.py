# -*- coding: utf-8 -*-
"""scene_walker —— 可漫步场景雏形（交互化第一小步，实验）。

把三块既有成果组合成「可交互场景」的模板：

    S1 场景 JSON（engine.scene_build 构建，含几何/材质/环境）
    + FixedStepper（渲染帧率与物理解耦，卡帧不穿透）
    + 物理漫步（AABB 走墙/站地/跳跃；场景几何自动注册为碰撞）
    + 发光元素呼吸（演出活性的最小体现；字体走 engine.fonts）

用法::

    <项目 .venv python> experiments/scene_walker/walk.py
    ... --file docs/llm_scene_demo.json --smoke 3 --flag walk

键位：V 轨道/漫游切换   WASD 走   SPACE 跳（漫游态）   SHIFT 加速
      鼠标拖动：轨道环绕 / 漫游转视角
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'demos'))

import pyglet                                                       # noqa: E402

from _harness import (DemoApp, PROJECT_ROOT, orbit_camera_input,    # noqa: E402
                      run)

DEFAULT_JSON = os.path.join(PROJECT_ROOT, 'docs', 'llm_scene_demo.json')
_ENV_KEYS = ('sun_dir', 'sun_color', 'sun_intensity', 'sky_zenith',
             'sky_horizon', 'sky_ground', 'ibl_intensity', 'sky_intensity',
             'env_size', 'prefilter_levels')


def _extract_file(argv):
    args = list(argv)
    path = None
    if '--file' in args:
        i = args.index('--file')
        if i + 1 < len(args):
            path = args[i + 1]
            del args[i:i + 2]
    else:
        for i, a in enumerate(args):
            if a.startswith('--file='):
                path = a.split('=', 1)[1]
                del args[i]
                break
    return path, args


class WalkerApp(DemoApp):
    TITLE = 'scene walker · 可漫步场景（实验）'
    HELP = 'V 轨道/漫游   WASD 走   SPACE 跳   SHIFT 加速   鼠标拖动看'
    FOV = 60.0
    Z_FAR = 600.0
    JSON_PATH = None

    WALK_SPEED = 4.2          # m/s
    SPRINT = 1.9
    JUMP_V = 7.0              # 跳起 ~1.1m（g=-22）
    EYE = 0.7                 # 眼高（相对身体中心）

    def __init__(self, width, height, args=None):
        self._json_path = os.path.abspath(self.JSON_PATH or DEFAULT_JSON)
        self._keys = set()
        self.walk = False
        self.yaw, self.pitch = 180.0, -6.0
        self._landed_reported = False
        super().__init__(width, height, args=args)

    # -------------------------------------------------------------- setup --
    def setup(self):
        from engine.camera import FlyCamera, OrbitCamera
        from engine.env import LightEnvironment
        from engine.fixedstep import FixedStepper
        from engine.physics import Body, PhysicsWorld
        from engine.renderer import Renderer
        from engine.scene_build import build_scene, load_json
        from engine.shaders import get

        self._data = load_json(self._json_path)
        env_def = self._data.get('environment') or {}
        self.env = LightEnvironment(
            **{k: v for k, v in env_def.items() if k in _ENV_KEYS}).bake()
        self.renderer = Renderer(self.window.width, self.window.height,
                                 environment=self.env)
        self.scene = build_scene(self._data, get('pbr'))

        # 物理：场景几何自动注册为静态碰撞（世界 AABB）
        self.physics = PhysicsWorld(gravity=-22.0, cell=6.0)
        self.scene.update()
        for n in self.scene.root.traverse():
            if n.drawable is not None:
                box = getattr(n.drawable, 'world_aabb', None)
                if box is not None:
                    self.physics.add_static(box, n.name)

        # 漫步者 + 固定步长驱动（卡帧不穿透；见 engine/fixedstep.py）
        self.body = Body(position=(9.5, 2.5, 0.0), half=(0.3, 0.9, 0.3))
        self.physics.add_body(self.body)
        self.stepper = FixedStepper(lambda dt: self.physics.step(dt),
                                    fixed_dt=1.0 / 60.0)

        # 相机：轨道（默认）+ 第一人称容器（复用 FlyCamera 的 look/朝向模型）
        self.camera = OrbitCamera(target=(0.0, 3.0, 0.0), yaw=35.0,
                                  pitch=20.0, dist=24.0,
                                  dist_range=(4.0, 160.0),
                                  fov=self.FOV, far=self.Z_FAR)
        self.eye = FlyCamera(position=(9.5, 3.2, 0.0), fov=self.FOV,
                             far=self.Z_FAR)
        self._drag_orbit_input = orbit_camera_input(self, self.camera)
        self.on_mouse_drag, self.on_mouse_scroll = (
            self._drag_dispatch, self._drag_orbit_input[1])
        self.window.push_handlers(on_key_release=self._on_key_release)

        # 发光元素基线（呼吸动画不累积乘）
        self._glow = {}
        for n in self.scene.root.traverse():
            d = n.drawable
            if d is not None and getattr(d, 'material', None) is not None:
                e = d.material.emissive or (0.0, 0.0, 0.0)
                if sum(e) > 0.05:
                    self._glow[n.name] = tuple(e)

        # 演出变体（可选）：zhuangtaiji 马尔可夫链驱动发光模式；
        # 不可用/未配置时回退为时间正弦呼吸（见 _breathe）。
        self._mc, self._mc_t, self._mc_gain = None, 0.0, {}
        try:
            z = os.environ.get('ZHUANGTAIJI_PATH', r'G:\repos\zhuangtaiji')
            if os.path.isdir(z):
                if z not in sys.path:
                    sys.path.insert(0, z)
                from core.markov_chain import MarkovChain
                self._mc = MarkovChain(
                    ['dim', 'glow', 'spark', 'flare'],
                    {'dim': {'dim': 0.55, 'glow': 0.40, 'spark': 0.05, 'flare': 0.0},
                     'glow': {'dim': 0.35, 'glow': 0.40, 'spark': 0.20, 'flare': 0.05},
                     'spark': {'dim': 0.20, 'glow': 0.35, 'spark': 0.30, 'flare': 0.15},
                     'flare': {'dim': 0.45, 'glow': 0.30, 'spark': 0.20, 'flare': 0.05}},
                    initial='dim')
                self._mc_gain = {'dim': 0.55, 'glow': 0.9,
                                 'spark': 1.35, 'flare': 1.9}
        except Exception:
            self._mc = None

        if 'walk' in self.args.flags:              # --flag walk：直接进入漫游
            self.walk = True
            self._sync_eye()

    # -------------------------------------------------------------- 输入 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        if name in ('W', 'A', 'S', 'D', 'SHIFT', 'LSHIFT', 'RSHIFT'):
            self._keys.add('SHIFT' if 'SHIFT' in name else name)
            return
        if name == 'V':
            self.walk = not self.walk
            if self.walk:
                self._sync_eye()

    def _on_key_release(self, symbol, modifiers):
        key = pyglet.window.key
        name = getattr(key, 'symbol_string', lambda s: '')(symbol)
        self._keys.discard(name)
        if 'SHIFT' in name:
            self._keys.discard('SHIFT')

    def _on_key_press(self, symbol, modifiers):
        # 漫游态：SPACE = 跳跃（覆盖基类的暂停；其余键走基类）
        if self.walk and symbol == pyglet.window.key.SPACE:
            if self.body.on_ground:
                v = self.body.velocity
                self.body.velocity = (v[0], self.JUMP_V, v[2])
            return
        super()._on_key_press(symbol, modifiers)

    def _drag_dispatch(self, x, y, dx, dy, buttons, modifiers):
        if self.walk:
            self.yaw += dx * 0.28
            self.pitch = max(-80.0, min(80.0, self.pitch - dy * 0.28))
        else:
            self._drag_orbit_input[0](x, y, dx, dy, buttons, modifiers)

    def _sync_eye(self):
        p = self.body.position
        self.eye.position = (p[0], p[1] + self.EYE, p[2])
        self.eye.yaw = self.yaw
        self.eye.pitch = self.pitch
        self.eye.update(0.0)

    # ------------------------------------------------------------ 主循环 --
    def update(self, dt):
        if self.walk:
            ks = self._keys
            f = (1.0 if 'W' in ks else 0.0) - (1.0 if 'S' in ks else 0.0)
            r = (1.0 if 'D' in ks else 0.0) - (1.0 if 'A' in ks else 0.0)
            yaw_r = math.radians(self.yaw)
            fx, fz = math.cos(yaw_r), math.sin(yaw_r)      # 水平前向（FlyCamera 约定）
            rx, rz = -fz, fx
            speed = self.WALK_SPEED * (self.SPRINT if 'SHIFT' in ks else 1.0)
            vx, vz = f * fx + r * rx, f * fz + r * rz
            ln = math.hypot(vx, vz)
            vel = list(self.body.velocity)
            if ln > 1e-6:
                vel[0], vel[2] = vx / ln * speed, vz / ln * speed
            else:
                vel[0] = vel[2] = 0.0
            self.body.velocity = tuple(vel)
            self.stepper.advance(dt)                        # 固定步长驱动物理
            self._sync_eye()
            if not self._landed_reported and self.body.on_ground:
                self._landed_reported = True
                p = self.body.position
                print(f'[walk] landed at ({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}) '
                      f'（物理与固定步长工作正常）', flush=True)
        self._breathe(self.time)
        self.scene.update(dt)

    def _breathe(self, t):
        """发光元素活性：马尔可夫模式（演出变体）或正弦呼吸（回退）。"""
        if self._mc is not None:
            if t - self._mc_t >= 1.6:              # 每 1.6s 演化一步（随机变体）
                self._mc_t = t
                self._mc.step()
                print(f'[mc] ember -> {self._mc.current}', flush=True)
            gain = self._mc_gain.get(self._mc.current, 1.0)
            for name, base in self._glow.items():
                n = self.scene.root.find(name)
                d = n.drawable if n is not None else None
                if d is not None and getattr(d, 'material', None) is not None:
                    d.material.emissive = tuple(c * gain for c in base)
            return
        for i, (name, base) in enumerate(self._glow.items()):
            n = self.scene.root.find(name)
            d = n.drawable if n is not None else None
            if d is not None and getattr(d, 'material', None) is not None:
                k = 0.75 + 0.45 * (0.5 + 0.5 * math.sin(t * 1.7 + i * 1.31))
                d.material.emissive = tuple(c * k for c in base)

    def render(self):
        cam = self.eye if self.walk else self.camera
        w, h = self.window.width, self.window.height
        self.renderer.render(cam, self.scene, w / float(h), self.window)

    def _on_resize(self, width, height):
        result = super()._on_resize(width, height)
        if hasattr(self, 'renderer'):
            self.renderer.resize(width, height)
        return result

    def status_text(self):
        base = super().status_text()
        if self.walk:
            p = self.body.position
            mc = self._mc.current if self._mc is not None else 'breath'
            return (f'{base}   [WALK] pos=({p[0]:5.1f},{p[1]:4.1f},{p[2]:5.1f}) '
                    f'ground={int(self.body.on_ground)} ember={mc}')
        return f'{base}   [ORBIT]   V 进入漫游'


def main():
    path, argv = _extract_file(sys.argv[1:])
    if path:
        WalkerApp.JSON_PATH = path
    run(WalkerApp, argv)


if __name__ == '__main__':
    main()

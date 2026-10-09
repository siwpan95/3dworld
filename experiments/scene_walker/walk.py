# -*- coding: utf-8 -*-
"""scene_walker —— 可漫步场景（交互化深化版 v3）。

在 v2（S1 场景 + FixedStepper 物理漫步 + 马尔可夫演出变体）基础上深化交互：

    v3 新增——拾取与近身互动：
      鼠标点击 → 拾取场景元素（Picker 射线）→ 状态行显示命中
      E 键 → 唤醒**最近**的发光元素（脉冲高亮 + 强制马尔可夫档位回升）
      交互反馈 = 发光脉冲（SFX 音效为下一层，见 README）

用法::

    <项目 venv python> experiments/scene_walker/walk.py
    ... --smoke 6 --flag walk --flag interact   # 无键盘环境自检：自动触发一次互动

键位：V 轨道/漫游   WASD 走   SPACE 跳（漫游）   SHIFT 加速
      E 近身互动（漫游）   鼠标拖动：轨道环绕 / 漫游转视角   点击：拾取
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
from bgm import AdaptiveBgm                                         # noqa: E402

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


class SfxPlayer:
    """轻量音效播放：pyglet.media 预载 WAV；失败/缺设备时静默降级。

    - 无音频设备（服务器/沙箱）或缺 wav 文件 → disabled（不中断交互）；
    - 可用 WORLD3D_SFX=0 显式禁用。
    """

    def __init__(self, sfx_dir, enabled=True, gain=1.0):
        self._sources = {}
        self._gain = gain
        self.enabled = bool(enabled) and os.environ.get('WORLD3D_SFX', '1') != '0'
        if not self.enabled:
            print('[sfx] disabled by env', flush=True)
            return
        try:
            import pyglet.media as pm
            for name in ('jump', 'pickup', 'power_up', 'land'):
                p = os.path.join(sfx_dir, f'{name}.wav')
                if os.path.exists(p):
                    self._sources[name] = pm.load(p, streaming=False)
            print(f'[sfx] ready: {sorted(self._sources)}', flush=True)
        except Exception as e:
            print(f'[sfx] disabled: {type(e).__name__}: {e}', flush=True)
            self.enabled = False

    def play(self, name, gain=1.0):
        if not self.enabled or name not in self._sources:
            return
        try:
            p = self._sources[name].play()
            if p is not None:
                p.volume = max(0.0, min(1.0, self._gain * gain))
            print(f'[sfx] play {name}', flush=True)
        except Exception as e:
            print(f'[sfx] play failed ({name}): {e}', flush=True)


class WalkerApp(DemoApp):
    TITLE = 'scene walker · 可漫步场景（实验 v3）'
    HELP = ('V 轨道/漫游  WASD 走  SPACE 跳  SHIFT 加速  E 互动  '
            '鼠标拖动看  点击拾取')
    FOV = 60.0
    Z_FAR = 600.0
    JSON_PATH = None

    WALK_SPEED = 4.2          # m/s
    SPRINT = 1.9
    JUMP_V = 7.0              # 跳起 ~1.1m（g=-22）
    EYE = 0.7                 # 眼高（相对身体中心）
    REACH = 6.5               # 近身互动半径（m）

    def __init__(self, width, height, args=None):
        self._json_path = os.path.abspath(self.JSON_PATH or DEFAULT_JSON)
        self._keys = set()
        self.walk = False
        self.yaw, self.pitch = 180.0, -6.0
        self._landed_reported = False
        self._was_ground = False               # 落地边沿（音效反馈用）
        self.selected = None
        self._pulse = {}                       # name -> 高亮截止时刻
        self._msg = ''
        self._auto_t = None                    # --flag interact：自检触发时刻
        super().__init__(width, height, args=args)

    # -------------------------------------------------------------- setup --
    def setup(self):
        from engine.camera import FlyCamera, OrbitCamera
        from engine.env import LightEnvironment
        from engine.fixedstep import FixedStepper
        from engine.physics import Body, PhysicsWorld
        from engine.picking import Picker
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
        self.picker = Picker()                 # v3：拾取

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
        self.window.push_handlers(on_key_release=self._on_key_release,
                                  on_mouse_press=self.on_mouse_press)

        # 交互音效（v3.1）：预载 SFX；无设备/缺文件时静默降级
        self.sfx = SfxPlayer(os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'sfx'))
        # 自适应 BGM（v3.2）：bloodmusic interactive 引擎（失败自降级）
        self.bgm = AdaptiveBgm()
        self.window.push_handlers(on_close=self._on_close)

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
        if 'interact' in self.args.flags:          # --flag interact：自检互动
            self._auto_t = 1.2
            if 'walk' not in self.args.flags:
                self.walk = True
                self._sync_eye()
            self.body.position = (5.0, 2.5, 0.0)   # 近移（确保在互动半径内）

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
        elif name == 'E':
            self._interact()

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
                self.sfx.play('jump')
            return
        super()._on_key_press(symbol, modifiers)

    def _drag_dispatch(self, x, y, dx, dy, buttons, modifiers):
        if self.walk:
            self.yaw += dx * 0.28
            self.pitch = max(-80.0, min(80.0, self.pitch - dy * 0.28))
        else:
            self._drag_orbit_input[0](x, y, dx, dy, buttons, modifiers)

    def on_mouse_press(self, x, y, button, modifiers):
        """v3 拾取：射线命中场景元素 → 记录选中并显示距离。"""
        cam = self.eye if self.walk else self.camera
        w, h = self.window.width, self.window.height
        try:
            hit = self.picker.pick(x, y, w, h, cam, self.scene,
                                   aspect=w / float(h), precise=True)
        except Exception as e:
            self._msg = f'pick failed: {e}'
            return
        if hit is None:
            self.selected = None
            self._msg = '拾取：未命中'
            return
        self.selected = hit.drawable
        self._msg = f'拾取 {hit.drawable.name} 距离 {hit.distance:.1f}m'
        self.sfx.play('pickup')
        self.bgm.arouse(3.0, 0.42)     # 轻微警觉（v3.2）

    # ---------------------------------------------------------- 近身互动 --
    def _nearest_glow(self):
        """返回 (name, 距离) —— 最近的发光元素（限 REACH 内）。"""
        p = self.body.position
        best, bd = None, self.REACH
        for name in self._glow:
            n = self.scene.root.find(name)
            d = n.drawable if n is not None else None
            box = getattr(d, 'world_aabb', None) if d is not None else None
            if box is None:
                continue
            c = box.center
            dist = math.dist((c[0], c[1], c[2]), p)
            if dist < bd:
                best, bd = name, dist
        return (best, bd) if best else None

    def _interact(self):
        """v3：唤醒最近发光元素（脉冲高亮 + 马尔可夫档位回升）。"""
        if not self.walk:
            self._msg = '先按 V 进入漫游，再近身互动'
            return
        tgt = self._nearest_glow()
        if tgt is None:
            self._msg = '附近没有可互动的元素'
            print('[interact] no target within reach', flush=True)
            return
        name, dist = tgt
        self._pulse[name] = self.time + 1.2
        self._msg = f'唤醒 {name}（距离 {dist:.1f}m）'
        self.sfx.play('power_up')
        self.bgm.arouse(6.0, 0.55)     # 音乐进入 TENSION（v3.2）
        if self._mc is not None:
            for _ in range(3):                 # 简单策略：最多 3 步离开 dim
                if self._mc.current != 'dim':
                    break
                self._mc.step()
            print(f'[interact] wake {name} -> ember {self._mc.current}',
                  flush=True)
        else:
            print(f'[interact] wake {name} (breath mode)', flush=True)

    def _on_close(self):
        """退出前停掉 BGM 流（尽力；不拦截默认关闭流程）。"""
        try:
            self.bgm.close()
        except Exception:
            pass

    def _sync_eye(self):
        p = self.body.position
        self.eye.position = (p[0], p[1] + self.EYE, p[2])
        self.eye.yaw = self.yaw
        self.eye.pitch = self.pitch
        self.eye.update(0.0)

    # ------------------------------------------------------------ 主循环 --
    def update(self, dt):
        if self._auto_t is not None and self.time >= self._auto_t:
            self._auto_t = None
            print('[selftest] auto interact', flush=True)
            self._interact()
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
            if self.body.on_ground and not self._was_ground:
                self.sfx.play('land', 0.6)                  # 落地反馈音
            self._was_ground = self.body.on_ground
            self._sync_eye()
            if not self._landed_reported and self.body.on_ground:
                self._landed_reported = True
                p = self.body.position
                print(f'[walk] landed at ({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}) '
                      f'（物理与固定步长工作正常）', flush=True)
        self.bgm.tick(dt)              # 自适应 BGM：警觉衰减（v3.2）
        self._breathe(self.time)
        self.scene.update(dt)

    def _breathe(self, t):
        """发光元素活性：马尔可夫模式（演出变体）或正弦呼吸（回退）；
        叠加交互脉冲（v3）。"""
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
                    g = gain * (1.6 if t < self._pulse.get(name, -1.0) else 1.0)
                    d.material.emissive = tuple(c * g for c in base)
            return
        for i, (name, base) in enumerate(self._glow.items()):
            n = self.scene.root.find(name)
            d = n.drawable if n is not None else None
            if d is not None and getattr(d, 'material', None) is not None:
                k = 0.75 + 0.45 * (0.5 + 0.5 * math.sin(t * 1.7 + i * 1.31))
                if t < self._pulse.get(name, -1.0):
                    k *= 1.6
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
        msg = f'   {self._msg}' if self._msg else ''
        if self.walk:
            p = self.body.position
            mc = self._mc.current if self._mc is not None else 'breath'
            return (f'{base}   [WALK] pos=({p[0]:5.1f},{p[1]:4.1f},{p[2]:5.1f}) '
                    f'ground={int(self.body.on_ground)} ember={mc} '
                    f'bgm={self.bgm.state_name}{msg}')
        return f'{base}   [ORBIT]   V 进入漫游{msg}'


def main():
    path, argv = _extract_file(sys.argv[1:])
    if path:
        WalkerApp.JSON_PATH = path
    run(WalkerApp, argv)


if __name__ == '__main__':
    main()

# -*- coding: utf-8 -*-
"""scene walker —— 可漫步场景（S1 场景 + 交互 + 自适应音景）。

一个「可交互场景」的完整示例：把 S1 的 LLM 场景 JSON 变成可以走进去的空间。

    - 物理漫步：AABB 走墙/站地/跳跃，场景几何自动注册为碰撞；
      物理由 FixedStepper 以固定步长驱动（渲染帧率无关、卡帧不穿透）；
    - 演出变体：zhuangtaiji 马尔可夫链驱动发光元素（氛围每次不同，
      不可用时回退正弦呼吸）；
    - 交互：点击拾取场景元素；E 键唤醒**最近**的发光元素（脉冲高亮）；
      **玩法目标**：唤醒全部 3 个 orb —— 音乐叙事随之推进
      （探索→警觉→紧张→胜利；HUD 显示唤醒进度）；
    - 音景：bloodmusic SFX 反馈音（跳跃/落地/拾取/互动；`assets/sfx/*.wav`，
      由 scripts/gen_sfx.py 生成）+ **自适应 BGM**（互动/拾取推高张力，
      EXPLORE ⇄ TENSION 平滑过渡；需安装 bloodmusic，缺失则静默降级）；
    - 热重载：编辑场景 JSON 即时套用（TRS + 碰撞同步重建）。

全部音景能力均为**可选增强**：缺资产/缺库/无设备时自动静默降级，
核心（场景 + 漫步 + 交互）仅依赖 pyglet。

用法::

    python demos/scene_walker.py
    python demos/scene_walker.py --file docs/llm_scene_demo.json
    python demos/scene_walker.py --smoke 6 --flag walk,interact   # 自检

键位：V 轨道/漫游   WASD 走   SPACE 跳（漫游）   SHIFT 加速
      E 近身互动（漫游）   鼠标拖动：轨道环绕 / 漫游转视角   点击：拾取
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'demos'))

import pyglet                                                       # noqa: E402

from _harness import (DemoApp, PROJECT_ROOT, orbit_camera_input,    # noqa: E402
                      run)
from engine.adaptive_music import AdaptiveBgm                       # noqa: E402
from engine.audio import SfxPlayer                                  # noqa: E402

DEFAULT_JSON = os.path.join(PROJECT_ROOT, 'docs', 'llm_scene_demo.json')
SFX_DIR = os.path.join(PROJECT_ROOT, 'assets', 'sfx')
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
    TITLE = 'scene walker · 可漫步场景（S1 场景 + 交互 + 自适应音景）'
    HELP = ('V 轨道/漫游  WASD 走  SPACE 跳  SHIFT 加速  E 互动  '
            '鼠标拖动看  点击拾取')
    FOV = 60.0
    Z_FAR = 600.0
    JSON_PATH = None
    # 抓帧（scripts/verify_demos.py 验收用；相对项目根）
    SHOTS = ((2.5, os.path.join('docs', 'scene_walker_01.png')),)

    WALK_SPEED = 4.2          # m/s
    SPRINT = 1.9
    JUMP_V = 7.0              # 跳起 ~1.1m（g=-22）
    EYE = 0.7                 # 眼高（相对身体中心）
    REACH = 6.5               # 近身互动半径（m）

    def __init__(self, width, height, args=None):
        self._json_path = os.path.abspath(self.JSON_PATH or DEFAULT_JSON)
        self._tmp_json = None                  # --flag hotreload 用
        self._keys = set()
        self.walk = False
        self.yaw, self.pitch = 180.0, -6.0
        self._landed_reported = False
        self._was_ground = False
        self.selected = None
        self._pulse = {}                       # name -> 高亮截止时刻
        self._msg = ''
        self._auto_t = None                    # --flag interact 自检
        self._hot_t = None                     # --flag hotreload 自检
        self._awakened = set()                 # 已唤醒元素（玩法目标：3 个 orb）
        self._victory_until = 0.0              # 胜利余韵截止
        self._victory_t = None                 # --flag victory 自检
        super().__init__(width, height, args=args)

    # -------------------------------------------------------------- setup --
    def setup(self):
        import tempfile

        from engine.camera import FlyCamera, OrbitCamera
        from engine.env import LightEnvironment
        from engine.fixedstep import FixedStepper
        from engine.physics import Body, PhysicsWorld
        from engine.picking import Picker
        from engine.renderer import Renderer
        from engine.scene_build import build_scene, load_json
        from engine.serialize import SceneWatcher
        from engine.shaders import get

        # --flag hotreload 自检：用临时副本，避免动到仓库里的场景文件
        if 'hotreload' in self.args.flags:
            self._tmp_json = os.path.join(tempfile.gettempdir(),
                                          'world3d_scene_hot.json')
            with open(self._json_path, encoding='utf-8') as f:
                data = f.read()
            with open(self._tmp_json, 'w', encoding='utf-8') as f:
                f.write(data)
            self._json_path = self._tmp_json
            self._hot_t = 3.0

        self._data = load_json(self._json_path)
        env_def = self._data.get('environment') or {}
        self.env = LightEnvironment(
            **{k: v for k, v in env_def.items() if k in _ENV_KEYS}).bake()
        self.renderer = Renderer(self.window.width, self.window.height,
                                 environment=self.env)
        self.scene = build_scene(self._data, get('pbr'))
        self.picker = Picker()

        # 物理：场景几何自动注册为静态碰撞（世界 AABB；热重载时重建）
        self.physics = PhysicsWorld(gravity=-22.0, cell=6.0)
        self._rebuild_statics()

        # 漫步者 + 固定步长驱动
        self.body = Body(position=(9.5, 2.5, 0.0), half=(0.3, 0.9, 0.3))
        self.physics.add_body(self.body)
        self.stepper = FixedStepper(lambda dt: self.physics.step(dt),
                                    fixed_dt=1.0 / 60.0)

        # 相机：轨道（默认）+ 第一人称容器
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

        # 交互音效（可选增强：缺 assets/sfx/*.wav 时静默降级）
        self.sfx = SfxPlayer(SFX_DIR)
        # 自适应 BGM（可选增强：缺 bloodmusic 时静默降级）
        self.bgm = AdaptiveBgm()
        self.window.push_handlers(on_close=self._on_close)

        # 发光元素基线（呼吸/变体不累积乘）
        self._glow = {}
        for n in self.scene.root.traverse():
            d = n.drawable
            if d is not None and getattr(d, 'material', None) is not None:
                e = d.material.emissive or (0.0, 0.0, 0.0)
                if sum(e) > 0.05:
                    self._glow[n.name] = tuple(e)

        # 演出变体（可选）：zhuangtaiji 马尔可夫链驱动发光模式
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

        # 场景热重载（编辑 JSON 即时套用 TRS，碰撞同步重建）
        self.watcher = SceneWatcher(self._json_path,
                                    on_reload=self._on_scene_reloaded)

        if 'walk' in self.args.flags:
            self.walk = True
            self._sync_eye()
        if 'interact' in self.args.flags:
            self._auto_t = 1.2
            if 'walk' not in self.args.flags:
                self.walk = True
                self._sync_eye()
            self.body.position = (5.0, 2.5, 0.0)   # 近移确保在互动半径内
        if 'victory' in self.args.flags:
            self._victory_t = 3.0
            if 'walk' not in self.args.flags:
                self.walk = True
                self._sync_eye()

    def _rebuild_statics(self):
        """（重建）把场景几何注册为静态碰撞——热重载后调用以同步碰撞。"""
        self.physics.statics.clear()
        self.physics._hash_dirty = True
        self.scene.update()
        for n in self.scene.root.traverse():
            if n.drawable is not None:
                box = getattr(n.drawable, 'world_aabb', None)
                if box is not None:
                    self.physics.add_static(box, n.name)

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
        """拾取：射线命中场景元素 → 记录选中并显示距离。"""
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
        self.bgm.arouse(3.0, 0.42)

    # ------------------------------------------------------------ 热重载 --
    def _on_scene_reloaded(self, loaded):
        """套用节点 TRS（几何不重建；JSON 里没有顶点）并同步重建碰撞。"""
        count = 0
        for src in loaded.root.traverse():
            dst = self.scene.root.find(src.name)
            if dst is None:
                continue
            dst.set_translation(src.translation)
            dst.set_rotation(src.rotation)
            dst.set_scale(src.scale)
            count += 1
        self._rebuild_statics()
        print(f'[hotreload] applied {count} nodes', flush=True)
        self._msg = f'热重载 {count} 个节点'

    # ---------------------------------------------------------- 近身互动 --
    def _nearest_glow(self):
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
        """唤醒最近发光元素（脉冲高亮 + 马尔可夫档位回升 + 音景反馈）。"""
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
        if name not in self._awakened:
            self._awakened.add(name)
            self._update_music_stage(len(self._awakened))
        if self._mc is not None:
            for _ in range(3):
                if self._mc.current != 'dim':
                    break
                self._mc.step()
            print(f'[interact] wake {name} -> ember {self._mc.current}',
                  flush=True)
        else:
            print(f'[interact] wake {name} (breath mode)', flush=True)

    def _update_music_stage(self, n):
        """唤醒进度 -> 音乐叙事阶段：探索 → 警觉 → 紧张 → 胜利。"""
        if n >= 3:
            self.bgm.set_extra({'victory': True})
            self._victory_until = self.time + 6.0
            print('[goals] all orbs awakened -> VICTORY', flush=True)
        elif n == 2:
            self.bgm.set_extra({'enemy_count': 3})     # 引擎阈值 -> COMBAT_LOW
            print('[goals] 2/3 awakened -> COMBAT_LOW', flush=True)
        else:
            self.bgm.arouse(6.0, 0.55)                 # -> TENSION

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
        try:
            self._update_impl(dt)
        except Exception:
            import traceback
            print('[update] EXC:\n' + traceback.format_exc(), flush=True)
            self._msg = 'update 异常（见日志）'

    def _update_impl(self, dt):
        if self._auto_t is not None and self.time >= self._auto_t:
            self._auto_t = None
            print('[selftest] auto interact', flush=True)
            self._interact()
        if self._hot_t is not None and self.time >= self._hot_t:
            self._hot_t = None
            self._hotreload_step()
        if self._victory_t is not None and self.time >= self._victory_t:
            self._victory_t = None
            print('[selftest] auto victory', flush=True)
            self._awakened = {'orb_a', 'orb_b', 'orb_c'}
            self._update_music_stage(3)
        if self._victory_until and self.time >= self._victory_until:
            self._victory_until = 0.0
            self.bgm.set_extra({})          # 胜利余韵结束，回到探索
        if self.walk:
            ks = self._keys
            f = (1.0 if 'W' in ks else 0.0) - (1.0 if 'S' in ks else 0.0)
            r = (1.0 if 'D' in ks else 0.0) - (1.0 if 'A' in ks else 0.0)
            yaw_r = math.radians(self.yaw)
            fx, fz = math.cos(yaw_r), math.sin(yaw_r)
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
                self.sfx.play('land', 0.6)
            self._was_ground = self.body.on_ground
            self._sync_eye()
            if not self._landed_reported and self.body.on_ground:
                self._landed_reported = True
                p = self.body.position
                print(f'[walk] landed at ({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}) '
                      f'（物理与固定步长工作正常）', flush=True)
        self.bgm.tick(dt)              # 自适应 BGM：警觉衰减
        try:
            self.watcher.poll()        # 场景热重载：改 JSON 即生效
        except Exception as e:         # 重载失败不打断主循环
            print(f'[hotreload] poll failed: {type(e).__name__}: {e}',
                  flush=True)
        self._breathe(self.time)
        self.scene.update(dt)

    def _hotreload_step(self):
        """--flag hotreload 自检：改写临时场景 JSON 的节点位移。"""
        import json
        try:
            with open(self._json_path, encoding='utf-8') as f:
                data = json.load(f)
            nodes = data.get('nodes')
            if nodes is None:
                root = data.get('root') or {}
                nodes = root.get('children') or []
            hit = None
            for nd in nodes:
                if isinstance(nd, dict) and 'translation' in nd:
                    hit = nd
                    break
            if hit is not None:
                tr = list(hit['translation'])
                tr[1] = float(tr[1]) + 0.6
                hit['translation'] = tr
                with open(self._json_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=1)
                print('[selftest] hotreload: scene json modified', flush=True)
            else:
                os.utime(self._json_path)      # 兜底：只动 mtime 触发重载
                print('[selftest] hotreload: touched (no translation node)',
                      flush=True)
        except Exception as e:
            print(f'[selftest] hotreload failed: {e}', flush=True)

    def _breathe(self, t):
        """发光元素活性：马尔可夫模式（演出变体）或正弦呼吸（回退）；
        叠加交互脉冲。"""
        if self._mc is not None:
            if t - self._mc_t >= 1.6:
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
                    f'bgm={self.bgm.state_name} 唤醒{len(self._awakened)}/3{msg}')
        return f'{base}   [ORBIT]   V 进入漫游{msg}'


def main():
    path, argv = _extract_file(sys.argv[1:])
    if path:
        WalkerApp.JSON_PATH = path
    run(WalkerApp, argv)


if __name__ == '__main__':
    main()

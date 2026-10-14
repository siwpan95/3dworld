# -*- coding: utf-8 -*-
"""阈廊 / Liminall —— 后室循环走廊恐怖（kkrieger 基座 · P.T. 机制致敬）。

机制致敬 `P.T.`(2014) 的「循环走廊重装修」，题材取后室 Level 0 公共要素——
**原创命名 / 美术 / 叙事**（合规见 `docs/remake_ideas.md` §7 / §17）。
设计 note §17；先问结论：三幕混合 / 分阶段威胁 / 收音机+涂鸦 / 8 loops 3 谜。

分层（kkrieger 家族形态：逻辑纯数学、表现自管）：

- ``world3d/logic/liminall.py``——loop 状态机 / 8 圈事件表 / 实体 stalk AI /
  三谜题（纯数学，tests/test_liminall.py 全覆盖）；
- ``presentation/liminall_scene.py``——LimRenderer（KKRenderer 同款范式）；
- 本文件——EngineApp 装配：第一人称控制器 + P.T. 两动作（走 / zoom）+
  上下文交互（E）+ 手电（F）+ jump scare 演出 + 广播字幕。

操作：WASD 走   拖拽 转视角   SHIFT 疾走   右键按住 zoom   E 交互
      F 手电（L7/L8 照它它会退）   R 重开   SPACE 暂停   ESC 退出
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyglet
from pyglet.gl import GL_DEPTH_TEST, glDisable, glClearColor, glEnable

from world3d.engine.app import EngineApp, run
from world3d.logic.liminall import (BASE_FOV, HALL_A_LEN, RADIO_LINES,
                                    ZOOM_FOV, LiminallKernel)
from world3d.logic.liminall_audio import ensure_sfx
from world3d.presentation.liminall_scene import LimRenderer
from world3d.presentation.shot import capture_window

TITLE = '阈廊 / Liminall — 后室循环走廊（P.T. 机制致敬 · 8 loops）'
HELP = ('WASD 走   拖拽 转视角   SHIFT 疾走   右键按住 zoom   E 交互   '
        'F 手电   R 重开   SPACE 暂停   ESC 退出')
SHOTS = ((14.0, 'docs/liminall_01.png'),        # 走廊·谜题 1 段落
         (100.0, 'docs/liminall_02.png'))       # 后室迷宫（红潮前后）
HOTSPOTS = (
    ('radio', 0.9, 8.0), ('lamp', 0.5, 6.0), ('mirror_cab', -1.05, 9.0),
    ('cabinet_keypad', -1.05, 9.0), ('start_door', -HALL_B_LEN + 0.5, 0.0),
    ('phone', 0.7, 4.0),
)
INTERACT_RANGE = 1.9


class LimApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS

    def setup(self):
        self.k = LiminallKernel(self._seed())
        self.renderer = LimRenderer(self.k, self.window)
        self.auto = 'auto' in self.args.flags
        self.yaw = math.pi                        # 面向 -z（走廊深处）
        self.keys = set()
        self._firing_zoom = False
        self._light = False
        self._fov = BASE_FOV
        self._caption, self._caption_t = '', 0.0
        self._scare_t = 0.0
        self.renderer.apply_projection(self.window.width, self.window.height)
        # ---- 音频（best-effort）----
        self.sfx = {}
        self.hum = None
        try:
            paths = ensure_sfx()
            self.sfx = {n: pyglet.media.load(p, streaming=False)
                        for n, p in paths.items()}
            self.hum = self.sfx['hum'].player()
            self.hum.loop = True
            self.hum.volume = 0.5
            self.hum.play()
        except Exception as e:
            print(f'[audio] unavailable: {e}')
        # ---- 叠层字幕 ----
        self._title = pyglet.text.Label('', font_size=34,
                                        color=(200, 180, 90, 230),
                                        anchor_x='center', anchor_y='center',
                                        x=self.window.width // 2,
                                        y=self.window.height // 2 + 130)
        self._caption_lb = pyglet.text.Label('', font_size=16,
                                             color=(225, 222, 210, 235),
                                             anchor_x='center', anchor_y='center',
                                             x=self.window.width // 2, y=64,
                                             multiline=True,
                                             width=self.window.width - 160)
        self._scare_lb = pyglet.text.Label('！', font_size=170,
                                           color=(150, 18, 8, 255),
                                           anchor_x='center', anchor_y='center',
                                           x=self.window.width // 2,
                                           y=self.window.height // 2)
        self._hint = pyglet.text.Label(HELP, font_size=11,
                                       color=(150, 150, 145, 200),
                                       anchor_x='center', anchor_y='center',
                                       x=self.window.width // 2, y=24)

    def _seed(self):
        for f in self.args.flags:
            if f.startswith('seed') and f[4:].isdigit():
                return int(f[4:])
        return 20261014

    # ---------------------------------------------------- 输入 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        self.keys.add(symbol)
        if symbol == key.E:
            self._context_interact()
        elif symbol == key.F:
            self._light = not self._light
        elif symbol == key.R:
            self.k.restart((self.k.seed * 1103515245 + 12345) % (2 ** 31))

    def on_key_release(self, symbol, modifiers):
        self.keys.discard(symbol)

    def on_mouse_press(self, x, y, button, modifiers):
        mouse = pyglet.window.mouse
        if button == mouse.RIGHT:
            self._firing_zoom = True
        elif button == mouse.LEFT:
            self._context_interact()

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.k.turn(-dx * 0.0042)

    def status_text(self):
        k = self.k
        return (f'{k.variant_title}   loop {min(k.loop + 1, 8)}/8   '
                f'{"手电 ON " if self._light else ""}'
                f'{"[实体逼近] " if k.entity.get("active") and k.entity.get("frozen") is False else ""}'
                f'死亡 {k.deaths}   t={self.time:5.1f}s'
                f'{"  [AUTO]" if self.auto else ""}'
                f'{"  [PAUSED]" if self.paused else ""}')

    # ---------------------------------------------------- 交互 --
    def _context_interact(self):
        p = self.k.player
        best, best_d = None, INTERACT_RANGE
        for (what, hx, hz) in HOTSPOTS:
            d = math.hypot(p['x'] - hx, p['z'] - hz)
            if d < best_d:
                best, best_d = what, d
        if best is None:
            return
        r = self.k.interact(best)
        self._caption_for(r)

    def _caption_for(self, r):
        table = {
            'radio': RADIO_LINES[min(self.k.variant.get('radio', 0),
                                     len(RADIO_LINES) - 1)],
            'p1_solved': '…咔哒。有什么东西解锁了。手电和一把钥匙掉在地上。',
            'p2_solved': '灯亮了。东墙的颜色……比别的更深。',
            'unlock': '…咔哒。',
            'locked': '锁着。锁面上刻着四个空位。',
            'flash': '灯闪了：四下…一下…三下…两下。记住它。',
            'reset': '咔——顺序错了。全部弹回。',
            'won': '你被选中了。',
            'silent': '电话没有响。收音机说……时机未到。',
        }
        if r == 'radio':
            self._caption, self._caption_t = table['radio'], 7.0
        elif r and str(r).startswith('mark'):
            self._caption, self._caption_t = f'涂鸦上的数字……（{r}）', 4.0
        elif r in table:
            self._caption, self._caption_t = table[r], 5.0

    # ---------------------------------------------------- 每帧 --
    def update(self, dt):
        k = self.k
        if self.auto:
            k.auto_step(dt)
        else:
            key = pyglet.window.key
            dx = ((1.0 if key.D in self.keys else 0.0)
                  - (1.0 if key.A in self.keys else 0.0))
            dz = ((1.0 if key.S in self.keys else 0.0)
                  - (1.0 if key.W in self.keys else 0.0))
            if dx or dz:
                cy, sy = math.cos(self.yaw), math.sin(self.yaw)
                k.move(dx * cy + dz * sy, -dx * sy + dz * cy, dt,
                       sprint=bool(key.LSHIFT in self.keys))
        k.zoom(self._firing_zoom)
        fx, fz = -math.sin(self.yaw), -math.cos(self.yaw)
        k.update(dt, facing=(fx, fz), light=self._light)
        # zoom 平滑（P.T. 第二动作）
        target = ZOOM_FOV if self._firing_zoom else BASE_FOV
        self._fov += (target - self._fov) * min(1.0, dt * 10.0)
        self.renderer.apply_projection(self.window.width, self.window.height)
        self._apply_view()
        self.renderer.apply_variant(k.variant, light=self._light)
        # 实体逼近 → 灯嗡音量抬升（听觉压迫）
        if k.entity.get('active') and self.hum is not None:
            d = math.hypot(k.entity['x'] - k.player['x'],
                           k.entity['z'] - k.player['z'])
            self.hum.volume = min(1.0, 0.5 + max(0.0, 4.0 - d) * 0.12)
        # 事件 → 音效/字幕
        for ev in k.drain_events():
            if ev == 'jumpscare':
                self._scare_t = 0.55
                self._play('scare')
            elif ev in ('radio', 'ring'):
                self._play('broadcast')
        self._caption_t = max(0.0, self._caption_t - dt)
        self._scare_t = max(0.0, self._scare_t - dt)

    def _apply_view(self):
        p = self.k.player
        fx, fz = -math.sin(self.yaw), -math.cos(self.yaw)
        self.window.view = Mat4.look_at(
            Vec3(p['x'], 1.55, p['z']),
            Vec3(p['x'] + fx, 1.45, p['z'] + fz), Vec3(0, 1, 0))

    def _play(self, name):
        if name in self.sfx:
            try:
                self.sfx[name].seek(0.0)
                self.sfx[name].play()
            except Exception:
                pass

    # ---------------------------------------------------- 渲染 --
    def render(self):
        # 清屏色由 apply_variant 按当前圈雾色设置（window.clear 消费）
        self.renderer.draw()
        # ---- UI 层（正交）----
        glDisable(GL_DEPTH_TEST)
        w, h = self.window.width, self.window.height
        saved_proj, saved_view = self.window.projection, self.window.view
        self.window.view = Mat4()
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -1, 1)
        self._title.text = self.k.variant_title
        self._title.draw()
        if self._caption_t > 0.0:
            self._caption_lb.text = self._caption
            self._caption_lb.draw()
        if self._scare_t > 0.0:
            self._scare_lb.draw()
        self._hint.draw()
        glEnable(GL_DEPTH_TEST)
        self.window.projection = saved_proj
        self.window.view = saved_view

    def _on_resize(self, width, height):
        super()._on_resize(width, height)
        self._title.x = width // 2
        self._caption_lb.x = width // 2
        self._caption_lb.width = width - 160
        self._scare_lb.x = width // 2
        self._scare_lb.y = height // 2
        self._hint.x = width // 2


def main(argv=None):
    return run(LimApp, argv)


if __name__ == '__main__':
    sys.exit(main())

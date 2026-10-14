# -*- coding: utf-8 -*-
"""沙垒 / Sandward —— UV 投影规则演示场（dust 风格块状竞技场 · 5 检查点巡游）。

引擎级 UV 特性（§18）的可跑样板：程序化重建的块状竞技场，每一面墙/箱/地
的 UV 都由 ``texproj.UvProjection``（世界投影）生成——**"贴图规则"本体**；
材质来自 ``proctex`` 程序化图集（零外部素材）。三种显示模式演示不同视角：

* **材质**——真实观感（图集采样，格内平铺）；
* **棋盘格**——目检 UV 密度/扭曲/接缝（片元程序化棋盘，不采纹理）；
* **UV 规则**——按贴图密度着色阶（world units per UV，冷→暖）。

操作：WASD 走   拖拽 转视角   SHIFT 疾走   TAB 显示模式   R 重开
      SPACE 暂停   ESC 退出
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyglet
from pyglet.math import Mat4, Vec3

from world3d.engine.app import EngineApp, run
from world3d.logic.sandward import (EYE_H, SandwardKernel, SPRINT_SPEED,
                                    WALK_SPEED)
from world3d.presentation.sandward_scene import SandRenderer

TITLE = '沙垒 / Sandward — UV 投影规则演示场（dust 风格 · 5 检查点）'
HELP = ('WASD 走   拖拽 转视角   SHIFT 疾走   TAB 显示模式（材质/棋盘格/UV 规则）'
        '   R 重开   SPACE 暂停   ESC 退出')
SHOTS = ((13.0, 'docs/sandward_01.png'),        # 西北角场（沿环线）
         (32.0, 'docs/sandward_02.png'))        # 东环 / 东南角场


class SandwardApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS

    def setup(self):
        self.k = SandwardKernel(self._seed())
        self.renderer = SandRenderer(self.k, self.window)
        # `--shot` 是无人值守抓帧：也给 auto（否则停在出生点）
        self.auto = ('auto' in self.args.flags
                     or bool(getattr(self.args, 'shot', False)))
        self.yaw = 0.0                            # 面向 -z（中庭方向）
        self.keys = set()
        self.renderer.apply_projection(self.window.width, self.window.height)

    def _seed(self):
        for f in self.args.flags:
            if f.startswith('seed') and f[4:].isdigit():
                return int(f[4:])
        return 20261015

    # ---------------------------------------------------- 输入 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        self.keys.add(symbol)
        if symbol == key.TAB:
            self.renderer.set_mode(self.renderer.mode + 1)
        elif symbol == key.R:
            self.k.restart()

    def on_key_release(self, symbol, modifiers):
        self.keys.discard(symbol)

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.yaw -= dx * 0.0042

    def status_text(self):
        return (f'{self.k.status()}   模式 {self.renderer.mode_name} (TAB)'
                f'{"  [AUTO]" if self.auto else ""}'
                f'{"  [PAUSED]" if self.paused else ""}')

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
                       speed=SPRINT_SPEED if key.LSHIFT in self.keys
                       else WALK_SPEED)
        k.update(dt)
        self.renderer.apply_projection(self.window.width, self.window.height)
        self._apply_view()

    def _apply_view(self):
        p = self.k.player
        fx, fz = -math.sin(self.yaw), -math.cos(self.yaw)
        self.window.view = Mat4.look_at(
            Vec3(p['x'], EYE_H, p['z']),
            Vec3(p['x'] + fx, EYE_H - 0.08, p['z'] + fz), Vec3(0, 1, 0))

    # ---------------------------------------------------- 渲染 --
    def render(self):
        self.renderer.draw()


def main(argv=None):
    return run(SandwardApp, argv)


if __name__ == '__main__':
    sys.exit(main())

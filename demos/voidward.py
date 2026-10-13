# -*- coding: utf-8 -*-
"""星阑 / Voidward demo（§15）—— 惯性飞行 + 单星系贸易 + 海盗拦截。

2D 俯视星图：恒星 + 3 行星 + 1 空间站 + 你的船（惯性飞行）+ 海盗 + 弹丸。
停靠站点（自动判定半径）后可买卖 5 种货；资产达标 10000 -> won。

按键：``WASD`` 推进（惯性滑行）　``空格`` 开火　``B`` 买 1　``V`` 卖 1
　　　``N`` 升级（舱/炮轮换）　``A`` 自动贸易（策略 AI）　``R`` 重置　``ESC`` 退出

运行::

    python demos/voidward.py
    python demos/voidward.py --smoke
    python demos/voidward.py --shot

合规：致敬 Elite(1984) 的「惯性飞行 + 贸易 + 战斗」；**全部数值/命名/文案原创**。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
from pyglet.gl import (GL_BLEND, GL_COLOR_BUFFER_BIT,            # noqa: E402
                       GL_DEPTH_TEST, GL_ONE_MINUS_SRC_ALPHA,
                       GL_SRC_ALPHA, GL_TRIANGLES, glBlendFunc,
                       glClear, glClearColor, glDisable, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram        # noqa: E402
from pyglet.math import Mat4, Vec3                               # noqa: E402

from engine.app import EngineApp, run                            # noqa: E402
from host.tree import (WANT_VOID, config_path,  # noqa: E402
                                          mount_tree)
from games.void.kernel import (CORE_WORLD_R, DOCK_R,  # noqa: E402
                                            GOODS, GOOD_BASE, nearest_station)

TITLE = '3dworld 星阑 · Voidward（§15 太空贸易）'
HELP = 'WASD 推进  空格 开火  B 买  V 卖  N 升级  Y 切换货  G 自动  R 重置  ESC 退出'
SHOTS = ((1.5, 'docs/voidward_01.png'), (4.0, 'docs/voidward_02.png'))

VS = '''#version 330 core
in vec2 position;
uniform mat4 u_mvp;
void main() { gl_Position = u_mvp * vec4(position, 0.0, 1.0); }'''
FS = '''#version 330 core
out vec4 outColor;
uniform vec3 u_tint;
uniform float u_alpha;
void main() { outColor = vec4(u_tint, u_alpha); }'''

T_HUB = (0.55, 0.60, 0.70)
T_PLANET = (0.36, 0.52, 0.62)
T_STAR = (1.00, 0.86, 0.42)
T_SHIP = (0.45, 0.86, 1.00)
T_PIRATE = (0.98, 0.35, 0.32)
T_BULLET = (0.92, 0.98, 1.00)
T_DOCK = (0.30, 0.85, 0.45)


class VoidwardApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    VIEW_R = CORE_WORLD_R * 1.25          # 视野半径（世界单位）

    def setup(self):
        self.ctx, self.k = self._boot()
        self.prog = ShaderProgram(Shader(VS, 'vertex'), Shader(FS, 'fragment'))
        self.quad = self.prog.vertex_list(6, GL_TRIANGLES, position=('f', (
            -0.5, -0.5, 0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, 0.5)))
        self.keys = set()
        self.auto = 'auto' in self.args.flags
        self.buy_i = 0
        self.msg, self.msg_t = '', 0.0

    def _boot(self):
        return asyncio.run(self._mount())

    async def _mount(self):
        c = await mount_tree(config_path('cordis.void.yml'), want=WANT_VOID)
        return c, c.void

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.auto:
            self.k.auto_step(dt)
        else:
            ax = (1.0 if 'd' in self.keys else 0.0) - (1.0 if 'a' in self.keys else 0.0)
            az = (1.0 if 'w' in self.keys else 0.0) - (1.0 if 's' in self.keys else 0.0)
            if ax or az:
                self.k.thrust(ax, az, dt)
            self.k.update(dt)
        self.msg_t = max(0.0, self.msg_t - dt)

    def _rect(self, wx, wz, half, tint, alpha=1.0):
        """世界坐标 -> NDC 方块（工程宽高比拉伸，不影响判读）。"""
        nx, ny = wx / self.VIEW_R, wz / self.VIEW_R
        nw = half * 2.0 / self.VIEW_R
        m = (Mat4.from_translation(Vec3(nx, ny, 0.0))
             @ Mat4.from_scale(Vec3(nw, nw, 1.0)))
        self.prog['u_mvp'] = Mat4.orthogonal_projection(-1, 1, -1, 1, -1, 1) @ m
        self.prog['u_tint'] = tint
        self.prog['u_alpha'] = alpha
        self.quad.draw(GL_TRIANGLES)

    def render(self):
        glDisable(GL_DEPTH_TEST)
        glClearColor(0.025, 0.03, 0.05, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        self.prog.use()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        k = self.k

        self._rect(0.0, 0.0, 2.6, T_STAR, 1.0)                 # 恒星
        dock = k.docks_at()
        for s in k.stations:
            self._rect(s['pos'][0], s['pos'][1],
                       4.2 if s['kind'] == 'station' else 3.2,
                       T_DOCK if s is dock else
                       (T_HUB if s['kind'] == 'station' else T_PLANET),
                       0.95 if s is dock else 0.75)
            self._rect(s['pos'][0], s['pos'][1], DOCK_R,
                       T_DOCK, 0.12)                           # 停靠圈
        for p in k.pirates:
            if p['alive']:
                self._rect(p['x'], p['z'], 1.5, (1, 1, 1) if p['flash'] > 0.5 else T_PIRATE)
        for b in k.bullets:
            self._rect(b['x'], b['z'], 0.5, T_BULLET)
        sh = k.ship
        self._rect(sh['x'], sh['z'], 1.5, T_SHIP)              # 船
        # 朝向指示（船头前方一小块）
        import math as _m
        self._rect(sh['x'] + _m.cos(sh['heading']) * 3.0,
                   sh['z'] + _m.sin(sh['heading']) * 3.0, 0.7, T_SHIP, 0.85)
        glDisable(GL_BLEND)

    # ------------------------------------------------------------ 交互 --
    def on_key_press(self, symbol, modifiers):
        from pyglet.window import key
        m = {key.W: 'w', key.A: 'a', key.S: 's', key.D: 'd'}
        if symbol in m:
            self.keys.add(m[symbol])
            return pyglet.event.EVENT_HANDLED
        if symbol == key.SPACE:
            r = self.k.fire()
            self.msg, self.msg_t = r['detail'], 0.8
        elif symbol == key.B:
            g = GOODS[self.buy_i % len(GOODS)]
            r = self.k.buy(g, 4)
            self.msg, self.msg_t = r['detail'], 1.2
        elif symbol == key.V:
            g = max(GOODS, key=lambda x: self.k.cargo[x]) if any(self.k.cargo.values()) else GOODS[0]
            r = self.k.sell(g, self.k.cargo[g])
            self.msg, self.msg_t = r['detail'], 1.2
        elif symbol == key.N:
            kind = 'cargo' if self.k.upgrades['cargo'] <= self.k.upgrades['gun'] else 'gun'
            r = self.k.upgrade(kind)
            self.msg, self.msg_t = r['detail'], 1.4
        elif symbol == key.Y:
            self.buy_i += 1
            self.msg, self.msg_t = f'选中货 {GOODS[self.buy_i % len(GOODS)]}（基准 {GOOD_BASE[GOODS[self.buy_i % len(GOODS)]]:.0f}）', 1.4
        elif symbol == key.G:                  # A 已给左移，自动用 G
            self.auto = not self.auto
        elif symbol == key.R:
            self.k.ctx.rules.reset()
            self.k.ship.update(x=0.0, z=-12.0, vx=0.0, vz=0.0)
            self.k.cargo = {g: 0 for g in GOODS}
            self.auto, self.msg, self.msg_t = False, '已重置', 1.2
        elif symbol == key.ESCAPE:
            self.close()
        else:
            return pyglet.event.EVENT_UNHANDLED
        return None

    def on_key_release(self, symbol, modifiers):
        from pyglet.window import key
        m = {key.W: 'w', key.A: 'a', key.S: 's', key.D: 'd'}
        self.keys.discard(m.get(symbol, ''))
        return pyglet.event.EVENT_HANDLED

    def status_text(self):
        k = self.k
        st, d = nearest_station((k.ship['x'], k.ship['z']), k.stations)
        cur = GOODS[self.buy_i % len(GOODS)]
        return (f"{k.status_text()}  选中 {cur}@{k.price(cur):.0f}"
                + ('  [AUTO]' if self.auto else '')
                + (f'  {self.msg}' if self.msg_t > 0 else ''))


def main(argv=None):
    return run(VoidwardApp, argv)


if __name__ == '__main__':
    sys.exit(main())

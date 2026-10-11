# -*- coding: utf-8 -*-
"""静馆 / Stillhall demo（§14）—— 节点式漫游解谜。

**节点式**：固定机位 + 点击画面热点换景/取物/开机关（不是自由 WASD）。
当前景用**程序化示意**渲染（景名 + 热点矩形 + 悬停高亮），谜题进度与物品栏在 HUD。

按键：``鼠标左键`` 点击热点　``1`` 自动按最优序列推进（演示用）　``R`` 重置　``ESC`` 退出

运行::

    python demos/stillhall.py
    python demos/stillhall.py --smoke
    python demos/stillhall.py --shot

合规：致敬 Myst(1993) 的"节点式漫游 + 点击热点 + 谜题状态机"，**不碰原 IP 名称与素材**；
全部几何/文案/谜题为本项目原创。
"""
from __future__ import annotations

import asyncio
import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
from pyglet.gl import (GL_BLEND, GL_COLOR_BUFFER_BIT,            # noqa: E402
                       GL_DEPTH_TEST, GL_FALSE, GL_FLOAT,
                       GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA,
                       GL_TRIANGLES, glBlendFunc, glClear,
                       glClearColor, glDisable, glEnable,
                       glVertexAttribPointer)
from pyglet.graphics.shader import Shader, ShaderProgram        # noqa: E402
from pyglet.math import Mat4, Vec3                                    # noqa: E402

from engine.app import EngineApp, run                            # noqa: E402
from experiments.cordis.tree_boot import (WANT_HALL, config_path,  # noqa: E402
                                          mount_tree)

TITLE = '3dworld 静馆 · Stillhall（§14 节点式漫游解谜）'
HELP = '左键 点击热点   1 自动推进   R 重置   ESC 退出'
SHOTS = ((1.2, 'docs/stillhall_01.png'), (3.0, 'docs/stillhall_02.png'))

VS = '''#version 330 core
in vec2 position;
uniform mat4 u_mvp;
void main() { gl_Position = u_mvp * vec4(position, 0.0, 1.0); }'''
FS = '''#version 330 core
out vec4 outColor;
uniform vec3 u_tint;
uniform float u_alpha;
void main() { outColor = vec4(u_tint, u_alpha); }'''

# 每景的"氛围色"（程序化示意：不同景用不同底色，便于一眼区分换景）
SCENE_TINT = {'atrium': (0.16, 0.17, 0.22), 'hall': (0.20, 0.17, 0.15),
              'vault': (0.13, 0.15, 0.19)}
TINT_HOTSPOT = (0.42, 0.46, 0.55)
TINT_HOVER = (0.98, 0.82, 0.35)
TINT_LOCKED = (0.30, 0.22, 0.24)

# 自动推进序列（每个景内取"能推进谜题"的热点）
AUTO_SEQ = ('h_take_key', 'h_to_hall', 'h_lamp', 'h_to_vault', 'h_final')


class StillhallApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS

    def setup(self):
        self.prog = ShaderProgram(Shader(VS, 'vertex'), Shader(FS, 'fragment'))
        self.quad = self.prog.vertex_list(6, GL_TRIANGLES, position=('f', (
            -0.5, -0.5, 0.5, -0.5, 0.5, 0.5, -0.5, -0.5, 0.5, 0.5, -0.5, 0.5)))
        # 与 headless 验收**同一套挂载**（「demo 怎么挂的 = 验收怎么挂的」）
        self.ctx, self.hall = self._boot_kernel()
        self.hall._emit = lambda *a, **k: None        # demo 不需要事件流
        self.hover_id = None
        self.auto_i = 0
        self.auto_t = 0.0
        self.msg = ''
        self.msg_t = 0.0

    def _boot_kernel(self):
        return asyncio.run(self._mount_kernel())

    async def _mount_kernel(self):
        ctx = await mount_tree(config_path('cordis.hall.yml'), want=WANT_HALL)
        return ctx, ctx.hall

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        self.msg_t = max(0.0, self.msg_t - dt)
        if getattr(self, '_auto_on', False):
            self.auto_t -= dt
            if self.auto_t <= 0.0:
                self.auto_t = 0.45
                if self.auto_i < len(AUTO_SEQ):
                    r = self.hall.click(AUTO_SEQ[self.auto_i])
                    self.msg, self.msg_t = r['detail'], 1.2
                    self.auto_i += 1
                else:
                    self._auto_on = False

    def _draw_rect(self, x, y, w, h, tint, alpha=1.0):
        """画一个矩形。``x/y/w/h`` 是**归一化 0..1**（左下原点），内部转成 NDC。"""
        nx, ny = x * 2.0 - 1.0, y * 2.0 - 1.0        # 0..1 -> -1..1
        nw, nh = w * 2.0, h * 2.0
        m = (Mat4.from_translation(Vec3(nx + nw * 0.5, ny + nh * 0.5, 0.0))
             @ Mat4.from_scale(Vec3(nw, nh, 1.0)))
        self.prog['u_mvp'] = Mat4.orthogonal_projection(-1, 1, -1, 1, -1, 1) @ m
        self.prog['u_tint'] = tint
        self.prog['u_alpha'] = alpha
        self.quad.draw(GL_TRIANGLES)

    def render(self):
        glDisable(GL_DEPTH_TEST)
        glClearColor(0.06, 0.065, 0.08, 1.0)
        glClear(GL_COLOR_BUFFER_BIT)
        self.prog.use()
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        k = self.hall
        n = k.node()
        # 当前景底色（整屏）
        self._draw_rect(0.0, 0.0, 1.0, 1.0, SCENE_TINT.get(k.node_id, (0.15, 0.15, 0.18)))

        # 热点框 + 悬停/可用性区分（kernel 的 area 是**左上原点**，这里转成左下原点）
        for hs in k.hotspots():
            x, y, w, h = hs['area']
            tint, alpha = TINT_HOTSPOT, 0.85
            if not hs['usable']:
                tint = TINT_LOCKED
            if hs['id'] == self.hover_id:
                tint = TINT_HOVER
                alpha = 1.0
            self._draw_rect(x, 1.0 - y - h, w, h, tint, alpha)

        # 物品栏条（左下角小方块示意）
        for i, _item in enumerate(k.inventory()):
            self._draw_rect(0.02 + i * 0.045, 0.03, 0.032, 0.055,
                            (0.85, 0.72, 0.35), 1.0)
        glDisable(GL_BLEND)

    # ------------------------------------------------------------ 交互 --
    def on_mouse_motion(self, x, y, dx, dy):
        w, h = max(1, self.window.width), max(1, self.window.height)
        self.hover_id = self.hall.hit(x / w, y / h)

    def on_mouse_press(self, x, y, button, modifiers):
        w, h = max(1, self.window.width), max(1, self.window.height)
        hs = self.hall.hit(x / w, y / h)
        if hs:
            r = self.hall.click(hs)
            self.msg, self.msg_t = r['detail'], 1.6
        return pyglet.event.EVENT_HANDLED

    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key._1:
            self._auto_on = True
            self.auto_i, self.auto_t = 0, 0.0
        elif symbol == key.R:
            self.hall.node_id = self.hall.scene['start']
            self.hall.flags, self.hall.items = set(), []
            self.auto_i, self._auto_on = 0, False
            self.msg, self.msg_t = '已重置', 1.2
        elif symbol == key.ESCAPE:
            self.close()
        else:
            return pyglet.event.EVENT_UNHANDLED
        return None

    def status_text(self):
        k = self.hall
        n = k.node()
        st = k.current_step()
        return (f"{n['name']} · {n['look'][:18]} · 物品[{','.join(k.inventory()) or '空'}] · "
                f"谜 {st['id'] if st else '全解'} · {self.ctx.rules.state()}"
                + (f' · {self.msg}' if self.msg_t > 0 else ''))


class _MiniCtx:
    """（已弃用：demo 改走 mount_tree 与验收同源；保留类名以说明差异。）"""

    class _Rules:
        def __init__(self):
            self._st = 'playing'

        def state(self):
            return self._st

        def on_event(self, ev):
            if ev.get('type') == 'puzzle_solved':
                self._st = 'won'

        def reset(self):
            self._st = 'playing'

    def __init__(self):
        self.rules = _MiniCtx._Rules()


def main(argv=None):
    return run(StillhallApp, argv)


if __name__ == '__main__':
    sys.exit(main())

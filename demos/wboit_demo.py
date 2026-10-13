# -*- coding: utf-8 -*-
"""Weighted Blended OIT demo（P2-2）—— 顺序无关透明。

场景：几个**互相相交**的半透明球（排序法在这种场景**必然出错**：无论按什么顺序画，
总有一层被错误地遮挡），以及一个不透明地面。

按键：``S`` 切 **WBOIT ↔ 排序**　``O`` 反转透明绘制顺序　``SPACE`` 暂停
　　　拖动=旋转　滚轮=缩放　``ESC`` 退出

看点：**按 O 反转顺序时，WBOIT 画面不变，排序法画面会跳变** —— 这就是"顺序无关"的实证。

运行::

    python demos/wboit_demo.py
    python demos/wboit_demo.py --smoke
    python demos/wboit_demo.py --shot

合规：本 demo 的 WBOIT 为独立实现（McGuire 2013 的公开算法）；
参考 Easy3D 仅"问题意识"（透明靠排序不可靠），未借鉴其代码（GPL-3.0）。
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
from pyglet.gl import (GL_BLEND, GL_COLOR_BUFFER_BIT,            # noqa: E402
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST,
                       GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA, glClear,
                       glClearColor, glDisable, glEnable, glBlendFunc)
from pyglet.math import Mat4, Vec3                              # noqa: E402

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.app import EngineApp, run                            # noqa: E402
from engine.camera import OrbitCamera                            # noqa: E402
from engine.geometry import ground_grid, sphere                  # noqa: E402
from engine.oit import WBOIT, supported as wboit_supported       # noqa: E402
from engine.postfx import FullscreenQuad                         # noqa: E402
from engine.shaders import get                                   # noqa: E402
from engine.targets import RenderTarget, bind_texture            # noqa: E402

TITLE = '3dworld WBOIT · 顺序无关透明（P2-2）'
HELP = 'S WBOIT/排序  O 反转顺序  SPACE 暂停  drag orbit  wheel zoom  ESC quit'
SHOTS = ((1.2, 'docs/wboit_01.png'), (2.4, 'docs/wboit_02.png'))

# 4 个相交的半透明球（颜色 + 半径 + 位置），刻意让它们两两相交
ORBS = [
    ((-2.2, 1.3, 0.0), 1.6, (0.95, 0.25, 0.25, 0.55)),
    ((0.0, 1.3, -2.0), 1.6, (0.25, 0.85, 0.35, 0.55)),
    ((2.2, 1.3, 0.0), 1.6, (0.30, 0.55, 0.98, 0.55)),
    ((0.0, 2.4, 1.6), 1.6, (0.98, 0.80, 0.25, 0.55)),
]


class WBOITDemo(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    Z_FAR = 200.0

    # ------------------------------------------------------------ 构建 --
    def setup(self):
        w, h = self.window.width, self.window.height
        self.scene_rt = RenderTarget(w, h, color_count=1, depth=True)
        self.oit = WBOIT(w, h)
        self.blit_quad = FullscreenQuad()
        self.blit = get('wboit_composite')      # 复用合成程序做"贴回"（uScene + 空 accum）
        self.basic = get('basic')
        self.wboit_prog = get('wboit')
        self.has_wboit = wboit_supported()

        # 不透明地面（basic：position + colors）
        self.ground_md = ground_grid(half=22, tile=2.0)
        if self.ground_md.indices:
            self.ground_md = self.ground_md.to_non_indexed()   # 非索引绘制
        self.ground = self.basic.vertex_list(
            self.ground_md.vertex_count, GL_TRIANGLES_PY,
            position=('f', self.ground_md.positions),
            colors=('f', self._colors(self.ground_md, (0.34, 0.35, 0.38, 1.0))))

        # 透明球：同一份几何，两套 vertex_list（basic 用 colors / wboit 用 normals）
        self.orb_md = sphere(1.0, 26, 18, (1.0, 1.0, 1.0, 1.0))
        if self.orb_md.indices:                       # sphere 是**索引网格**，
            self.orb_md = self.orb_md.to_non_indexed()  # 非索引 vertex_list 必须先去索引
        self.orb_basic = self.basic.vertex_list(
            self.orb_md.vertex_count, GL_TRIANGLES_PY,
            position=('f', self.orb_md.positions),
            colors=('f', self._colors(self.orb_md, (1.0, 1.0, 1.0, 1.0))))
        # WBOIT 用**独立**的 MeshData（排除与 orb_basic 共享数据的一切干扰）
        _wmd = sphere(1.0, 26, 18, (1.0, 1.0, 1.0, 1.0))
        if _wmd.indices:
            _wmd = _wmd.to_non_indexed()
        self.orb_wboit = self.wboit_prog.vertex_list(
            _wmd.vertex_count, GL_TRIANGLES_PY,
            position=('f', _wmd.positions), normals=('f', _wmd.normals))

        self.cam = OrbitCamera(target=(0.0, 1.6, 0.0), yaw=30.0, pitch=18.0,
                               dist=13.0, dist_range=(4.0, 60.0),
                               fov=self.FOV, far=self.Z_FAR)
        self.use_wboit = self.has_wboit
        if 'sort' in self.args.flags:      # 强制排序路径（对照）
            self.use_wboit = False
        self.reverse = False
        self.paused = False
        self.time0 = 0.0

    @staticmethod
    def _colors(md, rgba):
        return list(rgba) * md.vertex_count

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        self.time0 += dt
        self.cam.advance(dt * 0.10)

    def _order(self):
        """透明球的绘制顺序（按相机距离；``O`` 键反转）。"""
        eye = self.cam.eye
        idx = list(range(len(ORBS)))
        idx.sort(key=lambda i: -sum((ORBS[i][0][k] - (eye.x, eye.y, eye.z)[k]) ** 2
                                    for k in range(3)))
        if self.reverse:
            idx.reverse()
        return idx

    def _draw_opaque(self):
        self.basic.use()
        # basic 的 model/tint/glow 必须显式给（不给 -> uniform 默认 0 -> 顶点全塌到原点）
        self.basic['model'] = Mat4()
        self.basic['tint'] = Vec3(1.0, 1.0, 1.0)
        self.basic['glow'] = 0.0
        self.ground.draw(GL_TRIANGLES_PY)

    def render(self):
        w, h = self.window.width, self.window.height
        self.window.view = Mat4(*self.cam.view_matrix())
        glEnable(GL_DEPTH_TEST)

        # ---- 1) 不透明场景 -> scene_rt ----
        self.scene_rt.bind()
        glClearColor(0.06, 0.07, 0.10, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self._draw_opaque()
        self.scene_rt.unbind()

        glClearColor(0.06, 0.07, 0.10, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)

        if self.use_wboit:
            # ---- 2a) WBOIT：透明物写 MRT（无排序） ----
            self.oit.begin()
            self.wboit_prog.use()
            for i in self._order():
                pos, rad, rgba = ORBS[i]
                self.wboit_prog['model'] = self._model(pos, rad)
                self.wboit_prog['normal_mat'] = self._normal_mat(pos, rad)
                self.wboit_prog['uColor'] = Vec3(*rgba[:3])
                self.wboit_prog['uAlpha'] = rgba[3]
                self.wboit_prog['uLightDir'] = Vec3(0.4, 0.8, 0.45)
                self.orb_wboit.draw(GL_TRIANGLES_PY)
            self.oit.end()
            # ---- 3a) 合成（背景 scene_rt + 透明累积） ----
            self.oit.composite(self.scene_rt.color[0], w, h)
        else:
            # ---- 2b) 排序路径：先把不透明场景贴回，再按顺序 alpha 混合 ----
            self.blit.use()
            bind_texture(0, self.scene_rt.color[0])
            self.blit['uScene'] = 0
            # accum 槽位：用 0 权重（黑 + 0 alpha），使 composite 退化为"直接输出背景"
            from engine.targets import make_solid_texture
            if not hasattr(self, '_black_tex'):
                self._black_tex = make_solid_texture(0, 0, 0, 0)
                self._white_tex = make_solid_texture(255, 255, 255, 255)
            bind_texture(1, self._black_tex)
            self.blit['uAccum'] = 1
            bind_texture(2, self._white_tex)
            self.blit['uReveal'] = 2
            glDisable(GL_DEPTH_TEST)
            self.blit_quad.draw(self.blit)
            glEnable(GL_DEPTH_TEST)

            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            self.basic.use()
            for i in self._order():
                pos, rad, rgba = ORBS[i]
                self.basic['model'] = self._model(pos, rad)
                self.basic['tint'] = Vec3(*rgba[:3])
                self.basic['glow'] = 0.0
                self.orb_basic.draw(GL_TRIANGLES_PY)
            glDisable(GL_BLEND)

    @staticmethod
    def _model(pos, rad):
        return (Mat4.from_translation(Vec3(*pos))
                @ Mat4.from_scale(Vec3(rad, rad, rad)))

    @staticmethod
    def _normal_mat(pos, rad):
        s = 1.0 / rad
        return Mat4(s, 0.0, 0.0, 0.0,
                    0.0, s, 0.0, 0.0,
                    0.0, 0.0, s, 0.0,
                    0.0, 0.0, 0.0, 1.0)

    # ------------------------------------------------------------ 交互 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.S:
            if self.has_wboit:
                self.use_wboit = not self.use_wboit
            else:
                print('[wboit] glBlendFunci 不可用 -> 保持排序路径（回退）')
        elif symbol == key.O:
            self.reverse = not self.reverse
        elif symbol == key.SPACE:
            self.paused = not self.paused
        elif symbol == key.ESCAPE:
            self.close()
        else:
            return pyglet.event.EVENT_UNHANDLED
        return None

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.cam.orbit(dx, dy)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        self.cam.zoom(scroll_y)

    def status_text(self):
        mode = ('WBOIT' if self.use_wboit else '排序(sort)') if self.has_wboit else '排序(sort，无 WBOIT)'
        return (f'{mode} · 顺序 {"反转" if self.reverse else "正序"} · '
                f'{"暂停" if self.paused else "运行"} · '
                f'按 O 反转顺序可看出 WBOIT 画面不变')


GL_TRIANGLES_PY = 0x0004


def main(argv=None):
    return run(WBOITDemo, argv)


if __name__ == '__main__':
    sys.exit(main())

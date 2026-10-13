# -*- coding: utf-8 -*-
"""点云渲染 + EDL demo（P2-4）——「点云地形 / 双螺旋」。

**零外部素材**：点云由 `engine/pointcloud.py` 程序化生成（值噪声地形 / 双螺旋）。

渲染链：

    pass 1  点云 → RT（颜色 + **深度纹理**）；点用 gl_PointSize + 片元圆盘裁剪（imposter 圆盘）
    pass 2  EDL 全屏 pass：按深度邻域差给轮廓加深；E 键开关
    pass 3  输出到窗口（EDL 开=pass2 结果，关=直接贴 pass1 颜色）

按键：``E`` EDL 开关　``1`` 地形点云　``2`` 双螺旋　``SPACE`` 暂停　``[``/``]`` 点大小
　　　拖动=旋转　滚轮=缩放　``ESC`` 退出

运行::

    python examples/pointcloud_edl.py
    python examples/pointcloud_edl.py --smoke
    python examples/pointcloud_edl.py --shot
"""
from __future__ import annotations

import ctypes
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
from pyglet.gl import (GL_BLEND, GL_COLOR_BUFFER_BIT,            # noqa: E402
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST,
                       GL_ONE_MINUS_SRC_ALPHA, GL_PROGRAM_POINT_SIZE,
                       GL_SRC_ALPHA, glBlendFunc, glClear, glClearColor,
                       glDisable, glEnable)
from pyglet.gl import GL_POINTS                                # noqa: E402
from pyglet.graphics.shader import Shader, ShaderProgram        # noqa: E402
from pyglet.math import Mat4, Vec3                              # noqa: E402

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.app import EngineApp, run                            # noqa: E402
from engine.camera import OrbitCamera                            # noqa: E402
from engine.pointcloud import (edl_glsl, generate_helix_cloud,   # noqa: E402
                               generate_terrain_cloud)
from engine.postfx import FullscreenQuad                         # noqa: E402
from engine.shaders import FULLSCREEN_VERT                       # noqa: E402
from engine.targets import RenderTarget, bind_texture            # noqa: E402

TITLE = '3dworld 点云 + EDL（P2-4）'
HELP = 'E EDL开关  1 地形  2 双螺旋  SPACE 暂停  [ ] 点大小  drag orbit  wheel zoom'
SHOTS = ((1.2, 'docs/pointcloud_01.png'), (2.4, 'docs/pointcloud_02.png'))

POINT_VS = '''#version 330 core
in vec3 position;
in vec3 colors;
in float sizes;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
uniform float uPointScale;
out vec3 vColor;
void main() {
    vec4 wp = model * vec4(position, 1.0);
    gl_Position = window.projection * window.view * wp;
    // 透视缩放：近大远小；clamp 到驱动上限内（本机 ≥ 63）
    gl_PointSize = clamp(sizes * uPointScale / max(0.05, gl_Position.w), 1.0, 48.0);
    vColor = colors;
}'''

POINT_FS = '''#version 330 core
in vec3 vColor;
layout(location = 0) out vec4 outColor;
void main() {
    // imposter 圆盘：把方形点裁成圆，边缘做软衰减（比 quad imposter 省一半顶点）
    vec2 d = gl_PointCoord * 2.0 - 1.0;
    float r2 = dot(d, d);
    if (r2 > 1.0) { discard; }
    float a = 1.0 - smoothstep(0.55, 1.0, sqrt(r2));
    outColor = vec4(vColor * (0.75 + 0.55 * a), a);
}'''


class PointCloudEDLApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    Z_FAR = 300.0

    # ------------------------------------------------------------ 构建 --
    def setup(self):
        w, h = self.window.width, self.window.height
        self.rt = RenderTarget(w, h, color_count=1, depth=True,
                               depth_attachment='texture')     # 深度要能采样（EDL 用）
        self.quad = FullscreenQuad()
        self.point_prog = ShaderProgram(Shader(POINT_VS, 'vertex'),
                                        Shader(POINT_FS, 'fragment'))
        self.edl_prog = ShaderProgram(Shader(FULLSCREEN_VERT, 'vertex'),
                                      Shader(edl_glsl(), 'fragment'))
        self.blit_prog = ShaderProgram(Shader(FULLSCREEN_VERT, 'vertex'),
                                       Shader('''#version 330 core
in vec2 vNdc;
layout(location = 0) out vec4 outColor;
uniform sampler2D uTex;
void main() { outColor = texture(uTex, vNdc * 0.5 + 0.5); }''', 'fragment'))

        self.clouds = {'terrain': generate_terrain_cloud(),
                       'helix': generate_helix_cloud()}
        self.cloud = 'terrain'
        self.vlists = {}
        for key, (pos, col, siz) in self.clouds.items():
            n = len(pos) // 3
            self.vlists[key] = self.point_prog.vertex_list(
                n, GL_POINTS,
                position=('f', pos), colors=('f', col), sizes=('f', siz))

        self.cam = OrbitCamera(target=(0.0, 5.0, 0.0), yaw=34.0, pitch=16.0,
                               dist=26.0, dist_range=(4.0, 120.0),
                               fov=self.FOV, far=self.Z_FAR)
        self.edl_on = True
        self.point_scale = 64.0
        self.paused = False

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        self.cam.advance(dt * 0.12)

    def render(self):
        w, h = self.window.width, self.window.height
        self.window.view = Mat4(*self.cam.view_matrix())
        self.rt.resize(w, h)

        # ---- pass 1：点云 → RT（颜色 + 深度纹理）----
        self.rt.bind()
        glClearColor(0.03, 0.035, 0.05, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_PROGRAM_POINT_SIZE)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        self.point_prog.use()
        self.point_prog['model'] = Mat4()
        self.point_prog['uPointScale'] = self.point_scale
        self.vlists[self.cloud].draw(GL_POINTS)
        glDisable(GL_BLEND)
        self.rt.unbind()

        # ---- pass 2/3：EDL（或直接贴回）→ 窗口 ----
        glDisable(GL_DEPTH_TEST)
        if self.edl_on:
            p = self.edl_prog
            p.use()
            bind_texture(0, self.rt.depth)
            p['uDepth'] = 0
            bind_texture(1, self.rt.color[0])
            p['uColor'] = 1
            p['uTexel'] = (1.0 / max(1, w), 1.0 / max(1, h))
            p['uStrength'] = 22.0
            p['uRadius'] = 1.4
            self.quad.draw(p)
        else:
            p = self.blit_prog
            p.use()
            bind_texture(0, self.rt.color[0])
            p['uTex'] = 0
            self.quad.draw(p)
        glEnable(GL_DEPTH_TEST)

    # ------------------------------------------------------------ 交互 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.E:
            self.edl_on = not self.edl_on
        elif symbol == key._1:
            self.cloud = 'terrain'
        elif symbol == key._2:
            self.cloud = 'helix'
        elif symbol == key.SPACE:
            self.paused = not self.paused
        elif symbol == key.BRACKETLEFT:
            self.point_scale = max(8.0, self.point_scale * 0.8)
        elif symbol == key.BRACKETRIGHT:
            self.point_scale = min(400.0, self.point_scale * 1.25)
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
        n = len(self.clouds[self.cloud][0]) // 3
        return (f'{self.cloud} · {n} 点 · EDL {"开" if self.edl_on else "关"} · '
                f'点大小 {self.point_scale:.0f} · {self.fps:5.1f} fps')


def main(argv=None):
    return run(PointCloudEDLApp, argv)


if __name__ == '__main__':
    sys.exit(main())

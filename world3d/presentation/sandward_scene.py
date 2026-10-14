# -*- coding: utf-8 -*-
"""沙垒 / Sandward 场景渲染器（UV 图集采样 + 三显示模式）。

与 KK/Lim 家族同形（自带 shader + 单个 VertexList 一次 draw call），差异在
**消费 texproj 的贴图规则**：

* 顶点 UV = ``logic.sandward.build_geometry()`` 的**世界投影 UV**（贴图数，
  可远超 1）——这正是被演示的对象；纹理来自 ``proctex.make_atlas()`` 的
  程序化 5 格图集（无任何外部素材）。
* 图集格内平铺在**片元**里做：``fract(uv)`` + 格矩形 mix + 半像素内缩。
  （顶点侧做 fract 会在插值时跨缝撕裂——经典坑。）
* 三种显示模式（Tab 切换，控制器在 examples/sandward.py）：

  ====== ==================================================
  0 材质 图集采样（真实观感）
  1 棋盘 片元程序化棋盘格（目检 UV 密度/扭曲/接缝）
  2 规则 按贴图密度着色阶（world units per UV，冷→暖）
  ====== ==================================================
"""
from __future__ import annotations

from pyglet.gl import (GL_DEPTH_TEST, GL_TRIANGLES, GL_TEXTURE0,
                       glActiveTexture, glClearColor, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4

from world3d.engine import proctex
from world3d.logic.sandward import build_geometry

MODE_NAMES = ('材质', '棋盘格', 'UV 规则')

SAND_VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;
in vec2 uv;
in vec4 cell;
in float extra;
out vec4 v_color;
out vec2 v_uv;
out vec4 v_cell;
out float v_extra;
out float v_dist;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
void main() {
    vec4 view_pos = window.view * model * vec4(position, 1.0);
    v_dist = length(view_pos.xyz);
    gl_Position = window.projection * view_pos;
    v_color = colors;
    v_uv = uv;
    v_cell = cell;
    v_extra = extra;
}'''

SAND_FRAGMENT_SRC = '''#version 330 core
in vec4 v_color;
in vec2 v_uv;
in vec4 v_cell;
in float v_extra;
in float v_dist;
out vec4 final_colors;
uniform sampler2D u_atlas;
uniform int u_mode;
uniform vec2 u_cell_pad;      // 半像素内缩（1/图集宽高）
uniform vec3 fog_color;
uniform float fog_near;
uniform float fog_far;
void main() {
    vec3 rgb;
    if (u_mode == 0) {
        // 材质：图集格内平铺（uv 取小数 → 格内坐标）
        vec2 t = fract(v_uv);
        vec2 auv = mix(v_cell.xy + u_cell_pad, v_cell.zw - u_cell_pad, t);
        rgb = texture(u_atlas, auv).rgb * v_color.rgb;
    } else if (u_mode == 1) {
        // 棋盘格：每半张贴图一个格（密度/扭曲/接缝目检）
        vec2 g = floor(v_uv * 2.0);
        float c = mod(g.x + g.y, 2.0);
        rgb = mix(vec3(0.86, 0.84, 0.80), vec3(0.16, 0.16, 0.18), c)
              * v_color.rgb;
    } else {
        // UV 规则：贴图密度色阶（log2 映射，约 ±2 倍跨度）
        float d = clamp(log2(v_extra / 128.0) * 0.5 + 0.5, 0.0, 1.0);
        rgb = mix(vec3(0.15, 0.40, 0.95), vec3(0.95, 0.72, 0.12), d);
        rgb *= 0.6 + 0.4 * v_color.rgb;
    }
    float f = smoothstep(fog_near, fog_far, v_dist);
    final_colors = vec4(mix(rgb, fog_color, f), 1.0);
}'''

FOG_COLOR = (0.71, 0.65, 0.52)     # dust 天光浮尘
FOG_NEAR, FOG_FAR = 55.0, 240.0


class SandRenderer:
    """沙垒渲染器：一次建网（brush → 面投影 UV → 图集格），三种显示模式。"""

    def __init__(self, kernel, window, cell=128, atlas=None):
        self.kernel = kernel
        self.window = window
        self.program = ShaderProgram(
            Shader(SAND_VERTEX_SRC, 'vertex'),
            Shader(SAND_FRAGMENT_SRC, 'fragment'))
        glEnable(GL_DEPTH_TEST)
        glClearColor(FOG_COLOR[0], FOG_COLOR[1], FOG_COLOR[2], 1.0)

        self.atlas = atlas if atlas is not None else proctex.make_atlas(
            names=('sandstone', 'plaster', 'crate', 'metal', 'sand'), cell=cell)
        self._tex = None
        pad = 0.5 / self.atlas['width'], 0.5 / self.atlas['height']

        geo = build_geometry(kernel.brushes)
        self.vl = self.program.vertex_list(
            geo['vertex_count'], GL_TRIANGLES,
            position=('f', geo['positions']), colors=('f', geo['colors']),
            uv=('f', geo['uvs']), cell=('f', geo['cells']),
            extra=('f', geo['extras']))
        self.mode = 0
        self.program.use()
        self.program['u_cell_pad'] = pad
        self.program['u_atlas'] = 0

    def set_mode(self, mode):
        self.mode = mode % len(MODE_NAMES)

    @property
    def mode_name(self):
        return MODE_NAMES[self.mode]

    def _ensure_tex(self):
        if self._tex is None:
            self._tex = proctex.atlas_texture(self.atlas)

    def apply_projection(self, width=None, height=None):
        w = width or self.window.width
        h = height or self.window.height
        self.window.projection = Mat4.perspective_projection(
            w / float(max(1, h)), z_near=0.05, z_far=300.0, fov=75.0)

    def draw(self):
        self._ensure_tex()
        self.program.use()
        self.program['model'] = Mat4()
        self.program['u_mode'] = self.mode
        self.program['fog_color'] = FOG_COLOR
        self.program['fog_near'] = FOG_NEAR
        self.program['fog_far'] = FOG_FAR
        glActiveTexture(GL_TEXTURE0)
        self._tex.bind()
        self.vl.draw(GL_TRIANGLES)

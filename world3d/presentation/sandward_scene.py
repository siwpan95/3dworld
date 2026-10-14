# -*- coding: utf-8 -*-
"""沙垒 / Sandward 场景渲染器（PBR 三通道图集 + 三显示模式）。

与 KK/Lim 家族同形（自带 shader + 单个 VertexList 一次 draw call），差异在
**消费 texproj 的贴图规则 + proctex 的三通道图集**：

* 顶点 UV = ``logic.sandward.build_geometry()`` 的**世界投影 UV**（贴图数，
  可远超 1）——这正是被演示的对象；纹理来自 ``proctex.make_atlas()`` 的
  程序化 5 格**三通道图集**（albedo / normal+A=高度 / mr=glTF ORM），零外部素材；
* **PBR 光照（§19）**：GGX 直接光（1 方向光）+ 半球环境（天空/地面双色，
  无 IBL 探针时的诚实近似）+ **法线贴图**；TBN 的切线/手性由投影同源导出
  （``logic.sandward`` 的 ``tangents``/``hands``）；
* 图集格内平铺在**片元**里做：``fract(uv)`` + 格矩形 mix + 半像素内缩。
  （顶点侧做 fract 会在插值时跨缝撕裂——经典坑。）
* 三种显示模式（Tab 切换，控制器在 examples/sandward.py）：

  ====== ==================================================
  0 PBR  三通道 PBR（真实观感）
  1 棋盘 片元程序化棋盘格（目检 UV 密度/扭曲/接缝）
  2 规则 按贴图密度着色阶（world units per UV，冷→暖）
  ====== ==================================================
"""
from __future__ import annotations

import pyglet
from pyglet.gl import (GL_DEPTH_TEST, GL_TRIANGLES, glActiveTexture,
                       glClearColor, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4

from world3d.engine import proctex
from world3d.logic.sandward import build_geometry

MODE_NAMES = ('PBR', '棋盘格', 'UV 规则', '通道')

#: 通道视图子项（模式 3，C 键循环）
CHANNEL_NAMES = ('albedo', 'normal', 'rough', 'metal', 'height')

#: 沙漠太阳：方向（地表→太阳，已归一）、暖白光与半球环境双色
SUN_DIR = (0.42, 0.78, 0.28)
SUN_COLOR = (1.35, 1.18, 0.95)
SKY_COLOR = (0.46, 0.52, 0.62)
GROUND_COLOR = (0.42, 0.36, 0.26)

SAND_VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec3 normal;
in vec3 tangent;
in float hand;
in vec4 colors;
in vec2 uv;
in vec4 cell;
in float extra;
out vec3 v_wpos;
out vec3 v_n;
out vec3 v_t;
out float v_hand;
out vec4 v_color;
out vec2 v_uv;
out vec4 v_cell;
out float v_extra;
out float v_dist;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
void main() {
    vec4 world = model * vec4(position, 1.0);
    vec4 view_pos = window.view * world;
    v_wpos = world.xyz;
    v_dist = length(view_pos.xyz);
    gl_Position = window.projection * view_pos;
    mat3 m3 = mat3(model);
    v_n = normalize(m3 * normal);
    v_t = normalize(m3 * tangent);
    v_hand = hand;
    v_color = colors;
    v_uv = uv;
    v_cell = cell;
    v_extra = extra;
}'''

SAND_FRAGMENT_SRC = '''#version 330 core
const float PI = 3.141592653589793;

in vec3 v_wpos;
in vec3 v_n;
in vec3 v_t;
in float v_hand;
in vec4 v_color;
in vec2 v_uv;
in vec4 v_cell;
in float v_extra;
in float v_dist;
out vec4 final_colors;

uniform sampler2D u_albedo;
uniform sampler2D u_normal;
uniform sampler2D u_mr;
uniform int u_mode;
uniform int u_chan;           // 通道视图子项（模式 3）
uniform float u_pom;          // POM 深度（0 = 关；贴图宽度比例，推荐 0.02~0.04）
uniform vec2 u_cell_pad;
uniform vec3 u_cam_pos;
uniform vec3 u_sun_dir;
uniform vec3 u_sun_color;
uniform vec3 u_sky_color;
uniform vec3 u_ground_color;
uniform vec3 fog_color;
uniform float fog_near;
uniform float fog_far;

// ---- GGX/COOK-TORRANCE 三件（直接光）----
float d_ggx(float NoH, float a) {
    float a2 = a * a;
    float d = NoH * NoH * (a2 - 1.0) + 1.0;
    return a2 / (PI * d * d);
}
float g_smith(float NoV, float NoL, float a) {
    float k = a * 0.5;                 // 直接光的 Schlick-GGX k
    float gv = NoV / (NoV * (1.0 - k) + k);
    float gl = NoL / (NoL * (1.0 - k) + k);
    return gv * gl;
}
vec3 f_schlick(float u, vec3 f0) {
    return f0 + (vec3(1.0) - f0) * pow(1.0 - u, 5.0);
}

// ---- 图集采样坐标（格内平铺 t → 格矩形 mix + 半像素内缩）----
vec2 atlas_uv(vec2 uv) {
    vec2 t = fract(uv);
    return mix(v_cell.xy + u_cell_pad, v_cell.zw - u_cell_pad, t);
}

// 高度场采样（normal.A，§19 声明性挪用）
float height_at(vec2 uv) {
    return texture(u_normal, atlas_uv(uv)).a;
}

// ---- POM：陡峭视差 12 步 + 步内线性插值（贴图数空间直接位移）----
vec2 pom_uv(vec2 uv, vec3 vts) {
    const int STEPS = 12;
    float layer_step = 1.0 / float(STEPS);
    // 掠射角 clamp：|z| 太小会让步长爆掉（穿帮来源）
    vec2 delta = (vts.xy / max(vts.z, 0.22)) * (u_pom / float(STEPS));
    float layer = 0.0;
    vec2 cur = uv;
    float h = height_at(cur);
    for (int i = 0; i < STEPS; ++i) {
        if (layer >= h) break;
        cur -= delta;
        h = height_at(cur);
        layer += layer_step;
    }
    // 线性细化：在 (cur+delta, cur) 区间按高度差插值
    float h_prev = height_at(cur + delta);
    float layer_prev = layer - layer_step;
    float denom = (h_prev - layer_prev) - (h - layer);
    float w = abs(denom) < 1e-4 ? 0.5 : clamp((h_prev - layer_prev) / denom, 0.0, 1.0);
    return mix(cur + delta, cur, w);
}

void main() {
    vec3 rgb;
    if (u_mode == 0) {
        // ---- PBR：三通道采样 + GGX 直接光 + 半球环境（+ 可选 POM 视差）----
        vec3 N = normalize(v_n);
        vec3 T = normalize(v_t);
        vec3 B = normalize(cross(N, T) * v_hand);
        vec3 V = normalize(u_cam_pos - v_wpos);
        vec2 uv_tex = v_uv;
        if (u_pom > 0.0) {
            vec3 V_ts = vec3(dot(V, T), dot(V, B), dot(V, N));
            uv_tex = pom_uv(v_uv, V_ts);
        }
        vec2 auv = atlas_uv(uv_tex);

        vec3 albedo = texture(u_albedo, auv).rgb;
        vec3 nrm_ts = texture(u_normal, auv).xyz * 2.0 - 1.0;
        vec4 mr = texture(u_mr, auv);
        float rough = clamp(mr.g, 0.045, 1.0);
        float metal = clamp(mr.b, 0.0, 1.0);
        float ao = mr.r;

        // TBN：切线/手性由投影同源导出（+z 面 +1、-z 面 -1）
        vec3 n = normalize(mat3(T, B, N) * nrm_ts);

        // 顶点色（面明暗）降级为 AO 近似：轻调 albedo/环境，不承担光照
        float vao = dot(v_color.rgb, vec3(0.3333));
        albedo *= mix(0.90, 1.10, vao);

        vec3 L = normalize(u_sun_dir);
        vec3 H = normalize(V + L);
        float NoL = max(dot(n, L), 0.0);
        float NoV = max(dot(n, V), 1e-4);
        float NoH = max(dot(n, H), 0.0);
        float VoH = max(dot(V, H), 0.0);

        vec3 f0 = mix(vec3(0.04), albedo, metal);
        float a = rough * rough;
        vec3 F = f_schlick(VoH, f0);
        vec3 spec = (d_ggx(NoH, a) * g_smith(NoV, NoL, a) * F)
                    / max(4.0 * NoV * NoL, 1e-4);
        vec3 kd = (1.0 - F) * (1.0 - metal);
        // 漫反射不除 PI（演示口径的艺术家归一：太阳色 ≈ 3.0 的等效亮度）
        vec3 direct = (kd * albedo + spec) * u_sun_color * NoL;

        // 半球环境（天空/地面双色）+ 粗糙度调制的环境镜面近似
        vec3 hemi = mix(u_ground_color, u_sky_color, 0.5 + 0.5 * N.y);
        vec3 env_spec = mix(vec3(0.30), f0, 0.5) * mix(1.0, 0.22, rough);
        vec3 ambient = (albedo * (1.0 - metal) * 0.9 + env_spec) * hemi;
        ambient *= mix(0.80, 1.0, vao) * ao;

        rgb = direct + ambient;
    } else if (u_mode == 1) {
        // 棋盘格：每半张贴图一个格（密度/扭曲/接缝目检）
        vec2 g = floor(v_uv * 2.0);
        float c = mod(g.x + g.y, 2.0);
        rgb = mix(vec3(0.86, 0.84, 0.80), vec3(0.16, 0.16, 0.18), c)
              * v_color.rgb;
    } else if (u_mode == 2) {
        // UV 规则：贴图密度色阶（log2 映射，以 4 米/贴图为中位、约 ±2 倍跨度）
        float d = clamp(log2(v_extra / 4.0) * 0.5 + 0.5, 0.0, 1.0);
        rgb = mix(vec3(0.15, 0.40, 0.95), vec3(0.95, 0.72, 0.12), d);
        rgb *= 0.6 + 0.4 * v_color.rgb;
    } else {
        // 通道视图（C 键循环）：albedo / normal / rough / metal / height
        vec2 auv = atlas_uv(v_uv);
        if (u_chan == 0)      rgb = texture(u_albedo, auv).rgb;
        else if (u_chan == 1) rgb = texture(u_normal, auv).rgb;
        else if (u_chan == 2) rgb = vec3(texture(u_mr, auv).g);
        else if (u_chan == 3) rgb = vec3(texture(u_mr, auv).b);
        else                  rgb = vec3(texture(u_normal, auv).a);
    }

    float f = smoothstep(fog_near, fog_far, v_dist);
    final_colors = vec4(mix(rgb, fog_color, f), 1.0);
}'''

FOG_COLOR = (0.71, 0.65, 0.52)     # dust 天光浮尘
FOG_NEAR, FOG_FAR = 55.0, 240.0


class SandRenderer:
    """沙垒渲染器：一次建网（brush → 面投影 UV → 三通道图集），PBR 三模式。"""

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
        self.cam = (0.0, 1.6, 0.0)
        pad = 0.5 / self.atlas['width'], 0.5 / self.atlas['height']

        geo = build_geometry(kernel.brushes)
        self.vl = self.program.vertex_list(
            geo['vertex_count'], GL_TRIANGLES,
            position=('f', geo['positions']), colors=('f', geo['colors']),
            uv=('f', geo['uvs']), cell=('f', geo['cells']),
            extra=('f', geo['extras']), normal=('f', geo['normals']),
            tangent=('f', geo['tangents']), hand=('f', geo['hands']))
        self.mode = 0
        self.pom = 0.03                  # POM 深度（0=关；[ ] 键调整）
        self.chan = 0
        self.program.use()
        self.program['u_cell_pad'] = pad
        self.program['u_albedo'] = 0
        self.program['u_normal'] = 1
        self.program['u_mr'] = 2

    def set_mode(self, mode):
        self.mode = mode % len(MODE_NAMES)

    @property
    def mode_name(self):
        return MODE_NAMES[self.mode]

    def set_pom(self, scale):
        self.pom = max(0.0, min(0.08, float(scale)))

    def set_chan(self, chan):
        self.chan = chan % len(CHANNEL_NAMES)

    @property
    def chan_name(self):
        return CHANNEL_NAMES[self.chan]

    def set_camera(self, x, y, z):
        self.cam = (float(x), float(y), float(z))

    def _ensure_tex(self):
        if self._tex is None:
            self._tex = {
                'base': proctex.atlas_texture(self.atlas, 'base'),
                'normal': proctex.atlas_texture(self.atlas, 'normal'),
                'mr': proctex.atlas_texture(self.atlas, 'mr'),
            }

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
        self.program['u_chan'] = self.chan
        self.program['u_pom'] = self.pom
        self.program['u_cam_pos'] = self.cam
        self.program['u_sun_dir'] = SUN_DIR
        self.program['u_sun_color'] = SUN_COLOR
        self.program['u_sky_color'] = SKY_COLOR
        self.program['u_ground_color'] = GROUND_COLOR
        self.program['fog_color'] = FOG_COLOR
        self.program['fog_near'] = FOG_NEAR
        self.program['fog_far'] = FOG_FAR
        # ⚠️ pyglet 2.x 的 ``Texture.bind(texture_unit=0)`` **默认参数是 unit 0**，
        # 且内部自己 ``glActiveTexture``——外面先 glActiveTexture 再 bind() 会被
        # 拽回 unit 0，三张纹理全叠在一起（后绑的覆盖前面，unit1/2 空 → 采样黑）。
        # 正确用法：把单元号直接交给 bind()。
        self._tex['base'].bind(0)
        self._tex['normal'].bind(1)
        self._tex['mr'].bind(2)
        self.vl.draw(GL_TRIANGLES)

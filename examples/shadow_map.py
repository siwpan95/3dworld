# -*- coding: utf-8 -*-
"""阴影贴图（两趟渲染 / render-to-texture）：光源深度图 + PCF 软阴影。

这是 examples/ 里唯一一个**渲染两趟**的例子，也是唯一用到
``pyglet.image.Framebuffer`` 的例子：

1. **第一趟（深度趟）**：把相机放到光源位置（平行光 → 正交投影），
   把整场景的"到光源的深度"渲染进一张离屏纹理。深度用
   ``GL_R32F``（32 位浮点单通道）——**不能用默认的 RGBA8**：正交投影下
   深度线性映射到 [0,1]，8 位只有 255 级，在 60 单位的深度范围里每级约
   0.24 世界单位，比 bias 还大一个量级，结果是满屏条纹状自阴影（acné）。
   浮点纹理把这个问题从根上消掉。
2. **第二趟（主趟）**：正常渲染场景，片元着色器把世界坐标投到光源空间，
   与深度图比较得出阴影，3x3 PCF 让边缘变软；比较时按"法线越斜、bias 越大"
   做斜率偏移，进一步压住自阴影。
3. **验证**：右下角画中画直接显示第一趟的深度图——render-to-texture 是否
   真的产出了内容，肉眼即可确认（黑屏 = 没渲染成功）。

场景：棋盘地面 + 4 个转动/浮动的盒体 + 1 个球体，平行光绕场一周（30s 一整圈，
循环无缝）。法线不预存，用屏幕空间导数 ``cross(dFdx, dFdy)`` 现算几何法线
——盒体与球体都能得到正确的面片着色，省掉一整套法线数据。

用法::

    python examples/shadow_map.py
    python examples/shadow_map.py --shot       两张不同光照角度的验证图

按键：L 光源方位 +15°　B 切换 bias　S 阴影开关　A 自动环绕相机　
SPACE 暂停　鼠标拖动/滚轮　ESC 退出
"""
import math

import pyglet
from pyglet.gl import (GL_COLOR_ATTACHMENT0, GL_COLOR_BUFFER_BIT,
                       GL_DEPTH_ATTACHMENT, GL_DEPTH_BUFFER_BIT,
                       GL_DEPTH_COMPONENT24, GL_DEPTH_TEST, GL_NEAREST, GL_R32F,
                       GL_RED, GL_TEXTURE0, GL_TEXTURE_2D, GL_TRIANGLES,
                       glActiveTexture, glBindTexture, glClear, glClearColor,
                       glDisable, glEnable, glViewport)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.image import Framebuffer, Texture
from pyglet.image.buffer import Renderbuffer
from pyglet.math import Mat4, Vec3

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, orbit_camera_input, run
from core.geometry import box_data
from engine.soft_shadow import pcss_glsl
from logic.animation import quat_from_axis_angle, quat_to_mat4
from logic.camera import OrbitCamera, look_at_matrix

# ---------------------------------------------------------------- 着色器 --
DEPTH_VS = '''#version 330 core
in vec3 position;
uniform mat4 u_light_vp;
uniform mat4 u_model;
out float v_depth;
void main() {
    vec4 lp = u_light_vp * u_model * vec4(position, 1.0);
    // 正交投影 w=1，z 已是线性深度；[-1,1] → [0,1] 存进纹理
    v_depth = lp.z * 0.5 + 0.5;
    gl_Position = lp;
}'''

DEPTH_FS = '''#version 330 core
in float v_depth;
out vec4 final_colors;
void main() { final_colors = vec4(v_depth, 0.0, 0.0, 1.0); }'''

MAIN_VS = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 v_color;
out vec3 v_world;
out vec4 v_light;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 u_model;
uniform mat4 u_light_vp;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    v_world = wp.xyz;
    v_light = u_light_vp * wp;
    v_color = colors;
    gl_Position = window.projection * window.view * wp;
}'''

MAIN_FS = ('''#version 330 core
in vec4 v_color;
in vec3 v_world;
in vec4 v_light;
out vec4 final_colors;
uniform vec3  u_light_dir;    // 指向光源的单位向量
uniform vec3  u_eye;
uniform vec3  u_fog_color;
uniform sampler2D u_shadow;
uniform float u_bias;
uniform float u_texel;
uniform int   u_shadow_on;
uniform int   u_pcf;          // PCF 半径：1 → 3x3，2 → 5x5
uniform int   u_pcss;         // 1 = PCSS（P 键第三档）
uniform float u_light_size;   // 光源尺寸（影响半影宽度）
uniform float u_search_texels;// blocker search 半径（纹素）
uniform float u_max_penumbra; // 半影上限（纹素）
''' + pcss_glsl() + '''

float shadow_factor(float ndl) {
    vec3 p = v_light.xyz / v_light.w * 0.5 + 0.5;
    if (p.x < 0.0 || p.x > 1.0 || p.y < 0.0 || p.y > 1.0 || p.z > 1.0) {
        return 1.0;                       // 落在光源视锥外：不判阴影
    }
    // 斜率偏移：法线越背光，深度比较的容差越大（压住自阴影条纹）
    float bias = u_bias * (1.0 + 2.0 * (1.0 - ndl));
    if (u_pcss == 1) { return pcss_factor(p.xy, p.z, bias); }   // PCSS 档
    float sum = 0.0;
    float cnt = 0.0;
    // 固定跑 5x5 的循环范围，用半径 r 裁掉外圈：GLSL 330 的循环边界必须是
    // 常量，这样写才能让 3x3 / 5x5 共用一份代码（P 键切换）。
    for (int y = -2; y <= 2; ++y) {
        for (int x = -2; x <= 2; ++x) {
            if (abs(x) > u_pcf || abs(y) > u_pcf) { continue; }
            float d = texture(u_shadow, p.xy + vec2(float(x), float(y)) * u_texel).r;
            sum += (p.z - bias > d) ? 0.0 : 1.0;
            cnt += 1.0;
        }
    }
    return sum / cnt;
}

void main() {
    // 几何法线：不预存法线数据，用屏幕空间导数现算（面片着色）
    vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));
    float ndl = max(dot(n, u_light_dir), 0.0);
    float sh = (u_shadow_on == 1) ? shadow_factor(ndl) : 1.0;
    float amb = 0.25 + 0.12 * max(n.y, 0.0);
    vec3 lit = v_color.rgb * (amb + 0.95 * ndl * sh);
    // 一点高光，帮助看出球面曲率
    vec3 v = normalize(u_eye - v_world);
    lit += vec3(0.10) * pow(max(dot(reflect(-u_light_dir, n), v), 0.0), 28.0) * sh;
    lit = mix(lit, u_fog_color, smoothstep(24.0, 72.0, length(v_world - u_eye)));
    final_colors = vec4(lit, v_color.a);
}''')

PANEL_VS = '''#version 330 core
in vec3 position;
out vec2 v_uv;
uniform vec4 u_rect;        // 面板在 NDC 中的 (x0, y0, 宽, 高)
void main() {
    v_uv = position.xy * 0.5 + 0.5;
    // 局部 [-1,1]² → 面板矩形：这样面板是"像素正方形"，
    // 而不是跟着 NDC 被窗口宽高比拉扁（早期写死 NDC 坐标，
    // 1280x720 以外一律变形）。
    vec2 ndc = u_rect.xy + (position.xy * 0.5 + 0.5) * u_rect.zw;
    gl_Position = vec4(ndc, 0.0, 1.0);
}'''

PANEL_FS = '''#version 330 core
in vec2 v_uv;
out vec4 final_colors;
uniform sampler2D u_shadow;
uniform vec2 u_window;      // 显示拉伸窗口（深度图的原始 0..1 对比度太低）
uniform float u_texel;
void main() {
    float d = texture(u_shadow, v_uv).r;
    // 原始深度里"地面 ~0.44 / 物体 ~0.40 / 背景 1.0"挤在一起，直接显示看不出
    // 结构；按窗口线性拉伸，再叠一层深度不连续处的描边（等价于深度边缘检测），
    // 物体轮廓就一目了然了 —— 这是 render-to-texture 真的出了内容的实证。
    float t = clamp((d - u_window.x) / max(1e-6, u_window.y - u_window.x), 0.0, 1.0);
    float dx = abs(d - texture(u_shadow, v_uv + vec2(u_texel, 0.0)).r);
    float dy = abs(d - texture(u_shadow, v_uv + vec2(0.0, u_texel)).r);
    float edge = clamp(max(dx, dy) * 45.0, 0.0, 1.0);
    final_colors = vec4(vec3(mix(t, 1.0, edge)), 1.0);
}'''


# ---------------------------------------------------------------- 几何 --
def _sphere_data(radius, rings, sectors, rgb):
    """UV 球体（面片法线由着色器现算，故顶点只要 position/color）。"""
    verts, colors = [], []
    for i in range(rings):
        t0 = math.pi * i / rings
        t1 = math.pi * (i + 1) / rings
        for j in range(sectors):
            p0 = 2 * math.pi * j / sectors
            p1 = 2 * math.pi * (j + 1) / sectors
            quad = []
            for (t, p) in ((t0, p0), (t1, p0), (t1, p1), (t0, p0), (t1, p1), (t0, p1)):
                quad.append((radius * math.sin(t) * math.cos(p),
                             radius * math.cos(t),
                             radius * math.sin(t) * math.sin(p)))
            for x, y, z in quad:
                verts += [x, y, z]
                colors += [rgb[0], rgb[1], rgb[2], 1.0]
    return verts, colors


def _ground_data(half=13, tile=1.0):
    """棋盘地面（顶面朝上，单个合并网格）。"""
    verts, colors = [], []
    for ix in range(-half, half):
        for iz in range(-half, half):
            x0, z0 = ix * tile, iz * tile
            x1, z1 = x0 + tile, z0 + tile
            quad = [(x0, 0.0, z0), (x1, 0.0, z0), (x1, 0.0, z1),
                    (x0, 0.0, z0), (x1, 0.0, z1), (x0, 0.0, z1)]
            g = 0.62 if (ix + iz) % 2 == 0 else 0.44
            for p in quad:
                verts += list(p)
                colors += [g, g * 0.98, g * 0.92, 1.0]
    return verts, colors


# 场景物体：(名字, 尺寸或球半径, 基准位置, 颜色, 动画描述)
_BOXES = (
    ('a', (2.2, 3.0, 2.2), (-3.6, 1.50, 0.2), (0.86, 0.36, 0.30), ('y', 22.0, 0.0)),
    ('b', (1.6, 1.6, 1.6), (0.6, 0.80, -2.6), (0.30, 0.56, 0.86), ('y', -34.0, 1.1)),
    ('c', (1.2, 2.6, 1.2), (3.4, 1.30, 1.4), (0.92, 0.74, 0.26), ('z', 18.0, 2.2)),
    ('d', (2.8, 0.9, 1.5), (0.4, 3.10, -5.4), (0.46, 0.78, 0.44), ('x', 26.0, 0.6)),
)
_SPHERE = ('s', 1.35, (-1.2, 1.35, -3.4), (0.80, 0.80, 0.86))
BIASES = (0.0008, 0.0018, 0.0035)


class ShadowDemo(DemoApp):
    TITLE = '阴影贴图 · 离屏深度图(R32F) + PCF 软阴影 (pyglet Framebuffer)'
    HELP = ('L 光源方位 +15°   B 切换 bias   P 切换 PCF 3x3/5x5   S 阴影开关   '
            'A 自动环绕   鼠标拖动/滚轮   SPACE 暂停   ESC 退出')
    DURATION = 30.0
    MAP_SIZE = 1024
    LIGHT_RANGE = 14.0          # 光源正交视锥半宽（越小 → 深度精度越高）
    SKY = (0.52, 0.62, 0.76)
    SHOTS = ((4.0, 'docs/demo_shadow_a.png'),
             (13.0, 'docs/demo_shadow_b.png'))

    # ------------------------------------------------------------ 资源 --
    def setup(self):
        self.depth_prog = ShaderProgram(Shader(DEPTH_VS, 'vertex'),
                                        Shader(DEPTH_FS, 'fragment'))
        self.main_prog = ShaderProgram(Shader(MAIN_VS, 'vertex'),
                                       Shader(MAIN_FS, 'fragment'))
        self.panel_prog = ShaderProgram(Shader(PANEL_VS, 'vertex'),
                                        Shader(PANEL_FS, 'fragment'))

        # 场景网格：主趟与深度趟各建一份（顶点属性集合不同）
        self.ground_v, self.ground_c = _ground_data()
        self.objs = []                                  # [(name, model_fn, color, verts, colors)]
        for name, dims, pos, rgb, anim in _BOXES:
            v, c = box_data(dims[0], dims[1], dims[2], 0, 0, 0, rgb)
            self.objs.append((name, (pos, anim), rgb, v, c))
        sv, sc = _sphere_data(_SPHERE[1], 16, 28, _SPHERE[3])
        self.objs.append((_SPHERE[0], (_SPHERE[2], None), _SPHERE[3], sv, sc))

        self.main_lists, self.depth_lists = [], []
        for _n, _m, _rgb, v, c in self.objs:
            self.main_lists.append(self.main_prog.vertex_list(
                len(v) // 3, GL_TRIANGLES, position=('f', v), colors=('f', c)))
            # 深度趟不需要颜色：着色器里没声明 colors，传了也会被跳过
            self.depth_lists.append(self.depth_prog.vertex_list(
                len(v) // 3, GL_TRIANGLES, position=('f', v)))
        self.ground_main = self.main_prog.vertex_list(
            len(self.ground_v) // 3, GL_TRIANGLES,
            position=('f', self.ground_v), colors=('f', self.ground_c))
        self.ground_depth = self.depth_prog.vertex_list(
            len(self.ground_v) // 3, GL_TRIANGLES, position=('f', self.ground_v))

        # 画中画：把深度图贴在右下角。顶点是**面板局部**的 [-1,1]²，
        # 实际位置/尺寸由 PANEL_VS 的 u_rect 每帧按窗口尺寸算（见 render），
        # 这样面板在任何宽高比下都是像素正方形。
        q = (-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 1.0, 0.0,
             -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, -1.0, 1.0, 0.0)
        self.panel = self.panel_prog.vertex_list(6, GL_TRIANGLES,
                                                 position=('f', q))

        # 阴影贴图：R32F 颜色附件 + 深度 renderbuffer
        size = self.MAP_SIZE
        self.shadow_tex = Texture.create(size, size, GL_TEXTURE_2D, GL_R32F,
                                         GL_NEAREST, GL_NEAREST, GL_RED)
        self.depth_rb = Renderbuffer(size, size, GL_DEPTH_COMPONENT24)
        self.fbo = Framebuffer()
        self.fbo.attach_texture(self.shadow_tex, attachment=GL_COLOR_ATTACHMENT0)
        self.fbo.attach_renderbuffer(self.depth_rb, attachment=GL_DEPTH_ATTACHMENT)
        self.fbo_ok = bool(self.fbo.is_complete)
        if not self.fbo_ok:
            print('[shadow_map] WARNING: framebuffer incomplete ->',
                  self.fbo.get_status(), '(shadows disabled, scene only)')

        self.camera = OrbitCamera(target=Vec3(0, 1.0, -1.5),
                                  yaw=35.0, pitch=26.0, dist=17.0)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(self, self.camera)
        self.light_offset = 0.0
        self.bias_i = 1
        self.shadow_on = 1
        # 阴影模式（P 键三档循环）：0=PCF 3x3  1=PCF 5x5  2=PCSS
        self.shadow_mode = 0
        self.light_size = 2.6        # PCSS 光源尺寸（越大半影越宽）
        self.search_texels = 6.0     # blocker search 半径（纹素）
        self.max_penumbra = 14.0     # 半影上限（纹素）
        self.auto_orbit = True
        # 命令行起始状态：--flag pcf5 / --flag nobias-… 便于无人值守验证
        if 'pcf3' in self.args.flags:
            self.shadow_mode = 0
        if 'pcf5' in self.args.flags:
            self.shadow_mode = 1
        if 'pcss' in self.args.flags:
            self.shadow_mode = 2
        if 'noshadow' in self.args.flags:
            self.shadow_on = 0
        if 'noon' in self.args.flags:
            self.auto_orbit = False

    # ------------------------------------------------------------ 帧 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.L:
            self.light_offset = (self.light_offset + 15.0) % 360.0
        elif symbol == key.B:
            self.bias_i = (self.bias_i + 1) % len(BIASES)
        elif symbol == key.S:
            self.shadow_on = 0 if self.shadow_on else 1
        elif symbol == key.P:
            self.shadow_mode = (self.shadow_mode + 1) % 3   # PCF3x3 → PCF5x5 → PCSS
        elif symbol == key.A:
            self.auto_orbit = not self.auto_orbit

    def _light(self):
        """平行光：绕场一周恰好一个循环（30s），t=0 与 t=30 同角度，循环无缝。"""
        az = math.radians(28.0 + self.light_offset + 360.0 * self.time / self.DURATION)
        el = math.radians(44.0)
        to_light = Vec3(math.cos(el) * math.cos(az), math.sin(el),
                        math.cos(el) * math.sin(az))
        eye = Vec3(0, 0, 0) + to_light * 34.0
        view = look_at_matrix(eye, Vec3(0, 1.0, -1.5), Vec3(0, 1, 0))
        proj = Mat4.orthogonal_projection(-self.LIGHT_RANGE, self.LIGHT_RANGE,
                                          -self.LIGHT_RANGE, self.LIGHT_RANGE,
                                          1.0, 70.0)
        return to_light, proj @ view, eye

    def _model(self, slot):
        pos, anim = slot
        if anim is None:
            return Mat4.from_translation(Vec3(*pos))
        axis, speed, phase = anim
        deg = speed * self.time + phase * 57.2958
        axis_vec = {'x': (1, 0, 0), 'y': (0, 1, 0), 'z': (0, 0, 1)}[axis]
        bob = 0.5 * math.sin(self.time * 1.1 + phase)
        return (Mat4.from_translation(Vec3(pos[0], pos[1] + bob, pos[2]))
                @ quat_to_mat4(quat_from_axis_angle(axis_vec, deg)))

    def render(self):
        w, h = self.window.width, self.window.height
        to_light, light_vp, light_eye = self._light()
        models = [self._model(slot) for _n, slot, _c, _v, _cc in self.objs]

        # ---- 第一趟：光源视角的深度图 ----
        if self.fbo_ok:
            self.fbo.bind()
            glViewport(0, 0, self.MAP_SIZE, self.MAP_SIZE)
            glClearColor(1.0, 1.0, 1.0, 1.0)         # 背景 = 最远
            # **不要用 fbo.clear()**：pyglet 2.1.16 的 Framebuffer.clear() 把
            # 附件点枚举（GL_COLOR_ATTACHMENT0|GL_DEPTH_ATTACHMENT）当成
            # glClear 的掩码传下去，驱动直接报 GL_INVALID_VALUE(0x1281)。
            # 正确掩码是 GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT，这里手工清。
            glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            glEnable(GL_DEPTH_TEST)
            prog = self.depth_prog
            prog.use()
            prog['u_light_vp'] = light_vp
            prog['u_model'] = Mat4()
            self.ground_depth.draw(GL_TRIANGLES)
            for vl, model in zip(self.depth_lists, models):
                prog['u_model'] = model
                vl.draw(GL_TRIANGLES)
            self.fbo.unbind()

        # ---- 第二趟：正常渲染 + 阴影采样 ----
        self.window.viewport = (0, 0, w, h)
        glClearColor(self.SKY[0], self.SKY[1], self.SKY[2], 1.0)
        glEnable(GL_DEPTH_TEST)
        self.window.view = self.camera.view_matrix()
        prog = self.main_prog
        prog.use()
        prog['u_light_vp'] = light_vp          # 主趟也要光源矩阵：漏了它 → v_light=0
        prog['u_light_dir'] = to_light         # → w=0 → NaN → 判定短路 → 满屏无阴影
        prog['u_eye'] = self.camera.eye
        prog['u_fog_color'] = self.SKY
        prog['u_bias'] = BIASES[self.bias_i]
        prog['u_texel'] = 1.0 / float(self.MAP_SIZE)
        prog['u_shadow_on'] = self.shadow_on if self.fbo_ok else 0
        prog['u_pcf'] = 1 if self.shadow_mode == 0 else 2
        prog['u_pcss'] = 1 if self.shadow_mode == 2 else 0
        prog['u_light_size'] = self.light_size
        prog['u_search_texels'] = self.search_texels
        prog['u_max_penumbra'] = self.max_penumbra
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, self.shadow_tex.id)
        prog['u_shadow'] = 0
        prog['u_model'] = Mat4()
        self.ground_main.draw(GL_TRIANGLES)
        for vl, model in zip(self.main_lists, models):
            prog['u_model'] = model
            vl.draw(GL_TRIANGLES)

        # ---- 画中画：显示第一趟的深度图（render-to-texture 的实证）----
        if self.fbo_ok:
            # 面板按像素正方形摆放（占短边 24%），并避开底部 HUD 暗条；
            # 每帧重算 → 改窗口尺寸/换宽高比都不会拉伸或压字。
            side = max(64, int(min(w, h) * 0.24))
            x0 = (w - 14 - side) / w * 2.0 - 1.0
            y0 = (14 + 30 + 6) / h * 2.0 - 1.0
            self.panel_prog['u_rect'] = (x0, y0, 2.0 * side / w, 2.0 * side / h)

            glDisable(GL_DEPTH_TEST)
            self.panel_prog.use()
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, self.shadow_tex.id)
            self.panel_prog['u_shadow'] = 0
            self.panel_prog['u_window'] = (0.30, 0.68)
            self.panel_prog['u_texel'] = 1.0 / float(self.MAP_SIZE)
            self.panel.draw(GL_TRIANGLES)
            glEnable(GL_DEPTH_TEST)

    def update(self, dt):
        if self.auto_orbit:
            self.camera.yaw += dt * 4.0

    def status_text(self):
        _, _, light_eye = self._light()
        az = (28.0 + self.light_offset + 360.0 * self.time / self.DURATION) % 360.0
        mode = ('PCF 3x3', 'PCF 5x5',
                f'PCSS(light={self.light_size:.1f} search={self.search_texels:.0f}tex)')[self.shadow_mode]
        return (f'光源方位 {az:5.1f}°   bias {BIASES[self.bias_i]:.4f}   {mode}   '
                f'阴影 {"开" if self.shadow_on else "关"}   '
                f'影图 {self.MAP_SIZE}²   {"FBO-OK" if self.fbo_ok else "FBO-FAIL"}   '
                f'{self.fps:5.1f} fps{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(ShadowDemo)

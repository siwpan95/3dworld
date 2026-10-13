# -*- coding: utf-8 -*-
"""体积雾与光轴（god rays）—— ④-2。

**为什么这值得单独做一层**：体积雾最容易写成"沿射线采几步、每步乘 exp(-ρ·dt)" ——
能跑，但有两个硬问题：

1. **高度雾的密度随高度指数衰减**，逐步矩形求和（每步取起点密度）在低步数时会出
   **层状条纹**，而且"改步数会让整幅雾变浓变淡"。这一段的积分**有闭式解**，
   用上它之后"步数只影响散射采样精度、不影响雾的浓度"。本 demo 按 **A** 随时在
   「闭式 / 逐步矩形」之间切换 —— 两者并排看，条纹与整体亮度漂移一眼可见。
2. **相函数不能省**：各向同性散射看不见光柱，雾的强前向散射（g≈0.7~0.9）才是
   "太阳在柱子后面出现光柱"的成因。按 **G** 调各向异性，前向/后向的差别很直观。

数学与 GLSL 全在 `engine/volumetrics.py`（纯函数、可 headless 断言）。本文件的活是
把四趟渲染串起来：**深度趟 → 阴影趟 → 场景趟 → 合成趟**。

用法::

    python examples/volumetric_fog.py
    python examples/volumetric_fog.py --flag thick       # 起手就是浓雾

按键：F 雾开关   S 光轴开关   A 闭式/逐步矩形对照   ↑/↓ 密度   G 相函数
      PAGE_UP/PAGE_DOWN 步数   SPACE 暂停   拖动/滚轮 看视角   ESC 退出
"""
import math
import os
import sys

from pyglet.gl import (GL_COLOR_ATTACHMENT0, GL_COLOR_BUFFER_BIT, GL_DEPTH_ATTACHMENT,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_COMPONENT24, GL_DEPTH_TEST,
                       GL_NEAREST, GL_R16F, GL_R32F, GL_RED, GL_RGBA, GL_RGBA16F,
                       GL_TEXTURE0, GL_TEXTURE_2D, GL_TRIANGLES,
                       glClear, glClearColor, glDisable, glEnable, glViewport)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.image import Framebuffer, Texture
from pyglet.image.buffer import Renderbuffer
from pyglet.math import Mat4, Vec3
from pyglet.window import key

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError（P2-1 全量验收实测，30 个 demo 一起炸）。
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, orbit_camera_input, run  # noqa: E402
from core.geometry import box_data  # noqa: E402
from engine.csm import csm_glsl, csm_matrices, csm_uniforms, mat4_as_Mat4  # noqa: E402
from engine.mathkit import mat4_from_Mat4, mat4_mul, mat4_to_Mat4  # noqa: E402
from engine.targets import RenderTarget, bind_texture  # noqa: E402
from engine.volumetrics import dither_offset, volumetric_glsl  # noqa: E402
from logic.camera import OrbitCamera  # noqa: E402

WINDOW_BLOCK = '''uniform WindowBlock {
    mat4 projection;
    mat4 view;
} window;
'''

# ---- 场景趟：普通前向着色（法线用屏幕导数现算，省一整套法线数据）----
SCENE_VS = '#version 330 core\n' + WINDOW_BLOCK + '''
in vec3 position;
in vec4 colors;
uniform mat4 u_model;
out vec3 v_world;
out vec3 v_col;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    v_world = wp.xyz;
    v_col = colors.rgb;
    gl_Position = window.projection * window.view * wp;
}
'''

SCENE_FS = '#version 330 core\n' + WINDOW_BLOCK + '''
in vec3 v_world;
in vec3 v_col;
out vec4 final_colors;
uniform vec3 u_light_dir;      // 指向光源
uniform vec3 u_light_color;
void main() {
    vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));
    float ndl = max(dot(n, u_light_dir), 0.0);
    float amb = 0.34 + 0.14 * max(n.y, 0.0);
    vec3 col = v_col * (amb + 0.95 * ndl) * mix(vec3(1.0), u_light_color, 0.55);
    final_colors = vec4(col, 1.0);
}
'''

# ---- 深度趟：写**线性距离**（不是深度缓冲原值）—— 体积雾直接拿它当积分上界 ----
DEPTH_VS = '#version 330 core\n' + WINDOW_BLOCK + '''
in vec3 position;
uniform mat4 u_model;
uniform vec3 u_cam;            // 相机世界坐标：距离 = |世界点 − 相机|
out float v_dist;
void main() {
    vec4 wp = u_model * vec4(position, 1.0);
    v_dist = length(wp.xyz - u_cam);
    gl_Position = window.projection * window.view * wp;
}
'''

DEPTH_FS = '''#version 330 core
in float v_dist;
out vec4 final_colors;
void main() { final_colors = vec4(v_dist, 0.0, 0.0, 1.0); }
'''

# 阴影趟（正交光空间，与 `examples/shadow_map.py` 同约定）
SHADOW_VS = '''#version 330 core
in vec3 position;
uniform mat4 u_light_vp;
uniform mat4 u_model;
out float v_depth;
void main() {
    vec4 lp = u_light_vp * u_model * vec4(position, 1.0);
    v_depth = lp.z * 0.5 + 0.5;
    gl_Position = lp;
}
'''

SHADOW_FS = '''#version 330 core
in float v_depth;
out vec4 final_colors;
void main() { final_colors = vec4(v_depth, 0.0, 0.0, 1.0); }
'''

# ---- 合成趟：场景色 × 透射率 + 散射 + 曝光/tonemap ----
# `csm_glsl(1)`（单级阴影 —— 光轴只需要"这点被不被挡住"）
# 与 `volumetric_glsl(...)`（体积雾）拼在一个片段里：两者都自带 uniform 声明。
COMPOSITE_VS = '''#version 330 core
in vec2 position;
in vec2 uv;
out vec2 v_uv;
void main() {
    v_uv = uv;
    gl_Position = vec4(position, 0.0, 1.0);
}
'''

COMPOSITE_FS_HEAD = '#version 330 core\n'
COMPOSITE_FS_BODY = '''
in vec2 v_uv;
out vec4 final_colors;
uniform sampler2D u_scene;      // 场景色（HDR）
uniform float u_fog_on;         // 0/1：整体开关（雾+光轴一起关）
uniform float u_exposure;

// Reinhard + gamma（演示用；项目的正式 tone-map 在 `engine/postfx.py`）
vec3 tonemap(vec3 c) {
    c *= u_exposure;
    return pow(c / (1.0 + c), vec3(1.0 / 2.2));
}

void main() {
    vec3 scene = texture(u_scene, v_uv).rgb;
    vec3 col = scene;
    if (u_fog_on > 0.5) {
        float scene_dist = vol_fog_scene_dist(v_uv);
        // 抖动起点用模块提供的交错梯度噪声（CPU 侧 `dither_offset` 同式）
        vec4 f = vol_fog(v_uv, scene_dist, vol_fog_dither(gl_FragCoord.xy));
        // 先乘透射率，再加散射（能量守恒的顺序：先衰减再加光源项）
        col = scene * f.a + f.rgb;
    }
    final_colors = vec4(tonemap(col), 1.0);
}
'''

GROUND = (0.20, 0.20, 0.23, 1.0)
PILLAR = (0.62, 0.60, 0.55, 1.0)
PILLAR_ALT = (0.55, 0.55, 0.60, 1.0)
BEAM = (0.40, 0.38, 0.36, 1.0)

#: 场景清单：(尺寸, 世界位置, 颜色)——确定性，剧照可复现
SCENE = []


def _build_scene():
    if SCENE:
        return SCENE
    # 两列柱廊 + 顶梁：太阳在远处低角度，光从柱缝里穿过来 ⇒ 光轴
    for i in range(9):
        z = -4.0 - i * 9.0
        for sx in (-7.0, 7.0):
            SCENE.append(((3.0, 11.0, 3.0), (sx, 5.5, z),
                          PILLAR if i % 2 == 0 else PILLAR_ALT))
    for sx in (-7.0, 7.0):
        SCENE.append(((3.4, 1.6, 84.0), (sx, 11.8, -40.0), BEAM))
    # 几块浮空石：给雾"分层"的参照物（近处浓、高处淡一眼可见）
    for (x, y, z, s) in ((0.0, 14.0, -18.0, 3.2), (-3.5, 17.0, -44.0, 2.4),
                         (4.0, 20.0, -70.0, 3.6), (0.0, 24.0, -96.0, 4.2)):
        SCENE.append(((s, s * 0.7, s), (x, y, z), (0.44, 0.43, 0.47, 1.0)))
    return SCENE


class VolumetricFogApp(DemoApp):
    TITLE = '体积雾 / 光轴 —— 高度雾闭式积分 + 单次散射'
    HELP = ('F 雾  S 光轴  A 闭式⇄逐步矩形  ↑/↓ 密度  G 相函数  PGUP/PGDN 步数  '
            '拖动/滚轮 看视角   SPACE 暂停   ESC 退出')
    DURATION = 60.0                     # 光源绕场一周，循环无缝
    HUD = True
    SHOTS = ((5.0, 'docs/fog_01.png'),   # 光轴穿过柱廊
             (26.0, 'docs/fog_02.png'))  # 光转到另一侧，雾的分层更明显

    SHADOW_SIZE = 1024

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        # 合成趟：CSM（1 级）+ 体积雾。两模块各自带 uniform 声明，直接拼。
        self.composite_prog = ShaderProgram(
            Shader(COMPOSITE_VS, 'vertex'),
            Shader(COMPOSITE_FS_HEAD + csm_glsl(1)
                   + volumetric_glsl('vol_fog', 'csm_shadow') + COMPOSITE_FS_BODY,
                   'fragment'))
        self.scene_prog = ShaderProgram(Shader(SCENE_VS, 'vertex'),
                                       Shader(SCENE_FS, 'fragment'))
        self.shadow_prog = ShaderProgram(Shader(SHADOW_VS, 'vertex'),
                                        Shader(SHADOW_FS, 'fragment'))
        self.depth_prog = ShaderProgram(Shader(DEPTH_VS, 'vertex'),
                                       Shader(DEPTH_FS, 'fragment'))

        # 相机：站在柱廊入口，沿廊看进去（低俯角 ⇒ 光轴横穿画面）
        # yaw=90° ⇒ 相机落在 +z（柱廊入口），沿 −z 看进去；再配上"太阳在 −z"，
        # 视线正对光源 ⇒ 柱缝里的光轴才会横穿画面（背对光源是看不到光轴的）。
        self.cam = OrbitCamera(target=Vec3(0, 4, -50), yaw=90.0, pitch=7.0,
                               dist=58.0, pitch_range=(2.0, 40.0),
                               dist_range=(12.0, 140.0))
        self.cam.fov = 55.0
        self.cam.near = 0.5
        self.cam.far = 400.0

        # ---- flags ----
        thick = 'thick' in self.args.flags
        # 这两个 flag 既是交互默认值，也是排查用的"只留场景"开关
        self.fog_on = 0.0 if 'nofog' in self.args.flags else 1.0
        self.shafts_on = 0.0 if 'noshaft' in self.args.flags else 1.0
        self.analytic = 1.0
        self.density = 0.075 if thick else 0.040
        self.falloff = 0.16
        self.base_y = 0.0
        self.phase_g = 0.72
        self.steps = 48.0
        self.exposure = 0.95
        self.ambient = (0.085, 0.10, 0.135)

        # ---- 几何 ----
        self.ground_v = (-260.0, 0.0, -260.0, 260.0, 0.0, -260.0, 260.0, 0.0, 260.0,
                         -260.0, 0.0, -260.0, 260.0, 0.0, 260.0, -260.0, 0.0, 260.0)
        self.ground_c = GROUND * 6
        self.scene_lists = []
        for (dims, pos, col) in _build_scene():
            v, c = box_data(dims[0], dims[1], dims[2], 0, 0, 0, col[:3])
            self._add_box(v, c, pos)
        self._add_box(self.ground_v, self.ground_c, (0, 0, 0))

        # ---- 渲染目标 ----
        self._build_targets(self.window.width, self.window.height)
        self.shadow_tex, self.shadow_fbo = self._make_r32(self.SHADOW_SIZE,
                                                          self.SHADOW_SIZE)

        # ---- 全屏三角 ----
        # ⚠ **uv 必须是 `ndc*0.5+0.5`**，不能直接把 NDC 当 uv 传。
        # 实测：把 uv 写成 (-1,-1),(3,-1),(-1,3) 之后，屏幕上的 uv 范围是 [−1,1]，
        # 于是采样 `texture(u_scene, uv)` 时四周会**平铺/镜像**（画面上下各出现一份
        # 一模一样的内容），而 HUD 正常 —— 很容易误判成"场景画了两遍"。
        quad_pos = (-1.0, -1.0, 3.0, -1.0, -1.0, 3.0)
        quad_uv = (0.0, 0.0, 2.0, 0.0, 0.0, 2.0)
        self.quad = self.composite_prog.vertex_list(
            3, GL_TRIANGLES, position=('f', quad_pos), uv=('f', quad_uv))

        orbit_camera_input(self, self.cam)
        self.frames = 0

    # ------------------------------------------------------------ 资源 --
    def _add_box(self, verts, colors, pos):
        model = Mat4.from_translation(Vec3(*pos))
        self.scene_lists.append((
            self.scene_prog.vertex_list(len(verts) // 3, GL_TRIANGLES,
                                        position=('f', verts), colors=('f', colors)),
            self.shadow_prog.vertex_list(len(verts) // 3, GL_TRIANGLES,
                                         position=('f', verts)),
            self.depth_prog.vertex_list(len(verts) // 3, GL_TRIANGLES,
                                        position=('f', verts)),
            model))

    def _make_r32(self, w, h, fmt=GL_R32F):
        tex = Texture.create(w, h, GL_TEXTURE_2D, fmt, GL_NEAREST, GL_NEAREST, GL_RED)
        fbo = Framebuffer()
        fbo.attach_texture(tex, attachment=GL_COLOR_ATTACHMENT0)
        fbo.attach_renderbuffer(Renderbuffer(w, h, GL_DEPTH_COMPONENT24),
                                attachment=GL_DEPTH_ATTACHMENT)
        if not bool(fbo.is_complete):
            print(f'[fog] WARNING: framebuffer 不完整 -> {fbo.get_status()}', flush=True)
        return tex, fbo

    def _build_targets(self, w, h):
        """场景色（HDR）+ 线性距离（R32F）两个目标，随窗口尺寸重建。"""
        self.scene_rt = RenderTarget(max(1, w), max(1, h), color_count=1, depth=True)
        self.dist_tex, self.dist_fbo = self._make_r32(max(1, w), max(1, h))
        self._rt_size = (w, h)

    # ------------------------------------------------------------ 光源 --
    def _light_dir(self):
        # 方位角绕 252°±一周：太阳大部分时间在 −z（正对视线）附近 ⇒ 光轴可见；
        # 转到背面时则变成"逆光剪影"，同一段成片里有两种画面。
        az = math.radians(252.0 + 360.0 * self.time / self.DURATION)
        el = math.radians(16.0)              # 低太阳：影子拉长、光轴明显
        return (math.cos(el) * math.cos(az), math.sin(el),
                math.cos(el) * math.sin(az))

    # ------------------------------------------------------------ 键盘 --
    def on_key(self, symbol, modifiers):
        if symbol == key.F:
            self.fog_on = 0.0 if self.fog_on > 0.5 else 1.0
        elif symbol == key.S:
            self.shafts_on = 0.0 if self.shafts_on > 0.5 else 1.0
        elif symbol == key.A:
            self.analytic = 0.0 if self.analytic > 0.5 else 1.0
            print(f'[fog] 高度雾积分 = {"闭式" if self.analytic else "逐步矩形（对照组）"}',
                  flush=True)
        elif symbol == key.UP:
            self.density = min(0.4, self.density * 1.25)
        elif symbol == key.DOWN:
            self.density = max(0.002, self.density / 1.25)
        elif symbol == key.G:
            self.phase_g = 0.0 if self.phase_g > 0.9 else self.phase_g + 0.2
        elif symbol == key.PAGEUP:
            self.steps = min(256.0, self.steps * 1.5)
        elif symbol == key.PAGEDOWN:
            self.steps = max(4.0, self.steps / 1.5)
        else:
            super().on_key(symbol, modifiers)

    # ------------------------------------------------------------ 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        if (w, h) != self._rt_size:          # 窗口尺寸变了：离屏目标必须跟着重建
            self.scene_rt.delete()
            self.dist_fbo.delete()
            self.dist_tex.delete()
            self._build_targets(w, h)
        light = self._light_dir()
        aspect = w / max(1, h)
        self.window.view = self.cam.view_matrix()
        self.window.projection = self.cam.projection(aspect)
        view_tuple = mat4_from_Mat4(self.cam.view_matrix())

        # ---- 阴影趟：单级正交光空间深度图（光轴"这点被挡住没"就查它）----
        tf = math.tan(math.radians(self.cam.fov) / 2.0)
        casc = csm_matrices(view_tuple, self.cam.near, self.cam.far * 0.6, tf, aspect,
                            light, n=1, size=self.SHADOW_SIZE, pad=2.0, snap=True)
        self.shadow_fbo.bind()
        glViewport(0, 0, self.SHADOW_SIZE, self.SHADOW_SIZE)
        glClearColor(1.0, 1.0, 1.0, 1.0)
        # 手写掩码：pyglet 的 `Framebuffer.clear()` 会把附件点当 glClear 掩码传下去
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.shadow_prog.use()
        self.shadow_prog['u_light_vp'] = mat4_as_Mat4(casc[0]['vp'])
        for (_main, shadow_vl, _depth, model) in self.scene_lists:
            self.shadow_prog['u_model'] = model
            shadow_vl.draw(GL_TRIANGLES)
        self.shadow_fbo.unbind()

        # ---- 深度趟：相机视角的**线性距离** ----
        self.dist_fbo.bind()
        glViewport(0, 0, w, h)
        glClearColor(1e6, 1e6, 1e6, 1.0)     # "无限远"：没有几何的地方雾要能一直飘
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.depth_prog.use()
        self.depth_prog['u_cam'] = self.cam.eye
        for (_main, _shadow, depth_vl, model) in self.scene_lists:
            # 距离在顶点着色器里由世界坐标算（省得再传逆 view-projection）
            self.depth_prog['u_model'] = model
            depth_vl.draw(GL_TRIANGLES)
        self.dist_fbo.unbind()

        # ---- 场景趟：HDR 颜色 ----
        self.scene_rt.bind()
        glViewport(0, 0, w, h)
        glClearColor(0.02, 0.025, 0.035, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.scene_prog.use()
        self.scene_prog['u_light_dir'] = light
        self.scene_prog['u_light_color'] = (1.0, 0.86, 0.66)
        for (main_vl, _shadow, _depth, model) in self.scene_lists:
            self.scene_prog['u_model'] = model
            main_vl.draw(GL_TRIANGLES)
        self.scene_rt.unbind()

        # ---- 合成趟：雾 + 曝光 + tonemap ----
        glViewport(0, 0, w, h)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.composite_prog
        prog.use()
        bind_texture(0, self.scene_rt.color[0])
        prog['u_scene'] = 0
        bind_texture(1, self.dist_tex)
        prog['u_fog_depth'] = 1
        bind_texture(2, self.shadow_tex)
        prog['u_csm_tex0'] = 2
        prog['u_csm_vp0'] = list(casc[0]['vp'])
        # ⚠ 单元素数组 uniform 在 pyglet 里会退化成**标量**（GL 报 size=1 ⇒ 走标量
        # setter）—— 给它传 `[0.5]` 会 `TypeError: must be real number, not list`。
        # 这与 `mat4[]` 数组那条坑同源（pyglet 对"数组的尺寸"处理有边界情况）。
        prog['u_csm_split'] = casc[0]['split']
        prog['u_csm_pcf'] = 1.0
        # 屏幕 → 世界的逆矩阵：给体积雾反推射线方向用
        vp = mat4_mul(tuple(self.cam.projection(aspect)), view_tuple)
        from engine.mathkit import mat4_inverse
        prog['u_inv_vp'] = list(mat4_inverse(vp))
        prog['u_fog_cam'] = (self.cam.eye.x, self.cam.eye.y, self.cam.eye.z)
        prog['u_fog_light_dir'] = light
        prog['u_fog_light_color'] = (1.0, 0.84, 0.62)
        prog['u_fog_ambient'] = self.ambient
        prog['u_fog_density'] = self.density
        prog['u_fog_falloff'] = self.falloff
        prog['u_fog_base'] = self.base_y
        prog['u_fog_phase_g'] = self.phase_g
        prog['u_fog_steps'] = self.steps
        prog['u_fog_max_dist'] = float(self.cam.far)
        prog['u_fog_analytic'] = self.analytic
        prog['u_fog_shafts'] = self.shafts_on
        prog['u_fog_on'] = self.fog_on
        prog['u_exposure'] = self.exposure
        self.quad.draw(GL_TRIANGLES)
        self.frames += 1

    # ------------------------------------------------------------ HUD --
    def status_text(self):
        return (f'密度 {self.density:.3f} 衰减 {self.falloff:.2f} 相函数 g={self.phase_g:.2f} '
                f'步数 {self.steps:.0f}  高度雾积分 {"闭式" if self.analytic > 0.5 else "逐步矩形"}  '
                f'{"[雾]" if self.fog_on > 0.5 else "[无雾]"}'
                f'{"[光轴]" if self.shafts_on > 0.5 else "[无光轴]"}  '
                f'阴影图 {self.SHADOW_SIZE}²  曝光 {self.exposure:.2f}  '
                f't={self.time:4.1f}s {self.fps:4.1f}fps')


if __name__ == '__main__':
    run(VolumetricFogApp)

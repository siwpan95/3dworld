# -*- coding: utf-8 -*-
"""运动模糊（Motion Blur）—— ④-4：相机重投影 + 快门加权累积。

**这一项为什么最便宜、又最容易被做假**：屏幕空间的"整屏方向模糊"（拿一个全局向量去采样）
看起来像蒙了层纱 —— **它让所有像素糊得一样多**。真实的运动模糊里
**近处的东西扫过的像素多得多**，而且要满足三条只有带深度才做得到的律：

====================  ==========================================
相机**平移**           运动矢量 ∝ **1/z**（近处扫得远、远处几乎不动）
相机**只旋转**         运动矢量与深度**无关**（远处的山也糊）
相机**不动**           运动矢量**恒为 0**（不需要额外的"静止检测"）
====================  ==========================================

本 demo 把这三种相机运动做成了可切换的档（`1`~`4`），按 `V` 还能直接看运动矢量图 ——
一眼能看出"平移时近处的矢量被拉长、远处缩成一个点"。

数学与 GLSL 在 `engine/motionblur.py`（纯函数、可 headless 断言；三条律都是测试项）。
本文件的活：**深度趟 → 场景趟 → 合成趟**，并把上一帧的 view-projection 存下来。

用法::

    python examples/motion_blur.py
    python examples/motion_blur.py --flag stop      # 静止相机（验证模糊真的会消失）

按键：1 平移  2 原地转  3 推近  4 静止   ` 快门比例   N 采样数
      M 模糊开关   V 看运动矢量   SPACE 暂停   拖动/滚轮 看视角   ESC 退出
"""
import math
import os
import sys

from pyglet.gl import (GL_COLOR_ATTACHMENT0, GL_COLOR_BUFFER_BIT, GL_DEPTH_ATTACHMENT,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_COMPONENT24, GL_DEPTH_TEST,
                       GL_NEAREST, GL_R32F, GL_RED, GL_TEXTURE_2D, GL_TRIANGLES,
                       glClear, glClearColor, glEnable, glViewport)
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
from world3d.core.geometry import box_data  # noqa: E402
from world3d.engine.mathkit import (mat4_from_Mat4, mat4_inverse, mat4_mul)  # noqa: E402
from world3d.engine.motionblur import mb_glsl  # noqa: E402
from world3d.engine.targets import RenderTarget, bind_texture  # noqa: E402
from world3d.logic.camera import OrbitCamera  # noqa: E402

WINDOW_BLOCK = '''uniform WindowBlock {
    mat4 projection;
    mat4 view;
} window;
'''

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
uniform vec3 u_light_dir;
void main() {
    vec3 n = normalize(cross(dFdx(v_world), dFdy(v_world)));
    float ndl = max(dot(n, u_light_dir), 0.0);
    float amb = 0.36 + 0.14 * max(n.y, 0.0);
    // 棋盘地面：运动模糊的"拖影"在细纹理上最显眼（纯色面上一糊反而看不出来）
    vec3 col = v_col * (amb + 0.9 * ndl);
    if (v_world.y < 0.35) {          // 地面（y≈0）—— 用高度判，别拿颜色判
                                     // （`box_data` 会按面法线缩放亮度，按颜色判会判不中）
        float c = mod(floor(v_world.x) + floor(v_world.z), 2.0);
        col *= (0.72 + 0.28 * c);
    }
    final_colors = vec4(col, 1.0);
}
'''

DEPTH_VS = '#version 330 core\n' + WINDOW_BLOCK + '''
in vec3 position;
uniform mat4 u_model;
uniform vec3 u_cam;
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

COMPOSITE_VS = '''#version 330 core
in vec2 position;
in vec2 uv;
out vec2 v_uv;
void main() {
    v_uv = uv;
    gl_Position = vec4(position, 0.0, 1.0);
}
'''

COMPOSITE_FS_BODY = '''
in vec2 v_uv;
out vec4 final_colors;
uniform float u_exposure;
uniform float u_show_mv;      // 1 = 把运动矢量画出来

vec3 tonemap(vec3 c) {
    c *= u_exposure;
    return pow(c / (1.0 + c), vec3(1.0 / 2.2));
}

void main() {
    vec3 col = mb_apply(v_uv);
    if (u_show_mv > 0.5) {
        float z = texture(u_mb_depth, v_uv).r;
        vec2 prev = mb_apply_prev_uv(v_uv, z);
        vec2 mv = (prev - v_uv) * 6.0;          // 放大 6 倍才看得清
        col = vec3(clamp(abs(mv.x), 0.0, 1.0), clamp(abs(mv.y), 0.0, 1.0),
                   clamp(length(mv), 0.0, 1.0) * 0.35);
    }
    final_colors = vec4(tonemap(col), 1.0);
}
'''

GROUND = (0.20, 0.55, 0.22, 1.0)          # 绿色 ⇒ 着色器会给它加棋盘（拖影更容易看）
PILLAR_A = (0.80, 0.44, 0.30, 1.0)
PILLAR_B = (0.34, 0.52, 0.84, 1.0)


def _scene():
    out = [((400.0, 0.4, 400.0), (0.0, -0.2, -40.0), GROUND)]
    for i in range(16):
        z = -6.0 - i * 7.0
        t = i / 15.0
        col = tuple(PILLAR_A[k] * (1 - t) + PILLAR_B[k] * t for k in range(4))
        out.append(((2.0, 6.0, 2.0), (2.2 if i % 2 == 0 else -2.2, 3.0, z), col))
    for i in range(3):
        out.append(((3.0, 12.0, 3.0), (-16.0 + i * 16.0, 6.0, -150.0), (0.5, 0.5, 0.55, 1.0)))
    return out


class MotionBlurApp(DemoApp):
    TITLE = '运动模糊 —— 相机重投影（近处扫得远、远处几乎不动）'
    HELP = ('1 平移  2 原地转  3 推近  4 静止   ` 快门   N 采样   M 模糊   V 看运动矢量   '
            '拖动/滚轮 看视角   SPACE 暂停   ESC 退出')
    DURATION = None            # 相机运动由模式驱动，不按时钟循环
    HUD = True
    # 两个时刻都取「平移速度接近最大」的相位（1.1·t ≈ kπ），方向相反 ⇒ 两张必然不同
    # 两个时刻都取「平移速度接近最大」的相位（3.2·t ≈ kπ），方向相反 ⇒ 两张必然不同
    SHOTS = ((2.0, 'docs/mblur_01.png'),
             (3.94, 'docs/mblur_02.png'))

    MODES = ('pan', 'turn', 'dolly', 'stop')

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        self.prog_scene = ShaderProgram(Shader(SCENE_VS, 'vertex'),
                                       Shader(SCENE_FS, 'fragment'))
        self.prog_depth = ShaderProgram(Shader(DEPTH_VS, 'vertex'),
                                       Shader(DEPTH_FS, 'fragment'))
        self.prog_comp = ShaderProgram(
            Shader(COMPOSITE_VS, 'vertex'),
            Shader('#version 330 core\n' + mb_glsl(16) + COMPOSITE_FS_BODY, 'fragment'))

        self.cam = OrbitCamera(target=Vec3(0, 2.6, -40), yaw=90.0, pitch=4.0,
                               dist=12.0, pitch_range=(2.0, 30.0),
                               dist_range=(6.0, 60.0))
        self.cam.fov = 45.0
        self.cam.near = 0.2
        self.cam.far = 600.0

        self.mb_on = 1.0
        self.show_mv = 0.0
        self.shutter = 0.5                 # 180° 快门（摄影里最常见）
        self.samples = 16.0
        self.max_len = 60.0
        self.exposure = 1.0
        self.light = (0.5, 0.7, 0.4)
        self.mode_i = 0 if 'stop' not in self.args.flags else 3

        self.items = []
        for (dims, pos, col) in _scene():
            v, c = box_data(dims[0], dims[1], dims[2], 0, 0, 0, col[:3])
            self.items.append((
                self.prog_scene.vertex_list(len(v) // 3, GL_TRIANGLES,
                                            position=('f', v), colors=('f', c)),
                self.prog_depth.vertex_list(len(v) // 3, GL_TRIANGLES,
                                            position=('f', v)),
                Mat4.from_translation(Vec3(*pos))))

        self._build_targets(self.window.width, self.window.height)
        quad_pos = (-1.0, -1.0, 3.0, -1.0, -1.0, 3.0)
        quad_uv = (0.0, 0.0, 2.0, 0.0, 0.0, 2.0)      # ⚠ uv = ndc*0.5+0.5
        self.quad = self.prog_comp.vertex_list(3, GL_TRIANGLES,
                                              position=('f', quad_pos),
                                              uv=('f', quad_uv))

        # 上一帧的 view-projection（重投影要用）。首帧先等于当前帧 ⇒ 运动为 0。
        self.prev_vp = self._vp()
        self.pan_t = 0.0
        self.turn_t = 0.0
        self.dolly_t = 0.0
        orbit_camera_input(self, self.cam)

    # ------------------------------------------------------------ 资源 --
    def _make_r32(self, w, h):
        tex = Texture.create(w, h, GL_TEXTURE_2D, GL_R32F, GL_NEAREST, GL_NEAREST,
                             GL_RED)
        fbo = Framebuffer()
        fbo.attach_texture(tex, attachment=GL_COLOR_ATTACHMENT0)
        fbo.attach_renderbuffer(Renderbuffer(w, h, GL_DEPTH_COMPONENT24),
                                attachment=GL_DEPTH_ATTACHMENT)
        if not bool(fbo.is_complete):
            print(f'[mblur] WARNING: 距离 FBO 不完整 -> {fbo.get_status()}', flush=True)
        return tex, fbo

    def _build_targets(self, w, h):
        self.scene_rt = RenderTarget(max(1, w), max(1, h), color_count=1, depth=True)
        self.dist_tex, self.dist_fbo = self._make_r32(max(1, w), max(1, h))
        self._rt_size = (w, h)

    def _vp(self):
        aspect = self.window.width / max(1, self.window.height)
        return mat4_mul(tuple(self.cam.projection(aspect)),
                        mat4_from_Mat4(self.cam.view_matrix()))

    # ------------------------------------------------------------ 相机运动 --
    def _advance_camera(self, dt):
        """四种相机运动。**平移与原地转的区别就是本 demo 要展示的核心**：

        * `pan`   —— 目标点横移（朝向不变）⇒ **纯平移** ⇒ 只有近处糊；
        * `turn`  —— **位置钉死、只改朝向** ⇒ **纯旋转** ⇒ 整幅一起糊（远处也糊）；
        * `dolly` —— 沿视线推近 ⇒ 也是纯平移（只是沿 z）；
        * `stop`  —— 不动 ⇒ 运动矢量恒为 0（模糊**自动**消失，没有额外的静止检测）。
        """
        mode = self.MODES[self.mode_i]
        if mode == 'pan':
            self.pan_t += dt
            # 平移要够快：运动模糊的量 ∝ 速度 × 单帧时长，慢吞吞地平移看不出拖影
            off = 5.0 * math.sin(self.pan_t * 3.2)
            self.cam.target = Vec3(off, 2.6, -40)
        elif mode == 'turn':
            self.turn_t += dt
            # 位置钉死：先记住当前 eye，再把 target 摆到"以 eye 为中心转 θ"的位置上
            eye = self.cam.eye
            yaw = 90.0 + 12.0 * math.sin(self.turn_t * 0.9)
            self.cam.yaw = yaw
            self.cam.target = self._target_for_fixed_eye(eye)
        elif mode == 'dolly':
            self.dolly_t += dt
            self.cam.dist = 12.0 + 2.2 * math.sin(self.dolly_t * 1.3)

    def _target_for_fixed_eye(self, eye):
        """给定"想要的朝向"求 target，使得 eye 回到原来的位置（真正的原地转）。"""
        p, y = math.radians(self.cam.pitch), math.radians(self.cam.yaw)
        d = self.cam.dist
        off = (d * math.cos(p) * math.cos(y), d * math.sin(p), d * math.cos(p) * math.sin(y))
        return Vec3(eye.x - off[0], eye.y - off[1], eye.z - off[2])

    # ------------------------------------------------------------ 键盘 --
    def on_key(self, symbol, modifiers):
        if symbol in (key._1, key.NUM_1):
            self.mode_i = 0
        elif symbol in (key._2, key.NUM_2):
            self.mode_i = 1
        elif symbol in (key._3, key.NUM_3):
            self.mode_i = 2
        elif symbol in (key._4, key.NUM_4):
            self.mode_i = 3
        elif symbol == key.GRAVE:
            self.shutter = 1.0 if self.shutter > 0.85 else self.shutter + 0.25
            print(f'[mblur] 快门 {self.shutter * 360:.0f}°', flush=True)
        elif symbol == key.N:
            self.samples = 4.0 if self.samples >= 16.0 else self.samples * 2.0
        elif symbol == key.M:
            self.mb_on = 0.0 if self.mb_on > 0.5 else 1.0
        elif symbol == key.V:
            self.show_mv = 0.0 if self.show_mv > 0.5 else 1.0
        else:
            super().on_key(symbol, modifiers)

    # ------------------------------------------------------------ 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        if (w, h) != self._rt_size:
            self.scene_rt.delete()
            self.dist_fbo.delete()
            self.dist_tex.delete()
            self._build_targets(w, h)

        aspect = w / max(1, h)
        self.window.view = self.cam.view_matrix()
        self.window.projection = self.cam.projection(aspect)

        # ---- 深度趟 ----
        self.dist_fbo.bind()
        glViewport(0, 0, w, h)
        glClearColor(1e6, 1e6, 1e6, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog_depth.use()
        self.prog_depth['u_cam'] = self.cam.eye
        for (_m, depth_vl, model) in self.items:
            self.prog_depth['u_model'] = model
            depth_vl.draw(GL_TRIANGLES)
        self.dist_fbo.unbind()

        # ---- 场景趟 ----
        self.scene_rt.bind()
        glViewport(0, 0, w, h)
        glClearColor(0.04, 0.05, 0.07, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog_scene.use()
        self.prog_scene['u_light_dir'] = self.light
        for (main_vl, _d, model) in self.items:
            self.prog_scene['u_model'] = model
            main_vl.draw(GL_TRIANGLES)
        self.scene_rt.unbind()

        # ---- 合成趟：运动模糊 + tonemap ----
        glViewport(0, 0, w, h)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.prog_comp
        prog.use()
        bind_texture(0, self.dist_tex)
        prog['u_mb_depth'] = 0
        bind_texture(1, self.scene_rt.color[0])
        prog['u_mb_color'] = 1
        prog['u_mb_inv_vp_cur'] = list(mat4_inverse(self._vp()))
        prog['u_mb_vp_prev'] = list(self.prev_vp)
        prog['u_mb_cam'] = (self.cam.eye.x, self.cam.eye.y, self.cam.eye.z)
        prog['u_mb_shutter'] = self.shutter
        prog['u_mb_samples'] = self.samples
        prog['u_mb_max_len'] = self.max_len
        prog['u_mb_on'] = self.mb_on
        prog['u_exposure'] = self.exposure
        prog['u_show_mv'] = self.show_mv
        self.quad.draw(GL_TRIANGLES)

        # 本帧结束：把当前 vp 存成"上一帧"（下一帧的重投影基准）
        self.prev_vp = self._vp()

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        self._advance_camera(min(dt, 0.05))     # 掉帧时别让相机瞬移（否则拖影会断）

    # ------------------------------------------------------------ HUD --
    def status_text(self):
        mode = self.MODES[self.mode_i]
        label = {'pan': '平移(近处糊)', 'turn': '原地转(全糊)',
                 'dolly': '推近', 'stop': '静止(不糊)'}[mode]
        return (f'相机 {label}  快门 {self.shutter * 360:.0f}°  采样 {self.samples:.0f}  '
                f'长度上限 {self.max_len:.0f}px  '
                f'{"[模糊]" if self.mb_on > 0.5 else "[清晰]"}'
                f'{"[看矢量]" if self.show_mv > 0.5 else ""}  '
                f't={self.time:4.1f}s {self.fps:4.1f}fps')


if __name__ == '__main__':
    run(MotionBlurApp)

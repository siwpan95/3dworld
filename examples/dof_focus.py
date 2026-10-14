# -*- coding: utf-8 -*-
"""景深（DoF）—— ④-3：弥散圆 / 超焦距 / 散景。

**为什么要有独立的 demo**：景深不是"高斯模糊背景"，而是**每一点按弥散圆大小摊成圆盘**。
本 demo 的场景刻意做成"沿视线排开"：一排方块从 8 米一直排到 120 米，另有几个贴近相机的
浮空球 —— 于是**对焦距离一动，清晰的那一段（景深）就在画面里扫过去**，一眼看出：

* 焦点前后的虚化**不对称**（近侧涨得快、远侧很快到达渐近值）；
* 光圈越大景深越浅（按 `,`/`.` 换 f 值）；
* 散景形状随叶片数变（按 `B` 在圆形/5/6/8 叶之间切，亮点会变成多边形）。

数学与 GLSL 全在 `engine/dof.py`（纯函数、可 headless 断言：CoC / 超焦距 / 景深范围 /
散景盘面积均匀 / 前景剔除）。本文件的活是把三趟渲染串起来：
**深度趟（线性距离）→ 场景趟（HDR 色）→ 合成趟（DoF + tonemap）**。

用法::

    python examples/dof_focus.py
    python examples/dof_focus.py --flag rack        # 起手就自动扫焦（适合看成片）

按键：`[` `]` 对焦距离   `,` `.` 光圈(f 值)   B 散景叶片   D 景深开关
      V 看弥散圆   A 自动扫焦   SPACE 暂停   拖动/滚轮 看视角   ESC 退出
"""
import math
import os
import sys

from pyglet.gl import (GL_COLOR_ATTACHMENT0, GL_COLOR_BUFFER_BIT, GL_DEPTH_ATTACHMENT,
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_COMPONENT24, GL_DEPTH_TEST,
                       GL_NEAREST, GL_R32F, GL_RED, GL_TEXTURE_2D, GL_TRIANGLES,
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
from engine.dof import circle_of_confusion, dof_glsl  # noqa: E402
from engine.targets import RenderTarget, bind_texture  # noqa: E402
from logic.camera import OrbitCamera  # noqa: E402

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
    final_colors = vec4(v_col * (amb + 0.9 * ndl), 1.0);
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
uniform float u_show_coc;      // 1 = 直接把弥散圆画出来看

vec3 tonemap(vec3 c) {
    c *= u_exposure;
    return pow(c / (1.0 + c), vec3(1.0 / 2.2));
}

void main() {
    vec3 col = dof_apply(v_uv);
    if (u_show_coc > 0.5) {
        float z = texture(u_dof_depth, v_uv).r;
        float coc = abs(dof_apply_coc(z)) / max(1.0, u_dof_max_coc);
        col = mix(col, vec3(1.0, 0.35, 0.15), clamp(coc * 3.0, 0.0, 0.85));
    }
    final_colors = vec4(tonemap(col), 1.0);
}
'''

GROUND = (0.26, 0.27, 0.30, 1.0)
NEAR_COL = (0.86, 0.42, 0.30, 1.0)
FAR_COL = (0.36, 0.52, 0.82, 1.0)
BALL_COL = (0.90, 0.82, 0.40, 1.0)


def _scene():
    """(尺寸, 世界位置, 颜色) 清单 —— 全部确定性，剧照可复现。"""
    out = [((400.0, 0.4, 400.0), (0.0, -0.2, -40.0), GROUND)]
    # 沿视线排开的一列方块：从 8m 到 120m（每 8m 一个，交错左右）
    for i in range(15):
        z = -8.0 - i * 8.0
        t = i / 14.0
        col = tuple(NEAR_COL[k] * (1 - t) + FAR_COL[k] * t for k in range(4))
        x = 1.9 if i % 2 == 0 else -1.9
        h = 2.0 + 0.9 * math.sin(i * 1.9)
        out.append(((2.2, h, 2.2), (x, h / 2.0, z), col))
    # 几个贴近相机的浮空球：前景虚化最明显（近侧 CoC 涨得比远侧快）
    for (x, y, z, s) in ((1.9, 1.5, -2.4, 0.9), (-2.1, 2.6, -1.6, 0.7),
                         (0.0, 3.4, -3.2, 1.1)):
        out.append(((s, s, s), (x, y, z), BALL_COL))
    # 远处几个高柱，给"背景到底虚到什么程度"一个参照
    for i in range(3):
        out.append(((2.0, 14.0, 2.0), (-14.0 + i * 14.0, 7.0, -130.0 - i * 10.0),
                    (0.55, 0.58, 0.64, 1.0)))
    return out


class DofFocusApp(DemoApp):
    TITLE = '景深（DoF）—— 弥散圆 / 超焦距 / 散景'
    HELP = ('[ ] 对焦距离   , . 光圈(f 值)   B 散景叶片   D 景深   V 看弥散圆   '
            'A 自动扫焦   拖动/滚轮 看视角   SPACE 暂停   ESC 退出')
    DURATION = 24.0                # 自动扫焦一圈（对焦 3m ⇄ 90m），循环无缝
    HUD = True
    SHOTS = ((4.0, 'docs/dof_01.png'),    # 对焦在近处
             (14.0, 'docs/dof_02.png'))   # 对焦已扫到远处

    #: 传感器与镜头（物理量都换算到"世界单位 = 米"）
    SENSOR_HEIGHT = 0.024          # 24mm 全画幅
    # 85mm 中长焦：真实摄影里「背景糊成一片」靠的就是它。50mm f/5.6 时远场 CoC 只有
    # ~1.4px（**物理上就该这么小**），画面几乎看不出虚化，不适合当演示；
    # 85mm f/2.8 的远场 CoC ≈ 8px，正好"看得清虚化、又不至于糊成一团"。
    FOCAL = 0.085
    FOCAL_PIXELS_SCALE = 900.0     # "世界单位 → 像素"的经验系数（把 CoC 折成像素）

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        self.prog_scene = ShaderProgram(Shader(SCENE_VS, 'vertex'),
                                       Shader(SCENE_FS, 'fragment'))
        self.prog_depth = ShaderProgram(Shader(DEPTH_VS, 'vertex'),
                                       Shader(DEPTH_FS, 'fragment'))
        self.prog_comp = ShaderProgram(
            Shader(COMPOSITE_VS, 'vertex'),
            Shader('#version 330 core\n' + dof_glsl(32) + COMPOSITE_FS_BODY,
                   'fragment'))

        # 相机：正对那一列物体，低机位、小俯角（像在看一条走廊）
        self.cam = OrbitCamera(target=Vec3(0, 1.2, -46), yaw=90.0, pitch=8.0,
                               dist=30.0, pitch_range=(2.0, 34.0),
                               dist_range=(8.0, 110.0))
        self.cam.fov = 45.0
        self.cam.near = 0.2
        self.cam.far = 500.0

        # ---- 可调参数 ----
        self.dof_on = 1.0
        self.show_coc = 0.0
        self.rack = 'rack' in self.args.flags
        self.focus = 8.0               # 对焦距离（米）
        # 默认 f/5.6：85mm + f/2 那套是「人像浅景深」，会让整幅画面糊掉没法看；
        # 用标头 + f/5.6 起手，按 `.` 逐步开大光圈就能看到景深变浅的整个过程
        self.f_number = 2.8
        self.blades = 0                # 0 = 圆形
        self.max_coc = 26.0            # 弥散圆上限（像素）
        self.exposure = 1.0
        self.light = (0.55, 0.75, 0.35)

        # ---- 几何 ----
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
        # 全屏三角：⚠ uv 必须 `ndc*0.5+0.5`（直接当 NDC 用会让画面四周平铺/镜像）
        quad_pos = (-1.0, -1.0, 3.0, -1.0, -1.0, 3.0)
        quad_uv = (0.0, 0.0, 2.0, 0.0, 0.0, 2.0)
        self.quad = self.prog_comp.vertex_list(3, GL_TRIANGLES,
                                              position=('f', quad_pos),
                                              uv=('f', quad_uv))
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
            print(f'[dof] WARNING: 距离 FBO 不完整 -> {fbo.get_status()}', flush=True)
        return tex, fbo

    def _build_targets(self, w, h):
        self.scene_rt = RenderTarget(max(1, w), max(1, h), color_count=1, depth=True)
        self.dist_tex, self.dist_fbo = self._make_r32(max(1, w), max(1, h))
        self._rt_size = (w, h)

    # ------------------------------------------------------------ 参数 --
    def aperture_world(self):
        """光圈**直径**（世界单位 = 米）：``f / N``。"""
        return self.FOCAL / self.f_number

    def coc_pixels_at(self, z):
        """把 `engine.dof` 的 CoC 折成像素（与着色器里的 `u_dof_coc_scale` 同一套换算）。"""
        mm = circle_of_confusion(max(z, 1e-4), self.focus, self.aperture_world(),
                                 self.FOCAL)
        return mm / self.SENSOR_HEIGHT * self.FOCAL_PIXELS_SCALE

    # ------------------------------------------------------------ 键盘 --
    def on_key(self, symbol, modifiers):
        if symbol == key.BRACKETRIGHT:
            self.focus = min(200.0, self.focus * 1.25)
        elif symbol == key.BRACKETLEFT:
            self.focus = max(1.5, self.focus / 1.25)
        elif symbol == key.PERIOD:
            self.f_number = min(22.0, self.f_number * 1.4)
        elif symbol == key.COMMA:
            self.f_number = max(1.2, self.f_number / 1.4)
        elif symbol == key.B:
            self.blades = {0: 5, 5: 6, 6: 8, 8: 0}[self.blades]
            self._rebuild_composite()
        elif symbol == key.D:
            self.dof_on = 0.0 if self.dof_on > 0.5 else 1.0
        elif symbol == key.V:
            self.show_coc = 0.0 if self.show_coc > 0.5 else 1.0
        elif symbol == key.A:
            self.rack = not self.rack
        else:
            super().on_key(symbol, modifiers)

    def _rebuild_composite(self):
        """换散景叶片要重编译着色器（采样盘是**烘进常量**的）。"""
        self.prog_comp = ShaderProgram(
            Shader(COMPOSITE_VS, 'vertex'),
            Shader('#version 330 core\n' + dof_glsl(32, blades=self.blades)
                   + COMPOSITE_FS_BODY, 'fragment'))
        self.quad = self.prog_comp.vertex_list(
            3, GL_TRIANGLES,
            position=('f', (-1.0, -1.0, 3.0, -1.0, -1.0, 3.0)),
            uv=('f', (0.0, 0.0, 2.0, 0.0, 0.0, 2.0)))
        print(f'[dof] 散景叶片 = {self.blades or "圆形"}', flush=True)

    # ------------------------------------------------------------ 渲染 --
    def render(self):
        w, h = self.window.width, self.window.height
        if (w, h) != self._rt_size:
            self.scene_rt.delete()
            self.dist_fbo.delete()
            self.dist_tex.delete()
            self._build_targets(w, h)

        if self.rack:                                # 自动扫焦：3m ⇄ 90m 循环
            t = 0.5 - 0.5 * math.cos(2 * math.pi * self.time / self.DURATION)
            self.focus = 3.0 + t * 87.0

        aspect = w / max(1, h)
        self.window.view = self.cam.view_matrix()
        self.window.projection = self.cam.projection(aspect)

        # ---- 深度趟：相机视角的**线性距离**（景深要的就是它）----
        self.dist_fbo.bind()
        glViewport(0, 0, w, h)
        glClearColor(1e6, 1e6, 1e6, 1.0)             # 背景 = 极远
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog_depth.use()
        self.prog_depth['u_cam'] = self.cam.eye
        for (_main, depth_vl, model) in self.items:
            self.prog_depth['u_model'] = model
            depth_vl.draw(GL_TRIANGLES)
        self.dist_fbo.unbind()

        # ---- 场景趟：HDR 颜色 ----
        self.scene_rt.bind()
        glViewport(0, 0, w, h)
        glClearColor(0.03, 0.035, 0.05, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        self.prog_scene.use()
        self.prog_scene['u_light_dir'] = self.light
        for (main_vl, _depth, model) in self.items:
            self.prog_scene['u_model'] = model
            main_vl.draw(GL_TRIANGLES)
        self.scene_rt.unbind()

        # ---- 合成趟：DoF + tonemap ----
        glViewport(0, 0, w, h)
        glClearColor(0.0, 0.0, 0.0, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        prog = self.prog_comp
        prog.use()
        bind_texture(0, self.dist_tex)
        prog['u_dof_depth'] = 0
        bind_texture(1, self.scene_rt.color[0])
        prog['u_dof_color'] = 1
        prog['u_dof_focus'] = self.focus
        prog['u_dof_aperture'] = self.aperture_world()
        prog['u_dof_focal'] = self.FOCAL
        prog['u_dof_max_coc'] = self.max_coc
        # "世界单位 → 像素"：CoC(米) / 传感器高 × 图像高
        prog['u_dof_coc_scale'] = h / self.SENSOR_HEIGHT
        prog['u_dof_on'] = self.dof_on
        prog['u_exposure'] = self.exposure
        prog['u_show_coc'] = self.show_coc
        self.quad.draw(GL_TRIANGLES)

    # ------------------------------------------------------------ HUD --
    def status_text(self):
        zs = (1.0, 3.0, 8.0, 20.0, 60.0, 200.0)
        coc = ' '.join(f'{z:.0f}m:{self.coc_pixels_at(z):+5.1f}' for z in zs)
        return (f'对焦 {self.focus:5.1f}m  f/{self.f_number:<4.1f} '
                f'散景 {"圆" if not self.blades else str(self.blades) + "叶"}  '
                f'CoC(px) {coc}  '
                f'{"[景深]" if self.dof_on > 0.5 else "[全清晰]"}'
                f'{"[看 CoC]" if self.show_coc > 0.5 else ""}'
                f'{"[扫焦]" if self.rack else ""}  t={self.time:4.1f}s {self.fps:4.1f}fps')


if __name__ == '__main__':
    run(DofFocusApp)

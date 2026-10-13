# -*- coding: utf-8 -*-
"""Boids 3D 鸟群 —— **原生实例化渲染**（pyglet 2.1.16 ``vertex_list_instanced``）。

结构照抄 ``demos/demoscene_tunnel.py`` 的骨架（``platform.app.DemoApp``），
逻辑层用 ``logic.boids.Flock``（纯数学、无 GL），本文件只负责"把状态画出来"。

## 为什么要用实例化

340 只鸟若各自一个 ``vertex_list``，每帧就是 340 次 draw call + 340 份重复的
三角形顶点数据；实例化把**基础几何只上传一次**，位置/速度变成逐实例属性，
一次 ``glDrawArraysInstanced`` 画完全部。

**朝向在顶点着色器里算**：实例属性只传 ``i_pos`` / ``i_vel``（6 个 float/只），
由 ``vel`` 现场构造正交基 ``(right, up, forward)`` 把鸟的局部三角摆到航向上。
若改成 CPU 端算 340 个旋转矩阵再传进去，每帧要传 340×16 个 float —— 着色器里
一次 ``cross`` 就够的事，没必要占带宽。

## pyglet 2.1.16 实测坑位（都是踩过才写下来的）

1. ``vertex_list_instanced`` 的实例属性**必须给初始数据**（``i_pos=('f', [0]*9)``）。
   漏了不会报错：``_vertex_list_create`` 里那个 ``except KeyError: continue``
   会把该属性**静默丢掉**，于是它 ``instance=False``，随后
   ``instances[k].i_pos = ...`` 抛 ``Attribute 'i_pos' is not an instanced attribute``。
   初始数据长度按**基础几何顶点数**算（3 顶点 × vec3 = 9 个 float），不是实例数。
2. 域初始 ``_instances = 1``（第 0 号是一个**占位实例**，不是"已经加好的第一只鸟"），
   ``add_instance()`` 每次 +1。所以 N 只鸟要调 **N 次** ``add_instance``，
   返回的 ``VertexInstance`` 逐帧可写。少调一次就会有一只鸟停在原点不动。
3. 传了 ``position`` / ``colors`` 就必须在着色器里声明同名 ``in``；``colors``
   即使不用也得"用一下"（这里乘进颜色输出），否则会被 GLSL 优化掉 → ShaderException。
4. 实例属性在着色器里就是普通 ``in vec3 i_pos;``，divisor 由 pyglet 自动设。
5. ``DemoApp`` 只代管 ``on_mouse_drag`` / ``on_mouse_scroll``，
   ``on_mouse_press`` 要自己 ``push_handlers`` 挂上，否则点击永远收不到。

## 几何：一只"鸟"

3 个三角形的三角翼（机身 + 左右后掠翼），放在局部 XY 平面、朝 +Z 飞，
尺寸约 0.62 × 0.5。按最近邻距离 ~2.1（实测）来定，鸟与鸟不会糊在一起。

## 交互

- **左键点击**：把捕食者放到点击射线与 y=0 平面的交点（鸟群炸开）
- ``P``：清除捕食者　``1/2/3``：紧密群集 / 松散游弋 / 高速冲刺
- ``T``：速度尾迹　``R``：重新撒点（同一 seed，结果可复现）
- 鼠标拖动/滚轮：轨道相机

## 性能（实测 1280x720 / N=340 / vsync 开启）

- 整机帧率：**均值 59.7 fps、中位 59.8 fps、p5 57.1 fps、最慢单帧 19.1 ms**
  （丢帧率为 0%），HUD 上的 step 读数是实时的逻辑层耗时
- ``Flock.step``：340 只约 9.5 ms/步（tight 档；loose 8.8 / sprint 9.5）
- 实例属性写入：340 只约 0.16 ms/帧（逐条赋值，实测已足够，未动用 ctypes 批量路径）
"""
import math
import time

import pyglet
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_LINES, GL_ONE_MINUS_SRC_ALPHA,
                       GL_SRC_ALPHA, GL_TRIANGLES, glBlendFunc, glDisable,
                       glEnable, glLineWidth)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Vec3

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, orbit_camera_input, run
from logic.boids import DOMAIN, Flock, PRESET_LABELS
from logic.camera import OrbitCamera

N_BOIDS = 340                 # 300~500 区间；340 时 step 约 9.5 ms，留出渲染余量
CAM_TARGET = Vec3(0.0, 0.0, 0.0)

# ------------------------------------------------------------------ 着色器 --
# 顶点着色器：由 i_vel 现场构造正交基，把局部三角旋到航向 + 平移。
# 注意 cross 的退化情况（速度与参考上向量平行）：加一个极小的扰动项，
# 避免 normalize(0) 产生 NaN 顶点（NaN 三角形的表现是"整只鸟消失"）。
BIRD_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
in vec3 i_pos;
in vec3 i_vel;
out vec4 v_col;
out float v_speed;
uniform WindowBlock { mat4 projection; mat4 view; } window;

void main() {
    vec3 fwd = normalize(i_vel);
    vec3 ref = vec3(0.0, 1.0, 0.0) + fwd * 0.001;
    vec3 right = normalize(cross(ref, fwd));
    vec3 up = cross(fwd, right);
    mat3 basis = mat3(right, up, fwd);       // 列 = 基向量
    vec3 world = i_pos + basis * position;
    v_col = colors;
    v_speed = length(i_vel);
    gl_Position = window.projection * window.view * vec4(world, 1.0);
}'''

BIRD_FRAG = '''#version 330 core
in vec4 v_col;
in float v_speed;
out vec4 final_colors;
void main() {
    // 速度映射到冷/暖色。**基准值 4.5 是关键**：实测这一群的速率分布很"抱团"
    // （单帧内 p10~p90 只差约 1.5 倍），但整群会以约 15 s 周期在"慢速巡游(~1)
    // 与高速冲刺(~10)"之间集体呼吸。若按绝对速度除以 14 上色，整群会在慢相位
    // 全蓝、快相位全橙 —— 看着像两群不同的鸟（第一版截图正是如此）。
    // 除以基准 4.5 后，同一帧内颜色几乎一致，颜色变化只表达"整群此刻快不快"。
    float k = clamp(v_speed / 4.5, 0.25, 1.6);
    vec3 tint = mix(vec3(0.62, 0.80, 1.00), vec3(1.00, 0.80, 0.50),
                    clamp((k - 0.35) / 1.0, 0.0, 1.0));
    final_colors = vec4(v_col.rgb * tint, 1.0);
}'''

# 尾迹：一条线段/只（尾端 = 上一帧位置，头端 = 当前位置）。
# **这里刻意不用实例化**：实例属性只适合"每只一个值"的数据，而线段两端是
# 两个不同的位置 —— 硬塞进实例化就要再开一个 gl_VertexID 分支去索引两个
# 不同的实例属性，反而更绕。N 条线段的顶点数据一次性 set_attribute_data
# 上传（340 只 = 2040 个 float），成本可忽略。
TAIL_VERT = '''#version 330 core
in vec3 position;
in float alpha;
out float v_alpha;
uniform WindowBlock { mat4 projection; mat4 view; } window;
void main() {
    v_alpha = alpha;
    gl_Position = window.projection * window.view * vec4(position, 1.0);
}'''

TAIL_FRAG = '''#version 330 core
in float v_alpha;
out vec4 final_colors;
void main() {
    final_colors = vec4(0.45, 0.78, 1.0, v_alpha);
}'''

SOLID_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 v_col;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform vec3 u_offset;
void main() {
    v_col = colors;
    gl_Position = window.projection * window.view * vec4(position + u_offset, 1.0);
}'''

SOLID_FRAG = '''#version 330 core
in vec4 v_col;
out vec4 final_colors;
void main() { final_colors = v_col; }'''

# 背景渐变：一个 NDC 全屏四边形，关掉深度写入、画在远平面之后。
# 纯黑背景会让"薄片鸟"缺少参照、看着像贴纸；一层很暗的竖向渐变能撑出空间感，
# 又不至于喧宾夺主（亮度上限 0.10）。
BG_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
out float v_t;
void main() {
    v_t = position.y * 0.5 + 0.5;
    gl_Position = vec4(position.xy, 1.0, 1.0);   // z=w ⇒ NDC 深度 1.0（最远）
}'''

BG_FRAG = '''#version 330 core
in float v_t;
out vec4 final_colors;
void main() {
    // 亮度刻意压得很低（上限 ~0.20）：这是背景不是天空，
    // 目的是给"薄片鸟"一个明暗参照，不能把鸟的对比度吃掉
    vec3 bottom = vec3(0.020, 0.030, 0.058);
    vec3 top = vec3(0.105, 0.135, 0.225);
    final_colors = vec4(mix(bottom, top, v_t), 1.0);
}'''


def bird_geometry():
    """三角翼"鸟"：3 个三角形，局部朝 +Z 飞，落在 XY 平面（薄片）。"""
    verts, cols = [], []
    wing = (0.72, 0.86, 1.00)
    body = (1.00, 1.00, 1.00)

    def tri(a, b, c, rgb):
        for p in (a, b, c):
            verts.extend(p)
            cols.extend((rgb[0], rgb[1], rgb[2], 1.0))

    nose = (0.0, 0.0, 0.34)
    tail = (0.0, 0.0, -0.16)
    tri(nose, tail, (-0.30, 0.11, -0.30), wing)     # 左后掠翼
    tri(nose, tail, (0.30, 0.11, -0.30), wing)      # 右后掠翼
    tri(nose, tail, (0.0, 0.17, -0.34), body)       # 尾舵
    return verts, cols


def ring_geometry(radius=1.15, segments=40):
    """三个正交圆环（捕食者准星）：比实心球更省顶点，也更"准星"。"""
    verts, cols = [], []
    for axis in range(3):
        for k in range(segments):
            a0 = 2.0 * math.pi * k / segments
            a1 = 2.0 * math.pi * (k + 1) / segments
            for a in (a0, a1):
                ca, sa = math.cos(a), math.sin(a)
                if axis == 0:
                    verts.extend((0.0, radius * ca, radius * sa))
                elif axis == 1:
                    verts.extend((radius * ca, 0.0, radius * sa))
                else:
                    verts.extend((radius * ca, radius * sa, 0.0))
                cols.extend((1.0, 0.36, 0.26, 1.0))
    return verts, cols


class BoidsDemo(DemoApp):
    TITLE = 'logic.boids · 3D 鸟群（原生实例化 / 顶点着色器定向）'
    HELP = ('左键 放捕食者   P 清除   1/2/3 预设   T 尾迹   R 重撒   '
            '拖动/滚轮 相机   SPACE 暂停   ESC 退出')
    DURATION = 45.0
    FOV = 55.0
    Z_NEAR, Z_FAR = 0.5, 400.0
    # 抓帧时刻是实测挑的（跑满循环、逐秒记录角向分布后取值）：
    # 群体从随机朝向自组织到同向需要几秒，之后长期维持"整群像刚体一样同向平移"。
    SHOTS = ((6.0, 'docs/demo_boids_a.png'),
             (24.0, 'docs/demo_boids_b.png'))

    PRESET_ORDER = ('tight', 'loose', 'sprint')

    # ------------------------------------------------------------- setup --
    def setup(self):
        # 域参数取自 logic.boids.DOMAIN（bounds=9 / 邻域 3.0 / 格 3.0 / 撒点 4.0）。
        # **密度是观感的决定因素**，不是权重：同样 340 只鸟，域从 14 收到 9 之后
        # 角向分布中位数从 27.7° 降到 0.3°（实测），画面上才真是"一群鸟"。
        self.flock = Flock(count=N_BOIDS, seed=20261006, **DOMAIN)
        self.preset = 'tight'
        self.flock.set_preset(self.preset)
        self.tails_on = True
        self.pred_pos = None

        # 取景：密度调好后整群约占半径 6 的球（实测 RMS 半径 6.3），
        # dist=17 / FOV=55 时群铺满画面高度。第一版 dist=34 时群只有巴掌大，
        # 看着不像"一群鸟"（截图已证实）。
        self.camera = OrbitCamera(target=CAM_TARGET, yaw=34.0, pitch=20.0, dist=17.0)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(self, self.camera)
        self.window.push_handlers(on_mouse_press=self.on_mouse_press)   # 坑位 5

        self.bird_prog = ShaderProgram(Shader(BIRD_VERT, 'vertex'),
                                       Shader(BIRD_FRAG, 'fragment'))
        self.tail_prog = ShaderProgram(Shader(TAIL_VERT, 'vertex'),
                                       Shader(TAIL_FRAG, 'fragment'))
        self.solid_prog = ShaderProgram(Shader(SOLID_VERT, 'vertex'),
                                        Shader(SOLID_FRAG, 'fragment'))

        base_pos, base_col = bird_geometry()
        n_base_v = len(base_pos) // 3            # 9 个顶点（3 个三角形）
        # 坑位 1 的第二个细节：占位数组长度 = 顶点数 × vec3，即 n_base_v * 3。
        # 写成固定 9 会在顶点数变化时直接 ValueError（Expected 27, got 9）。
        zero_pad = [0.0] * (n_base_v * 3)
        self.vlist = self.bird_prog.vertex_list_instanced(
            n_base_v, GL_TRIANGLES,
            instance_attributes=['i_pos', 'i_vel'],
            position=('f', base_pos), colors=('f', base_col),
            i_pos=('f', zero_pad), i_vel=('f', zero_pad))
        xs, ys, zs = self.flock.positions()
        vx, vy, vz = self.flock.velocities()
        self.instances = []
        for i in range(N_BOIDS):        # N 次 add_instance（坑位 2）
            self.instances.append(self.vlist.add_instance(
                i_pos=(xs[i], ys[i], zs[i]), i_vel=(vx[i], vy[i], vz[i])))

        # 尾迹：N 条线段 × 2 顶点；position / alpha 逐帧整块 set_attribute_data
        n_tail_v = N_BOIDS * 2
        self.tail_vlist = self.tail_prog.vertex_list(
            n_tail_v, GL_LINES,
            position=('f', [0.0] * (n_tail_v * 3)),
            alpha=('f', [0.42] * n_tail_v))
        self._tail_pos = [0.0] * (n_tail_v * 3)
        # 尾端淡、头端亮：顶点 2i 是尾端（上一帧），2i+1 是头端（当前帧）
        tail_alpha = []
        for _ in range(N_BOIDS):
            tail_alpha.extend((0.05, 0.42))
        self.tail_vlist.set_attribute_data('alpha', tail_alpha)
        self.tail_prev = [(xs[i], ys[i], zs[i]) for i in range(N_BOIDS)]

        ring_pos, ring_col = ring_geometry()
        self.ring_vlist = self.solid_prog.vertex_list(
            len(ring_pos) // 3, GL_LINES,
            position=('f', ring_pos), colors=('f', ring_col))

        # 背景渐变四边形（NDC，2 个三角形）
        self.bg_prog = ShaderProgram(Shader(BG_VERT, 'vertex'),
                                     Shader(BG_FRAG, 'fragment'))
        bg_pos = (-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 1.0, 0.0,
                  -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, -1.0, 1.0, 0.0)
        self.bg_vlist = self.bg_prog.vertex_list(
            6, GL_TRIANGLES, position=('f', bg_pos), colors=('f', [1.0] * 24))

        self.step_ms = 0.0
        glEnable(GL_DEPTH_TEST)
        glDisable(GL_BLEND)
        glLineWidth(1.5)

    # ------------------------------------------------------------ update --
    def update(self, dt):
        t0 = time.perf_counter()
        self.flock.step(dt)
        self.step_ms = (time.perf_counter() - t0) * 1000.0

        xs, ys, zs = self.flock.positions()
        vx, vy, vz = self.flock.velocities()
        span = 2.0 * self.flock.bounds
        limit = span * 0.4
        for i in range(N_BOIDS):
            x, y, z = xs[i], ys[i], zs[i]
            self.instances[i].i_pos = (x, y, z)
            self.instances[i].i_vel = (vx[i], vy[i], vz[i])
            px, py, pz = self.tail_prev[i]
            # 回绕会把 (14 → -14) 当成一次瞬移：尾端直接贴到当前点，
            # 否则会看到一条横穿整个场景的假线
            if abs(x - px) > limit or abs(y - py) > limit or abs(z - pz) > limit:
                px, py, pz = x, y, z
            k = i * 6
            self._tail_pos[k] = px; self._tail_pos[k + 1] = py; self._tail_pos[k + 2] = pz
            self._tail_pos[k + 3] = x; self._tail_pos[k + 4] = y; self._tail_pos[k + 5] = z
            self.tail_prev[i] = (x, y, z)
        if self.tails_on:
            self.tail_vlist.set_attribute_data('position', self._tail_pos)

    # ------------------------------------------------------------ render --
    def render(self):
        # 背景：**必须关掉深度测试**。四边形的 NDC 深度是 1.0，而清屏后深度缓冲
        # 也是 1.0，默认 GL_LESS 判 (1.0 < 1.0) = false —— 背景会被整块丢掉，
        # 画面退回黑底（实测抓帧像素全是 (0,0,0)，glDepthMask(False) 救不了它）。
        glDisable(GL_DEPTH_TEST)
        self.bg_prog.use()
        self.bg_vlist.draw(GL_TRIANGLES)
        glEnable(GL_DEPTH_TEST)

        self.window.view = self.camera.view_matrix()

        if self.tails_on:
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            self.tail_prog.use()
            self.tail_vlist.draw(GL_LINES)
            glDisable(GL_BLEND)

        self.bird_prog.use()
        self.vlist.draw(GL_TRIANGLES)

        if self.pred_pos is not None:
            self.solid_prog.use()
            self.solid_prog['u_offset'] = self.pred_pos
            self.ring_vlist.draw(GL_LINES)

    # ------------------------------------------------------------ 交互 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.P:
            self.flock.set_predator(None)
            self.pred_pos = None
        elif symbol == key.T:
            self.tails_on = not self.tails_on
        elif symbol == key.R:
            self.flock.reset()
            xs, ys, zs = self.flock.positions()
            self.tail_prev = [(xs[i], ys[i], zs[i]) for i in range(N_BOIDS)]
            # 尾迹的**顶点缓冲**也要跟着清：只重置 tail_prev 的话，线段起点
            # 仍是上一帧的旧位置，重撒后第一帧会画出一片横穿场景的假线。
            # 直接把首尾两端都写成新位置 ⇒ 线段长度 0，等于"没有尾迹"。
            for i in range(N_BOIDS):
                k = i * 6
                self._tail_pos[k] = xs[i]
                self._tail_pos[k + 1] = ys[i]
                self._tail_pos[k + 2] = zs[i]
                self._tail_pos[k + 3] = xs[i]
                self._tail_pos[k + 4] = ys[i]
                self._tail_pos[k + 5] = zs[i]
            if self.tails_on:
                self.tail_vlist.set_attribute_data('position', self._tail_pos)
        elif symbol in (key._1, key._2, key._3):
            self.preset = self.PRESET_ORDER[symbol - key._1]
            self.flock.set_preset(self.preset)

    def on_mouse_press(self, x, y, buttons, modifiers):
        """左键：把捕食者放到点击射线与 y=0 平面的交点。

        射线由视图矩阵的**逆**反投影得到 —— 视图矩阵是刚体变换，
        逆 = 旋转转置 + 平移，纯算术即可，不必求一般 4×4 逆矩阵。
        """
        if not (buttons & pyglet.window.mouse.LEFT):
            return
        w, h = self.window.width, self.window.height
        ndc_x = 2.0 * x / max(1, w) - 1.0
        ndc_y = 2.0 * y / max(1, h) - 1.0
        aspect = w / float(max(1, h))
        tan_half = math.tan(math.radians(self.FOV) * 0.5)
        dx = ndc_x * tan_half * aspect      # 相机空间方向（-Z 为前方）
        dy = ndc_y * tan_half
        dz = -1.0
        m = self.camera.view_matrix()       # 世界 → 相机
        right = (m[0], m[4], m[8])          # 行的转置 = 世界空间的相机轴
        up = (m[1], m[5], m[9])
        fwd = (-m[2], -m[6], -m[10])        # 相机 -Z 在世界中的方向
        dirx = right[0] * dx + up[0] * dy + fwd[0] * (-dz)
        diry = right[1] * dx + up[1] * dy + fwd[1] * (-dz)
        dirz = right[2] * dx + up[2] * dy + fwd[2] * (-dz)
        ln = math.sqrt(dirx * dirx + diry * diry + dirz * dirz) or 1.0
        dirx, diry, dirz = dirx / ln, diry / ln, dirz / ln
        eye = self.camera.eye
        if abs(diry) < 1e-6:
            return
        t = -eye.y / diry                   # 与 y=0 平面求交
        if t <= 0.0:
            return
        b = self.flock.bounds
        # 夹进模拟域：域外的捕食者推不到任何鸟，点了会像"没反应"
        pos = Vec3(max(-b, min(b, eye.x + dirx * t)),
                   max(-b, min(b, eye.y + diry * t)),
                   max(-b, min(b, eye.z + dirz * t)))
        self.pred_pos = pos
        self.flock.set_predator(pos)

    # -------------------------------------------------------------- HUD --
    def status_text(self):
        pred = ('无' if self.pred_pos is None
                else '(%.1f,%.1f,%.1f)' % (self.pred_pos.x, self.pred_pos.y,
                                           self.pred_pos.z))
        return (f'{PRESET_LABELS[self.preset]}  boids={N_BOIDS}  '
                f'step={self.step_ms:5.2f}ms  尾迹={"开" if self.tails_on else "关"}  '
                f'捕食者={pred}   t={self.time:5.1f}s/{self.DURATION:.0f}s   '
                f'{self.fps:5.1f} fps{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(BoidsDemo)

# -*- coding: utf-8 -*-
"""GPU 实例化粒子：4.5 万颗粒子的逐帧动画 100% 在顶点着色器里算。

为什么这么写（本 demo 的重点）
------------------------------
CPU 每帧只做三件事：``prog['u_time'] = self.phase``（一个标量）、切预设、
画一次 ``draw``。粒子的位置/大小/颜色全部由顶点着色器从 ``i_seed``（每实例
随机种子）+ ``u_time`` 现推出来——所以粒子数翻倍几乎不影响 CPU 占用，也不存在
"每帧重建几万个顶点"的开销。

实例化用的是 pyglet 2.1.16 的**原生** API，不需要手搓 VAO/VBO::

    vlist = program.vertex_list_instanced(
        6, GL_TRIANGLES, instance_attributes=['i_seed', 'i_color'],
        position=('f', base_pos), colors=('f', base_col),
        i_seed=('f', seed0 * 6), i_color=('f', col0 * 6))
    for k in range(N - 1):                        # 域初始 _instances = 1
        vlist.add_instance(i_seed=seeds[k + 1], i_color=cols[k + 1])
    vlist.draw(GL_TRIANGLES)                      # 内部 glDrawArraysInstanced

实测踩到的坑（都写在这里省得下次再踩）
--------------------------------------
1. ``i_seed=(...)`` 的初始数据必须按**基础顶点数**给长度（四边形 6 顶点 × vec4
   = 24 个 float）。只给一条 vec4 会抛
   ``ValueError: Invalid data size for 'i_seed'. Expected 24, got 4.``
   —— pyglet 的 ``set_attribute_data`` 是按 ``vlist.count`` 写整块的。
2. ``add_instance`` 内部 ``_instances += 1`` 后写的是下标 ``_instances - 1``，
   即第 1 次 ``add_instance`` 写的是 **1 号槽**：0 号槽保留初始数据。所以
   ``range(N-1)`` 配 ``seeds[k+1]`` 刚好把 0..N-1 填满，不多不少。
3. 顶点着色器里**用到**的属性才活着：GLSL 会把没用到的 ``in`` 优化掉，pyglet
   按名字找不到会抛 ShaderException。这里 position / colors / i_seed / i_color
   四个都真用上了（colors 作为静态色调乘子）。
4. 加性混合要自己开：``glEnable(GL_BLEND)`` + ``glBlendFunc(GL_SRC_ALPHA, GL_ONE)``。
   粒子互相不遮挡，所以 ``glDepthMask(GL_FALSE)``（画完必须还原成 GL_TRUE，
   否则基类的 ``window.clear()`` 连深度缓冲都清不掉）。
5. 能量用**累加相位**而不是 ``u_time * energy``：后者在按键瞬间相位跳变，画面会
   闪一下；前者只改变推进速率。

用法::

    python demos/particles_gpu.py
    python demos/particles_gpu.py --smoke 2
    python demos/particles_gpu.py --shot

按键：1/2/3 切预设（切了就是手动，不再自动轮播），+/- 调能量，
A 回到自动轮播，鼠标拖动/滚轮转视角，SPACE 暂停，ESC 退出。
"""
import random

import pyglet
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_FALSE, GL_ONE,
                       GL_SRC_ALPHA, GL_TRIANGLES, GL_TRUE, glBlendFunc,
                       glDepthMask, glEnable)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Vec3

from _harness import DemoApp, orbit_camera_input, run
from logic.camera import OrbitCamera

# ---------------------------------------------------------------- 着色器 --
VERTEX_SRC = '''#version 330 core

in vec3 position;      // 基础四边形角点（[-1,1]^2，z=0）
in vec4 colors;        // 基础四边形顶点色（静态色调，见坑位 3）
in vec4 i_seed;        // 每实例种子 [0,1)^4 —— 逐帧动画的唯一输入
in vec4 i_color;       // 每实例基色（这里只当色彩抖动源）

uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform float u_time;      // 累加相位（CPU 每帧只更新这一个标量）
uniform float u_energy;    // 整体能量/速度（+/- 键）
uniform int   u_preset;    // 0 星系漩涡 / 1 喷泉烟花 / 2 湍流云

out vec2 v_corner;
out vec4 v_col;

const float TAU = 6.28318531;

void main() {
    vec3  p    = vec3(0.0);
    float size = 0.10;
    float life = 0.0;                  // 0..1：寿命/强度，用于颜色与尺寸渐变
    float alpha = 0.5;
    vec3  tint = i_color.rgb;

    if (u_preset == 0) {
        // ---- 0：星系漩涡 —— 盘状分布 + 绕 Y 轴差速旋转 ----
        float r   = 0.6 + 9.4 * pow(i_seed.x, 0.62);   // pow<1 让中心更密（盘状）
        float om  = 1.7 / (0.8 + r);                   // 差速：内圈角速度大（开普勒味）
        float ang = i_seed.y * TAU + u_time * om;      // 同一个 ang 里既有随机相位也有旋转
        float y   = (i_seed.z - 0.5) * (0.22 + 0.9 * r / 9.4);   // 薄盘，中心略鼓
        p = vec3(cos(ang) * r, y, sin(ang) * r);
        // 密度波：两条**固定在世界坐标**里的旋臂（粒子转、臂不动）
        float arm = 0.55 + 0.45 * sin(2.0 * ang - 2.6 * r);
        tint *= mix(vec3(1.00, 0.86, 0.60), vec3(0.40, 0.62, 1.00),
                    clamp(r / 9.5, 0.0, 1.0));         // 核球暖黄 -> 旋臂冷蓝
        tint *= 0.30 + 1.00 * arm;
        size  = (0.050 + 0.045 * i_seed.w) * (1.15 - 0.35 * r / 9.5);
        life  = arm;
        alpha = 0.22 + 0.55 * arm;

    } else if (u_preset == 1) {
        // ---- 1：喷泉/烟花 —— 抛物线弹道 + 重力 + 寿命循环 ----
        float rate = 0.45;
        float grp  = floor(i_seed.z * 3.0);            // 分成 3 组错峰发射
        life = fract(u_time * rate + grp * 0.3333 + i_seed.z * 0.05);
        float vy = 9.0 + 6.0 * i_seed.w;               // 竖直初速 9~15
        float G  = 11.0;                               // 重力
        float T  = 2.0 * vy / G;                       // 恰好落回地面的时刻
        float tt = life * T;
        float az = i_seed.y * TAU;
        float sp = 0.16 * vy * (0.35 + i_seed.x);      // 水平初速 ∝ vy -> 束形稳定
        p = vec3(cos(az) * sp * tt,
                 0.15 + vy * tt - 0.5 * G * tt * tt,
                 sin(az) * sp * tt);
        // 颜色随寿命渐变：白热 -> 橙 -> 暗红
        tint  = mix(vec3(1.00, 0.96, 0.78), vec3(1.00, 0.42, 0.10),
                    smoothstep(0.0, 0.55, life));
        tint  = mix(tint, vec3(0.42, 0.06, 0.02), smoothstep(0.55, 1.0, life));
        size  = (0.20 - 0.10 * life) * (0.55 + 0.75 * i_seed.w);
        alpha = 0.45 * (1.0 - life) * (1.0 - life);

    } else {
        // ---- 2：湍流云 —— 多频正弦伪噪声位移 ----
        vec3 q = (i_seed.xyz - 0.5) * vec3(12.0, 5.0, 12.0);
        float t = u_time * 0.6;
        vec3 d = 1.00 * sin(q.yzx * 0.55 + t * 1.00)
               + 0.50 * sin(q.zxy * 1.13 - t * 1.37)
               + 0.25 * sin(q.xyz * 2.31 + t * 0.73);
        p = q + d * (1.25 * u_energy);
        float spin = u_time * 0.09;                    // 整团云极慢自转
        float cs = cos(spin), sn = sin(spin);
        p.xz = mat2(cs, -sn, sn, cs) * p.xz;
        float m = clamp(length(d) / 1.6, 0.0, 1.0);
        tint  = mix(vec3(0.24, 0.42, 0.85), vec3(0.88, 0.96, 1.00), m);
        size  = (0.12 + 0.13 * i_seed.w) * (0.7 + 0.5 * m);
        life  = m;
        alpha = 0.10 + 0.26 * m;
    }

    // --- 朝向相机的公告板：先在视图空间定位，再按像素尺寸铺角点 ---
    // 能量只影响尺度/位移，不影响相位（推进速率由 CPU 的累加相位负责），
    // 这样按 +/- 时画面是"变大变小"而不是"跳帧"。
    size *= 0.80 + 0.25 * u_energy;
    vec4 view_pos = window.view * vec4(p, 1.0);
    view_pos.xy += position.xy * size;
    gl_Position = window.projection * view_pos;

    v_corner = position.xy;
    v_col = vec4(tint, alpha) * colors;
}'''

FRAGMENT_SRC = '''#version 330 core
in vec2 v_corner;
in vec4 v_col;
out vec4 final_colors;

void main() {
    // 径向软衰减：d2 = 1（内切圆）处为 0，四角 d2 = 2 被 max 夹到 0——
    // 于是不需要 discard/分支，边缘天然是软的。
    float d2 = dot(v_corner, v_corner);
    float falloff = max(0.0, 1.0 - d2);
    falloff *= falloff;
    final_colors = vec4(v_col.rgb, v_col.a * falloff);
}'''


# 预设：(名字, 相机距离, 注视点高度)
PRESETS = (('星系漩涡', 21.0, 1.6),
           ('喷泉烟花', 26.0, 4.5),
           ('湍流云', 25.0, 2.0))


class ParticlesDemo(DemoApp):
    TITLE = 'GPU 实例化粒子 (pyglet 2.1.16 vertex_list_instanced)'
    HELP = ('SPACE 暂停   1/2/3 预设   A 自动轮播   +/- 能量   '
            '拖动/滚轮 视角   ESC 退出')
    DURATION = 30.0
    SHOTS = ((6.5, 'docs/demo_particles_a.png'),
             (16.0, 'docs/demo_particles_b.png'))

    COUNT = 45000                 # 粒子数（3~6 万区间）
    BASE_VERTS = 6                # 每颗粒子的基础四边形顶点数
    AUTO_SWITCH = 10.0            # 自动轮播：每 10s 换一个预设

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        # 加性混合：粒子只叠加亮度，不互相遮挡（坑位 4）
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE)

        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))

        # --- 基础几何：一个以原点为中心的四边形（角点在 [-1,1]^2）---
        base_pos, base_col = [], []
        for cx, cy in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0),
                       (-1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
            base_pos += [cx, cy, 0.0]
            base_col += [1.0, 1.0, 1.0, 1.0]

        # --- 每实例数据：固定种子 rand，保证截图可复现 ---
        rng = random.Random(20240607)
        n = self.COUNT
        seeds = [[rng.random() for _ in range(4)] for _ in range(n)]
        cols = [[0.75 + 0.45 * rng.random(), 0.75 + 0.45 * rng.random(),
                 0.75 + 0.45 * rng.random(), 1.0] for _ in range(n)]

        seed0 = seeds[0] * self.BASE_VERTS      # 坑位 1：初始数据要按顶点数给
        col0 = cols[0] * self.BASE_VERTS
        self.vlist = self.program.vertex_list_instanced(
            self.BASE_VERTS, GL_TRIANGLES,
            instance_attributes=['i_seed', 'i_color'],
            position=('f', base_pos), colors=('f', base_col),
            i_seed=('f', seed0), i_color=('f', col0))
        for k in range(n - 1):                  # 坑位 2：0 号槽由初始数据占着
            self.vlist.add_instance(i_seed=seeds[k + 1], i_color=cols[k + 1])

        self.preset = 0
        self.auto_preset = True
        self.energy = 1.0
        self.phase = 0.0                        # 累加相位（坑位 5）

        self.camera = OrbitCamera(target=Vec3(0.0, 1.6, 0.0),
                                  yaw=35.0, pitch=20.0, dist=21.0)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(
            self, self.camera)
        self._set_preset(0)

    def _set_preset(self, i):
        self.preset = i % len(PRESETS)
        _, dist, ty = PRESETS[self.preset]
        self.camera.target = Vec3(0.0, ty, 0.0)
        self.camera.dist = dist

    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol in (key._1, key._2, key._3):
            self.auto_preset = False
            self._set_preset(symbol - key._1)
        elif symbol == key.A:
            self.auto_preset = True
        elif symbol in (key.PLUS, key.EQUAL, key.NUM_ADD):
            self.energy = min(3.0, self.energy + 0.2)
        elif symbol in (key.MINUS, key.NUM_SUBTRACT):
            self.energy = max(0.2, self.energy - 0.2)

    def update(self, dt):
        if not self.paused:
            # 能量只改**推进速率**，不改已积累的相位 -> 按键不会闪跳
            self.phase += dt * (0.5 + 0.5 * self.energy)
        if self.auto_preset:
            want = int(self.time / self.AUTO_SWITCH) % len(PRESETS)
            if want != self.preset:
                self._set_preset(want)
        self.camera.advance(dt)                 # 自动缓慢环绕（8°/s）

    def render(self):
        glDepthMask(GL_FALSE)                   # 粒子不写深度（自己叠自己）
        self.window.view = self.camera.view_matrix()
        prog = self.program
        prog.use()
        prog['u_time'] = self.phase
        prog['u_energy'] = self.energy
        prog['u_preset'] = self.preset
        self.vlist.draw(GL_TRIANGLES)
        glDepthMask(GL_TRUE)                    # 必须还原：否则下一帧深度清不掉

    def status_text(self):
        name = PRESETS[self.preset][0]
        auto = '自动' if self.auto_preset else '手动'
        return (f'粒子 {self.COUNT}   预设 {self.preset + 1}/{len(PRESETS)} '
                f'{name}  能量 {self.energy:.1f}  {auto}   '
                f't={self.time:5.1f}s/{self.DURATION:.0f}s   {self.fps:5.1f} fps'
                f'{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(ParticlesDemo)

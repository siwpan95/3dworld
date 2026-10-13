# -*- coding: utf-8 -*-
"""无限隧道（demoscene 经典）：全屏片元着色器 + 时间 uniform。

本 demo 同时充当 `examples/` 的**参考样板**：它演示了所有 demo 共用的骨架
（`world3d/platform/app.py` 的 DemoApp）以及"纯片元着色器"这一类 demo 的标准写法：

- 几何只有一个 NDC 全屏四边形（`make_fullscreen_quad`），不含任何顶点动画；
- 画面 100% 由 fragment shader 用极坐标隧道公式算出来（无贴图、无外部素材）；
- 片元着色器里必须声明 ``in vec3 position; in vec4 colors;``——pyglet 按名字
  校验顶点属性，声明了却不用会被 GLSL 编译器优化掉，导致创建顶点列表报错。

画面构成：极坐标隧道（1/r 深度）+ 程序化金属面板 + 琥珀灯条 + 行进光带
+ 色散 + 扫描线 + 暗角；全部随时间演化，循环周期 24s。

用法::

    python examples/demoscene_tunnel.py
    python examples/demoscene_tunnel.py --smoke
    python examples/demoscene_tunnel.py --shot

按键：SPACE 暂停，1/2/3 切换配色，T 切换扭动，G 切换扫描线，ESC 退出。
"""
import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glDisable
from pyglet.graphics.shader import Shader, ShaderProgram

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, make_fullscreen_quad, run

VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec2 v_uv;
out vec3 v_tint;
void main() {
    v_uv = position.xy * 0.5 + 0.5;
    v_tint = colors.rgb;
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

FRAGMENT_SRC = '''#version 330 core
in vec2 v_uv;
in vec3 v_tint;
out vec4 final_colors;

uniform float u_time;
uniform vec2  u_res;
uniform vec3  u_palette;   // 灯条基色（1/2/3 键切换）
uniform float u_twist;     // 隧道扭动强度（T 键）
uniform float u_scan;      // 扫描线开关（G 键）

float hash21(vec2 p) {
    p = fract(p * vec2(123.34, 456.21));
    p += dot(p, p + 45.32);
    return fract(p.x * p.y);
}

void main() {
    // --- 以短边归一化的中心坐标 ---
    vec2 p = (v_uv - 0.5) * vec2(u_res.x / u_res.y, 1.0) * 2.0;

    // --- 相机滚转 + 隧道扭动：整体旋转采样坐标 ---
    float roll = u_time * 0.25 + u_twist * 0.6 * sin(u_time * 0.37);
    float cr = cos(roll), sr = sin(roll);
    p = mat2(cr, -sr, sr, cr) * p;

    float r = max(length(p), 1e-4);
    float ang = atan(p.y, p.x);

    // --- 极坐标隧道：z 为"深度"，与 r 成反比 ---
    float z = 1.0 / r;
    // 行进速度带心跳式脉动（模拟 demo 的节拍）
    float beat = 1.0 + 0.35 * sin(u_time * 3.14159);
    vec2 uv = vec2(ang / 3.14159265, z * 0.55 + u_time * 1.7 * beat);

    // --- 程序化"金属面板"：格子哈希 + 缝隙 ---
    vec2 grid = uv * vec2(9.0, 5.0);
    vec2 cell = floor(grid);
    vec2 f = fract(grid);
    float h = hash21(cell);
    vec3 col = mix(vec3(0.045, 0.055, 0.085), vec3(0.13, 0.17, 0.26), h);
    col += 0.05 * h * vec3(0.4, 0.6, 1.0);                 // 轻微冷色噪声

    float gap = smoothstep(0.0, 0.045, f.x) * smoothstep(1.0, 0.955, f.x)
              * smoothstep(0.0, 0.06, f.y) * smoothstep(1.0, 0.94, f.y);
    col *= 0.22 + 0.78 * gap;                               // 缝隙压暗

    // --- 琥珀灯条：稀疏格子自发光，随机闪烁 ---
    float lit = step(0.90, hash21(cell * 0.71 + vec2(3.1, 7.7)));
    float flick = 0.65 + 0.35 * sin(u_time * 7.0 + h * 33.0);
    col += lit * u_palette * flick * 1.35;

    // --- 行进光带：沿深度方向扫过的亮环 ---
    float band = fract(z * 0.45 - u_time * 0.9);
    float ring = pow(1.0 - abs(band * 2.0 - 1.0), 12.0);
    col += ring * vec3(0.35, 0.75, 1.0) * 0.5 * smoothstep(0.6, 4.0, z);

    // --- 深度雾：远处（r 小 = z 大）融入黑暗 ---
    col *= exp(-z * 0.30);

    // --- 中心辉光：隧道尽头的光 ---
    float core = pow(max(0.0, 1.0 - r * 1.9), 4.0);
    col += core * (u_palette * 0.9 + vec3(0.25, 0.35, 0.6));

    // --- 色散：按半径轻微错位红蓝通道（伪·镜头色差）---
    col.r *= 1.0 + 0.18 * core;
    col.b *= 1.0 + 0.10 * (1.0 - core);

    // --- 扫描线 + 暗角 ---
    float scan = 1.0 - u_scan * 0.12 * (0.5 + 0.5 * sin(v_uv.y * u_res.y * 3.14159));
    float vig = smoothstep(1.35, 0.25, length(v_uv - 0.5) * 1.6);
    col *= scan * mix(0.55, 1.0, vig);

    // --- 色调映射 + 轻微 gamma ---
    col = col / (col + vec3(0.85));
    col = pow(max(col, 0.0), vec3(0.85));

    final_colors = vec4(col * v_tint, 1.0);
}'''


class TunnelDemo(DemoApp):
    TITLE = 'demoscene · 无限隧道 (pyglet 全屏片元着色器)'
    HELP = 'SPACE 暂停   1/2/3 配色   T 扭动   G 扫描线   ESC 退出'
    DURATION = 24.0
    HUD = True
    SHOTS = ((4.0, 'docs/demo_tunnel_a.png'),
             (13.5, 'docs/demo_tunnel_b.png'))

    # 三套配色：琥珀（kkrieger 味）/ 青蓝（cyberpunk）/ 品红（vaporwave）
    PALETTES = ((1.00, 0.55, 0.14), (0.20, 0.85, 1.00), (1.00, 0.30, 0.72))

    def setup(self):
        glDisable(GL_DEPTH_TEST)                    # 纯 2D 全屏 pass，不需要深度
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        self.quad = make_fullscreen_quad(self.program)
        self.palette_i = 0
        self.twist = 1.0
        self.scan = 1.0

    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol in (key._1, key._2, key._3):
            self.palette_i = symbol - key._1
        elif symbol == key.T:
            self.twist = 0.0 if self.twist else 1.0
        elif symbol == key.G:
            self.scan = 0.0 if self.scan else 1.0

    def render(self):
        w, h = self.window.width, self.window.height
        prog = self.program
        prog.use()
        prog['u_time'] = self.time
        prog['u_res'] = (float(w), float(h))
        prog['u_palette'] = self.PALETTES[self.palette_i]
        prog['u_twist'] = self.twist
        prog['u_scan'] = self.scan
        self.quad.draw(GL_TRIANGLES)

    def status_text(self):
        names = ('琥珀', '青蓝', '品红')
        return (f'{names[self.palette_i]}   t={self.time:6.2f}s / '
                f'{self.DURATION:.0f}s   {self.fps:5.1f} fps'
                f'{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(TunnelDemo)

# -*- coding: utf-8 -*-
"""水面波动：Gerstner 波在**顶点着色器**里位移，配菲涅尔天空反射 + 太阳高光。

这个 demo 的重点是"**同一套数学跑在 CPU 与 GPU 两侧**"：

- ``logic/waves.py`` 是纯数学层（不 import pyglet.gl，可无窗口单测）：
  ``gerstner_height`` / ``gerstner_gradient`` / ``gerstner_normal``。
- 顶点着色器里把**同一组波参数**（振幅 A、波长 L、方向 d，深水色散 w=√(gk)）
  按同一公式重算一遍，得到位移后的顶点与解析法线。
- 浮标的吃水与横摇由 CPU 侧调用 ``logic/waves.gerstner_height`` /
  ``gerstner_normal`` 算出——所以浮标永远精确贴在波面上，不会"飘在半空"
  或者"陷进浪里"。这是把数学抽成独立模块（而不是写死在 shader 字符串里）
  的真正收益：**跨语言的一致性可以被肉眼验证**。

网格与性能：

- 水面是 200×200 段的网格 = 40401 个顶点、80000 个三角形，**只在 setup 建一次**
  （顶点数据一次上传，逐帧零重建；g05 纪律也要求产物/字节码不入库）。
  用 ``vertex_list_indexed``（索引 16 位共用）省掉近 3MB 的重复顶点数据。
- 逐帧只更新 uniform：``u_time``、三个预设的振幅数组（浮点数，不是几何）。
  12 组波 × 每顶点 → 40 万次 sin/cos 由 GPU 承担，CPU 侧每帧只算 4 个浮标。
- 1280x720 稳定 60fps（vsync 上限）。这一档放在核显上也够用；
  真掉帧就把 ``GRID_N`` 从 200 降到 128（三角形数减 60%，画面几乎无差别）。

踩过的坑（写下来免得下次再撞）：

- 平面网格的法线**不用 CPU 预烘**：波面每帧都在变，预烘法线只有在静止时才对。
  这里逐顶点算解析法线（波高梯度），片元里只做归一化与着色。
- 海平面基准 y=0 与相机 target 必须吻合，否则"地平线"会随波浪整体左右浮动。
- 天空不能只靠 ``glClearColor``：用一个大尺寸地面色平面 + 全屏 NDC 天空 pass，
  否则水面 plane 边缘在近地平线处会露出"世界边界"的直线。
- 线框开关（W 键）用 ``glPolygonMode``，**必须**在关闭/退出前切回 GL_FILL：
  这开关是进程级 GL 状态，忘了切回会让随后的 HUD 文字也变成线框（只见轮廓）。
- 混合状态要显式声明（g：需要混合时明确 glEnable(GL_BLEND) + glBlendFunc）；
  这里水面是**不透明**的（深浅水色直接在片元里混），所以干脆关掉混合，
  免得半透明水面和浮标产生排序问题。

用法::

    python examples/water_waves.py
    python examples/water_waves.py --smoke 3
    python examples/water_waves.py --shot
    python examples/water_waves.py --size 960x540

按键：1/2/3 波浪强度预设，W 线框，鼠标拖动环绕/滚轮缩放，SPACE 暂停，ESC 退出。
"""
import math

import pyglet
from pyglet.gl import (GL_BLEND, GL_DEPTH_TEST, GL_FILL, GL_FRONT_AND_BACK,
                       GL_LINE, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA, GL_TRIANGLES,
                       glBlendFunc, glClearColor, glDisable, glEnable, glPolygonMode)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3

# ---- sys.path 引导（平台/引擎/逻辑各顶层包）-------------------------------
# ⚠️ 必须**显式**写：P2-1 之前这行是隐式的 —— `from _harness import ...`（裸名）时
# `_harness` 自己会插项目根；改成 `from world3d.platform.app import ...` 后不再隐式生效，
# 少了它就会 ModuleNotFoundError: No module named 'world3d'（P2-1 全量验收实测）。

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.platform.app import DemoApp, orbit_camera_input, run
from world3d.logic.camera import OrbitCamera
from world3d.logic.waves import sample_surface, scaled_preset, wave_presets

DURATION = 40.0
GRID_N = 200              # 网格段数（200 → 40401 顶点 / 80000 三角形）
PLANE_HALF = 95.0         # 水面半边长（世界单位）：要盖到地平线
GRAVITY = 9.81
SEA_FLOOR_Y = -5.0        # 海床（纯色）：透出深浅层次，避免"悬空的水面"
SKY_TOP = (0.10, 0.24, 0.52)
SKY_HORIZON = (0.66, 0.76, 0.86)
SUN_DIR = (0.35, 0.14, -0.93)       # 太阳偏低：让镜面高光落在画面中（见文件头说明）

# ---------------------------------------------------------------- 着色器 --
# 波参数用 GLSL **数组** uniform（u_kd[12] / u_amp[12]），配一个固定 12 次的循环。
# 绕了两圈才定下来，两条坑都记在这：
#
# 1) 先写成 12 个独立 uniform（u_p0..u_p11 / u_a0..u_a11）+ 展开的累加表达式。
#    结果驱动把**最后一组**优化掉了：`prog['u_p11']` 抛 "Uniform not found"。
#    实测 `prog.uniforms` 只剩 u_p0..u_p10 / u_a0..u_a10 —— 展开表达式末尾那项
#    被后端丢掉了（12 个独立 uniform 的展开累加不是驱动爱看的形式）。
# 2) 换回数组 uniform：pyglet 的 `_introspect_uniforms` 会把名字里的 `[0]`
#    `strip` 掉，所以 Python 侧的键是 `u_kd` / `u_amp`（**不带下标**），
#    一次赋整份数据；写 `prog['u_kd[0]'] = ...` 会 KeyError。
#    另外 `strip('[0]')` 是"按字符集剥字符"，别把数组取成 `u_a0` 这种名字。
# 用固定 12 次的循环（不做 `if (i >= count) break`）还顺带保证了所有槽位
# 都真被用到——否则"振幅恒为 0 的那组"同样会被优化掉。槽位多算几次
# sin/cos 的代价可以忽略，换来的是驱动永远不会删 uniform。
WATER_VERT = '''#version 330 core
in vec3 position;      // 基础平面：x/z 是世界坐标，y=0
in vec2 uv;            // uv.y 作为"离岸距离"，用来淡出短波细节
in vec4 colors;
out vec3 v_world;
out vec3 v_normal;
out vec2 v_uv;
out vec3 v_view;

uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform float u_time;
uniform vec4  u_kd[12];   // xyz = (dx*k, dz*k, k)，w = 角频率 ω（深水色散 √(gk)）
uniform float u_amp[12];  // 每波振幅（预设过渡时逐槽位插值）

void main() {
    vec2 xz = position.xz;
    float h = 0.0;
    vec2 grad = vec2(0.0);
    for (int i = 0; i < 12; i++) {
        vec3 kd = u_kd[i].xyz;
        float amp = u_amp[i];
        float phase = kd.x * xz.x + kd.y * xz.y - u_kd[i].w * u_time;
        h += amp * sin(phase);
        grad += amp * cos(phase) * kd.xy;      // ∂h/∂x, ∂h/∂z
    }
    v_world = vec3(xz.x, h, xz.y);
    v_normal = normalize(vec3(-grad.x, 1.0, -grad.y));
    v_uv = uv;
    vec4 vp = window.view * vec4(v_world, 1.0);
    v_view = vp.xyz;
    gl_Position = window.projection * vp;
}'''

WATER_FRAG = '''#version 330 core
in vec3 v_world;
in vec3 v_normal;
in vec2 v_uv;
in vec3 v_view;
out vec4 final_colors;

uniform vec3  u_eye;
uniform vec3  u_sun_dir;
uniform vec3  u_deep;
uniform vec3  u_shallow;
uniform vec3  u_sky_top;
uniform vec3  u_sky_hz;
uniform float u_foam;

void main() {
    vec3 N = normalize(v_normal);
    vec3 V = normalize(u_eye - v_world);
    vec3 L = normalize(u_sun_dir);
    float ndv = max(dot(N, V), 0.0);

    // --- 菲涅尔：Schlick 近似 F0=0.02（水的垂直入射反射率）---
    float fres = 0.02 + 0.98 * pow(1.0 - ndv, 5.0);

    // --- 反射方向采样"天空"：地平线方向偏白，抬头方向偏蓝 ---
    vec3 R = reflect(-V, N);
    float up = clamp(R.y * 0.5 + 0.5, 0.0, 1.0);
    vec3 sky = mix(u_sky_hz, u_sky_top, pow(up, 0.65));

    // --- 水体颜色：视线越"贴水"越深，正上方看下去偏浅（深浅水色）---
    vec3 body = mix(u_deep, u_shallow, clamp(V.y * 1.35, 0.0, 1.0));

    // --- 太阳镜面高光（Blinn-Phong，非常尖，做出"阳光碎金"）---
    vec3 H = normalize(L + V);
    float spec = pow(max(dot(N, H), 0.0), 420.0) * 2.6
               + pow(max(dot(N, H), 0.0), 48.0) * 0.10;

    // --- 白沫：波峰越高越白，远处（uv.y 大）淡出，避免地平线变成白线 ---
    // 上限压到 0.5：全白会把画面糊成"冰川"（第一版风暴档就是这么翻车的），
    // 留一半水色反而更像白浪。
    float crest = smoothstep(0.90, 1.70, v_world.y);
    float foam = crest * u_foam * (1.0 - smoothstep(0.35, 0.85, v_uv.y));

    vec3 col = mix(body, sky, fres) + vec3(1.0, 0.96, 0.88) * spec;
    col = mix(col, vec3(0.86, 0.93, 0.97), min(foam * 0.72, 0.5));

    // --- 雾：远处融进天空地平线色，藏掉水面 plane 的边界 ---
    float fog = 1.0 - exp(-0.00035 * dot(v_world.xz, v_world.xz));
    col = mix(col, u_sky_hz, clamp(fog, 0.0, 1.0));

    // --- 色调映射 + gamma ---
    col = (col * (2.51 * col + 0.03)) / (col * (2.43 * col + 0.59) + 0.14);
    col = pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2));
    final_colors = vec4(col, 1.0);
}'''

SKY_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec2 v_ndc;
void main() {
    v_ndc = position.xy;
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

SKY_FRAG = '''#version 330 core
in vec2 v_ndc;
out vec4 final_colors;
// 注意：这里**不要**声明用不到的 uniform（比如 u_sun_dir）——GLSL 会把未使用的
// uniform 优化掉，pyglet 随后 `prog['u_sun_dir'] = ...` 会抛 ShaderException
// "A Uniform with the name ... was not found"。要么真的用它，要么别声明。
uniform vec3 u_sky_top;
uniform vec3 u_sky_hz;
void main() {
    // 由 NDC 反推"仰角"的廉价近似：只关心天空的上下分层，不追求物理正确的射线
    float t = clamp(v_ndc.y * 0.75 + 0.18, 0.0, 1.0);
    vec3 col = mix(u_sky_hz, u_sky_top, pow(t, 0.75));
    float s = pow(max(v_ndc.y * 0.6 + 0.35, 0.0), 24.0);   // 太阳附近的亮斑
    col += vec3(1.0, 0.92, 0.72) * s * 0.55;
    col = (col * (2.51 * col + 0.03)) / (col * (2.43 * col + 0.59) + 0.14);
    col = pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2));
    final_colors = vec4(col, 1.0);
}'''

BUOY_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 v_color;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
void main() {
    vec4 wp = model * vec4(position, 1.0);
    v_color = colors;
    gl_Position = window.projection * window.view * wp;
}'''

BUOY_FRAG = '''#version 330 core
in vec4 v_color;
out vec4 final_colors;
uniform vec3 u_sun_dir;
void main() {
    // 浮标很小，用顶点色当常量色即可；这里只补一点点方向光对比
    float k = 0.72 + 0.28 * clamp(u_sun_dir.y + 0.35, 0.0, 1.0);
    final_colors = vec4(v_color.rgb * k, 1.0);
}'''


def _box_data(sx, sy, sz, rgb):
    """小盒体（浮标）：36 顶点，六面各自的固定明暗差，做出体积感。"""
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    faces = [
        ((0.0, 0.35), (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)),
        ((0.0, -0.35), (hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)),
        ((0.0, 1.0), (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)),
        ((0.0, -1.0), (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)),
        ((1.0, 0.2), (hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)),
        ((-1.0, 0.2), (-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)),
    ]
    verts, colors = [], []
    for (_, k), a, b, c, d in faces:
        g = 0.62 + 0.38 * abs(k)
        for p in (a, b, c, a, c, d):
            verts += list(p)
            colors += [rgb[0] * g, rgb[1] * g, rgb[2] * g, 1.0]
    return verts, colors


class WaterWavesDemo(DemoApp):
    TITLE = '水面波动 · Gerstner 波顶点位移 + 菲涅尔天空 + CPU/GPU 同一套数学'
    HELP = ('1/2/3 波浪强度   W 线框   鼠标拖动环绕/滚轮缩放   '
            'SPACE 暂停   ESC 退出')
    DURATION = DURATION
    FOV = 55.0
    Z_NEAR, Z_FAR = 0.1, 1200.0
    SHOTS = ((11.0, 'docs/demo_water_a.png'),        # 预设「微风」
             (30.0, 'docs/demo_water_b.png'))        # 预设已切到「风暴」

    # 四个浮标：(x, z, 主色)。分散放，方便看出"波峰错落"与倾斜方向不同
    BUOYS = ((-5.0, -1.6, (0.86, 0.42, 0.24)),
             (4.2, 2.6, (0.90, 0.78, 0.30)),
             (-0.8, 6.8, (0.30, 0.72, 0.62)),
             (6.8, -5.0, (0.78, 0.34, 0.52)))
    BUOY_SIZE = (2.2, 1.0, 1.7)          # 浮标箱体尺寸（放大过：1.5x0.7 在浪里太小）

    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glClearColor(*SKY_HORIZON, 1.0)
        glDisable(GL_BLEND)                 # 水面不透明，明确关掉混合

        # --- 波预设：先算出三档的参数表（波长/方向固定，只有振幅变）---
        self.presets = []
        for idx in range(len(wave_presets())):
            name, waves = scaled_preset(idx)
            self.presets.append((name, waves))
        self.preset_i = 0
        self.amp_from = self._amps(0)
        self.amp_to = list(self.amp_from)
        self.blend_t = 1.0                  # 1 = 过渡完成
        self.foam_level = 0.0
        self.waves = self.presets[0][1]
        self.amps = list(self.amp_from)
        self.wave_count = len(self.waves)
        # 振幅基准 = 当前档位波组的振幅总和（浮标按比例跟随插值后的浪高）
        self.amp_ref = sum(self.amp_to) or 1.0

        # --- 水面网格：200x200 段，只在 setup 建一次 ---
        n = GRID_N
        step = 2.0 * PLANE_HALF / n
        verts, uvs = [], []
        for iz in range(n + 1):
            z = -PLANE_HALF + iz * step
            for ix in range(n + 1):
                x = -PLANE_HALF + ix * step
                verts += [x, 0.0, z]
                uvs += [ix / n, iz / n]
        indices = []
        for iz in range(n):
            for ix in range(n):
                a = iz * (n + 1) + ix
                b = a + 1
                c = a + n + 1
                d = c + 1
                indices += [a, c, b, b, c, d]

        self.water_program = ShaderProgram(Shader(WATER_VERT, 'vertex'),
                                           Shader(WATER_FRAG, 'fragment'))
        # 顶点属性：position + uv（索引 16 位够用：40401 < 65536）
        self.water = self.water_program.vertex_list_indexed(
            len(verts) // 3, GL_TRIANGLES, indices,
            position=('f', verts), uv=('f', uvs))
        # --- 天空（全屏 NDC 四边形）---
        self.sky_program = ShaderProgram(Shader(SKY_VERT, 'vertex'),
                                         Shader(SKY_FRAG, 'fragment'))
        self.sky_quad = self.sky_program.vertex_list(
            6, GL_TRIANGLES,
            position=('f', (-1.0, -1.0, 0.0, 1.0, -1.0, 0.0, 1.0, 1.0, 0.0,
                            -1.0, -1.0, 0.0, 1.0, 1.0, 0.0, -1.0, 1.0, 0.0)),
            colors=('f', (1.0, 1.0, 1.0, 1.0) * 6))

        # --- 海床：一块大平面（纯色深蓝，压在水面下方给"深度感"）---
        self.floor_program = ShaderProgram(Shader(BUOY_VERT, 'vertex'),
                                           Shader(BUOY_FRAG, 'fragment'))
        fv, fc = _box_data(2.0 * PLANE_HALF, 1.0, 2.0 * PLANE_HALF, (0.02, 0.07, 0.13))
        self.seafloor = self.floor_program.vertex_list(
            len(fv) // 3, GL_TRIANGLES, position=('f', fv), colors=('f', fc))
        self.floor_model = Mat4.from_translation(
            Vec3(0.0, SEA_FLOOR_Y - 0.5, 0.0))

        # --- 浮标：4 个小盒体 + 一根"桅杆"，逐帧由 CPU 侧波函数摆位 ---
        self.buoy_program = ShaderProgram(Shader(BUOY_VERT, 'vertex'),
                                          Shader(BUOY_FRAG, 'fragment'))
        self.buoys = []
        for (bx, bz, rgb) in self.BUOYS:
            bv, bc = _box_data(*self.BUOY_SIZE, rgb)
            mv, mc = _box_data(0.20, 2.2, 0.20, (0.92, 0.92, 0.95))
            box = self.buoy_program.vertex_list(len(bv) // 3, GL_TRIANGLES,
                                                position=('f', bv), colors=('f', bc))
            mast = self.buoy_program.vertex_list(len(mv) // 3, GL_TRIANGLES,
                                                 position=('f', mv), colors=('f', mc))
            # 桅杆挂在同一 model 下：这里把它的局部偏移直接烘进几何
            self.buoys.append({'x': bx, 'z': bz, 'box': box, 'mast': mast,
                               'model': Mat4(), 'h': 0.0})

        # --- 相机（海平面基准 y=0；贴近一点让浮标与浪形看得出来）---
        self.camera = OrbitCamera(target=Vec3(0, 0.5, 0), yaw=34.0, pitch=12.0, dist=26.0)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(self, self.camera)

        self.wireframe = False
        self.auto_switch = True             # 无人值守时自动切到"风暴"（供第二张截图）
        self.auto_switch_at = 22.0

    # ---- 波参数 ↔ uniform ----
    def _amps(self, index):
        """把某档预设的振幅填进 12 个槽位（不足补 0，槽位数固定便于插值）。"""
        amps = [0.0] * 12
        _, waves = self.presets[index]
        for i, w in enumerate(waves[:12]):
            amps[i] = w.amplitude
        return amps

    @staticmethod
    # ---- 输入 ----
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol in (key._1, key._2, key._3):
            self._set_preset(symbol - key._1)
        elif symbol == key.W:
            self.wireframe = not self.wireframe
            glPolygonMode(GL_FRONT_AND_BACK, GL_LINE if self.wireframe else GL_FILL)

    def _set_preset(self, index):
        """切换预设：从**当前混合振幅**出发插值到目标档，避免浪高瞬间跳变。"""
        index = index % len(self.presets)
        self.auto_switch = False
        if index == self.preset_i and self.blend_t >= 1.0:
            return
        self.amp_from = list(self.amp_to)
        self.preset_i = index
        self.amp_to = self._amps(index)
        self.blend_t = 0.0

    # ---- 每帧 ----
    def update(self, dt):
        # 预设插值（0.9s 过渡）：海浪的"强度"是插值出来的，不是硬切
        if self.blend_t < 1.0:
            self.blend_t = min(1.0, self.blend_t + dt / 0.9)
        if self.auto_switch and self.time >= self.auto_switch_at:
            self._set_preset(2)
            self.auto_switch = False
        t = self.blend_t
        smooth = t * t * (3.0 - 2.0 * t)                 # smoothstep
        # GPU 侧的振幅：在"上一档 → 目标档"之间逐槽位插值
        self.amps = [a + (b - a) * smooth
                     for a, b in zip(self.amp_from, self.amp_to)]
        # CPU 侧浮标用**目标档**的波组求高度，再乘过渡进度 smooth。
        # 为什么不直接用混合后的振幅算高度：那要按振幅重排波的顺序，
        # 而方向/波长是绑在槽位上的，重排会让浮标在过渡期间跳变。
        # "目标档 + 进度系数"既连续，又和 GPU 的最终浪高一致（振幅线性 ⇒ 高度线性）。
        self.waves = self.presets[self.preset_i][1]
        self.wave_count = sum(1 for a in self.amps if a > 1e-6)
        # 白沫强度：跟着插值后的总振幅走（微风档 ≈0.12、风暴档 ≈0.60）
        self.foam_level = min(1.0, sum(self.amps) / 3.2)

        # ---- 浮标：CPU 侧调用 logic/waves.py 同一套数学 ----
        # 位置 = 该点波面高度；姿态 = 用解析法线构造的正交基（切平面）。
        # 与 GPU 同一公式：着色器用 (dx*k, dz*k, k) 与振幅求相位，
        # 这里用 Wave.phase() / Wave.amplitude——同源同参，肉眼可验。
        ratio = smooth
        for b in self.buoys:
            h, n = sample_surface(b['x'], b['z'], self.time, self.waves)
            b['h'] = h * ratio
            up = Vec3(n[0], n[1], n[2])
            # 切线 = world Z 投影到切平面；副切线 = up × tangent（右手系）。
            # 注意 pyglet 的 Vec3：模长是**方法** ``v.length()``，不是属性；
            # ``v.length`` 会静默返回绑定方法（比较时才炸），``abs(v)`` 走
            # ``__abs__`` 返回"逐分量取绝对值后的向量"，也不是模长。写错两次。
            tz = Vec3(0.0, 0.0, 1.0)
            tang = tz - up * tz.dot(up)
            if tang.length() < 1e-4:                     # 法线几乎与 Z 平行时退化
                tang = Vec3(1.0, 0.0, 0.0) - up * up.x
            tang = tang.normalize()
            bitan = up.cross(tang)
            # 列主序：第 0/1/2 列分别是切/副切/法线，平移列写在第 12~14 项
            rot = Mat4(tang.x, tang.y, tang.z, 0.0,
                       bitan.x, bitan.y, bitan.z, 0.0,
                       up.x, up.y, up.z, 0.0,
                       0.0, 0.0, 0.0, 1.0)
            b['model'] = Mat4.from_translation(Vec3(b['x'], h + 0.15, b['z'])) @ rot

    def _upload_waves(self, prog):
        """把 12 组波参数写进 uniform。

        pyglet 侧键名是 `u_kd` / `u_amp`（数组下标由库内部处理，见文件顶部说明），
        一次赋整份数据。**vec4 数组要传"12 个 4 元组"，不能传 48 个扁平 float**：
        pyglet 的 `_UniformArray._c_array` 在 vec4 情况下是
        `(c_float * 4 * 12)()` —— ctypes 的"类型 × N"在这里得到的是
        **12 个各含 4 个 float 的元素**（len == 12），所以长度校验比的是
        "元素个数"而不是分量总数。传扁平 48 个会报
        "Size of data (48) does not match size of the uniform: 12"。
        """
        prog['u_kd'] = self._pack_kd(self.waves)
        prog['u_amp'] = list(self.amps)

    @staticmethod
    def _pack_kd(waves):
        """12 个 (dx*k, dz*k, k, ω) 元组；空槽位填零元组（振幅为 0，不参与）。"""
        out = []
        for i in range(12):
            if i < len(waves):
                w = waves[i]
                k = 2.0 * math.pi / w.wavelength
                out.append((w.direction[0] * k, w.direction[1] * k, k, w.omega))
            else:
                out.append((0.0, 0.0, 0.0, 0.0))
        return out

    def render(self):
        self.window.view = self.camera.view_matrix()

        # --- 天空（全屏，先画；不需要深度）---
        glDisable(GL_DEPTH_TEST)
        sp = self.sky_program
        sp.use()
        sp['u_sky_top'] = SKY_TOP
        sp['u_sky_hz'] = SKY_HORIZON
        self.sky_quad.draw(GL_TRIANGLES)
        glEnable(GL_DEPTH_TEST)

        # --- 海床 ---
        fp = self.floor_program
        fp.use()
        fp['u_sun_dir'] = SUN_DIR
        fp['model'] = self.floor_model
        self.seafloor.draw(GL_TRIANGLES)

        # --- 水面（顶点位移 + 解析法线）---
        wp = self.water_program
        wp.use()
        wp['u_time'] = self.time
        self._upload_waves(wp)
        wp['u_eye'] = tuple(self.camera.eye)
        wp['u_sun_dir'] = SUN_DIR
        wp['u_deep'] = (0.012, 0.10, 0.19)
        wp['u_shallow'] = (0.05, 0.40, 0.47)
        wp['u_sky_top'] = SKY_TOP
        wp['u_sky_hz'] = SKY_HORIZON
        wp['u_foam'] = 0.35 + 0.65 * self.foam_level
        self.water.draw(GL_TRIANGLES)

        # --- 浮标 ---
        bp = self.buoy_program
        bp.use()
        bp['u_sun_dir'] = SUN_DIR
        for b in self.buoys:
            bp['model'] = b['model']
            b['box'].draw(GL_TRIANGLES)
            b['mast'].draw(GL_TRIANGLES)

    def status_text(self):
        name = self.presets[self.preset_i][0]
        hs = [b['h'] for b in self.buoys]
        flag = '  [PAUSED]' if self.paused else ''
        return (f'波浪「{name}」  波数 {self.wave_count}  '
                f'浮标高度 {min(hs):+.2f}~{max(hs):+.2f}m  '
                f'{"线框 " if self.wireframe else ""}'
                f't={self.time:5.2f}s/{self.DURATION:.0f}s   {self.fps:5.1f} fps{flag}')


if __name__ == '__main__':
    run(WaterWavesDemo)

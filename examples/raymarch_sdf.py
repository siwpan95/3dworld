# -*- coding: utf-8 -*-
"""SDF 光线步进（raymarching）：一个全屏四边形 + 一段片元着色器画完整场景。

这是"纯片元着色器"类 demo 的第二个样板（第一个是 ``demoscene_tunnel.py``），
但内容从"2D 极坐标贴图"升级成**真三维**：摄像机射线 + 有符号距离场步进。

画面构成（全部在 ``FRAGMENT_SRC`` 里算，无任何外部素材）：

- SDF 图元：球 / 圆角盒 / 圆环 / 地面平面，用 **smooth-min** 融合
  （多项式 smin，GPU Gems / iq 的经典写法：``h = clamp(0.5+0.5*(b-a)/k, 0, 1)``），
  融合半径 ``k`` 随时间呼吸 → 三团形状时而分离、时而粘连成"软体生物"。
- **解析法线**：四边形中心差分（4 次 SDF 求值），比把法线写死/烘进顶点靠谱。
- **软阴影**：iq 的 `ray-march shadow`（沿光线步进，用 ``min(k*h/t)`` 判遮挡），
  得到半影随距离变宽的接触阴影——硬阴影那种刀切边在这个尺度下很假。
- **AO**：iq 的 5 次采样环境遮蔽，凹处自然变暗。
- **雾**：指数雾 ``exp(-t·density)`` 把远处地面融进天空色，藏掉 t_max 截断。
- **色调映射**：ACES 近似 + gamma，避免高光直接削平成白块。
- 地面棋盘格 + 图元根部的"接地暗环"，让物体不像浮在空中。

循环步数上限 **96**（``MAX_STEPS``），软阴影另用 20 步独立预算——两者都不能
在片元里失控，否则一个坏像素就能拖垮整帧。

性能：1280x720 在核显上大约 45~60fps（取决于显卡与是否启用阴影）。
掉帧明显时用 ``python examples/raymarch_sdf.py --size 960x540``，
像素少了 44%，步进次数不变，画面内容完全一样。降采样是这里唯一的
"免费"提速手段；减 MAX_STEPS 会先让远景地面出现条纹瑕疵（步进提前 break）。

踩过的坑（pyglet 2.1.16）：

- 全屏 pass 的顶点着色器**必须**声明 ``in vec3 position; in vec4 colors;``——
  pyglet 按名字校验属性，几何里传了 ``colors`` 而着色器没声明就抛 ShaderException；
  声明了却完全不用会被 GLSL 优化掉，同样找不到。
- ``glDisable(GL_DEPTH_TEST)`` 后画全屏四边形，否则深度缓冲里上一帧的
  3D 内容会挡住它（本 demo 没有 3D 几何，但这是同类 pass 的通用规矩）。
- uniform 名要和 Python 侧 ``prog['u_xxx'] = ...`` 完全一致：拼错**不会报错**，
  只会静默保持 0（画面"莫名全黑"的第一嫌疑）。

用法::

    python examples/raymarch_sdf.py
    python examples/raymarch_sdf.py --smoke 3
    python examples/raymarch_sdf.py --shot
    python examples/raymarch_sdf.py --size 960x540      # 掉帧时用这个

按键：鼠标拖动改方位角/俯仰角，滚轮推拉距离，1/2/3 切换材质配色，
      T 切换软阴影，A 切换自动环绕，SPACE 暂停，ESC 退出。
"""
import math

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
void main() {
    v_uv = position.xy * 0.5 + 0.5;     // NDC → [0,1]²，片元里再转成屏幕坐标
    gl_Position = vec4(position.xy, 0.0, 1.0);
}'''

FRAGMENT_SRC = '''#version 330 core
in vec2 v_uv;
out vec4 final_colors;

uniform vec2  u_res;
uniform float u_time;        // 演示时间（已按 DURATION 取模）
uniform vec3  u_eye;         // 相机位置
uniform vec3  u_target;      // 相机注视点
uniform vec3  u_palette;     // 材质主色（1/2/3 键）
uniform float u_shadow;      // 软阴影开关
uniform float u_morph;       // 0..1 形态进度

const int   MAX_STEPS = 96;  // 主步进预算（硬上限，绝不放开）
const int   SHADOW_STEPS = 20;
const float T_MAX = 26.0;    // 最远步进距离（远超场景尺寸，靠雾收尾）

// 场景尺寸（世界单位）：地面在 y = GROUND，图元悬在地面之上
const float GROUND = -1.15;

// ---------------------------------------------------------------- SDF --
float sdSphere(vec3 p, float r) { return length(p) - r; }

float sdRoundBox(vec3 p, vec3 b, float r) {
    vec3 q = abs(p) - b + r;
    return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0) - r;
}

float sdTorus(vec3 p, vec2 t) {          // t = (主半径, 管半径)
    vec2 q = vec2(length(p.xz) - t.x, p.y);
    return length(q) - t.y;
}

float smin(float a, float b, float k) {  // 多项式平滑并集
    float h = clamp(0.5 + 0.5 * (b - a) / k, 0.0, 1.0);
    return mix(b, a, h) - k * h * (1.0 - h);
}

vec2 scene(vec3 p) {
    float m = u_morph;                                   // 0..1 形态进度
    float grow = 0.85 + 0.30 * sin(u_time * 0.9);        // 整体呼吸
    float k = 0.20 + 0.30 * (0.5 - 0.5 * cos(m * 3.14159));  // 融合半径呼吸

    // 图元都抬高 1.05：这样它们"坐"在地面上（底部刚好蹭到 y=GROUND），
    // 阴影是**接地阴影**——悬空 1 米以上时软阴影会飘在物体正下方老远，
    // 看起来像贴纸而不是影子。
    vec3 q0 = p - vec3(0.0, 1.05, 0.0);

    // 球：沿 X 来回平移（幅度 0.55，随 morph 从"贴住"到"分开"）
    vec3  sp = q0 - vec3(0.55 * cos(m * 3.14159), 0.10, 0.0);
    float d = sdSphere(sp, 0.72 * grow);

    // 圆角盒：YZ 截面按 morph 缩放（方 → 扁），并整体轻微倾斜
    float ca = cos(0.45 * m), sa = sin(0.45 * m);
    vec3 rp = vec3(q0.x * ca - q0.z * sa, q0.y, q0.x * sa + q0.z * ca);
    vec3 bsize = vec3(0.55, 0.48 + 0.18 * sin(m * 6.28318), 0.55);
    d = smin(d, sdRoundBox(rp, bsize, 0.20), k);

    // 圆环：绕 X 轴翻转（幅度 0.9 rad，够看出"立起来"但不至于倒平）
    float tb = 0.55 + 0.90 * m;
    float ct = cos(tb), st = sin(tb);
    mat3 rx = mat3(1.0, 0.0, 0.0,
                   0.0, ct, -st,
                   0.0, st, ct);
    d = smin(d, sdTorus(rx * q0, vec2(1.40, 0.26)), k);

    // 地面：加进来而不是 max 求交，几何上完全等价（物体落入地面下时平面更近），
    // 但**不会毁掉远处的步进**——max 版本会让"物体轮廓在地面上的投影"
    // 变成硬边，远处地面细看全是台阶。
    float ground = p.y - GROUND;
    d = min(d, ground);

    // 材质编号：地面用 0.65 的缓坡过渡，避免 1 像素宽的判定抖动。
    // 注意**不能**用 `ground < 0.0001` 判：物体内部 ground 是负的，
    // 那个条件在物体里也成立，会把整块物体染成地面色。
    float id = mix(2.0, 1.0, clamp((ground - 0.03) / 0.65, 0.0, 1.0));
    return vec2(d, id);
}

// ------------------------------------------------------------- 法线/AO --
// eps 随命中距离缩放：固定 eps 在"擦地"的像素上会取到地面/物体两侧的样本，
// 法线直接翻向、边缘出现一条亮线（第一版就是这么翻车的）。
vec3 calcNormal(vec3 p, float t) {
    float e = 0.0012 + 0.0016 * t;
    vec2 h = vec2(e, 0.0);
    return normalize(vec3(
        scene(p + h.xyy).x - scene(p - h.xyy).x,
        scene(p + h.yxy).x - scene(p - h.yxy).x,
        scene(p + h.yyx).x - scene(p - h.yyx).x));
}

float calcAO(vec3 p, vec3 n) {
    float occ = 0.0, sca = 1.0;
    for (int i = 0; i < 5; i++) {
        float h = 0.02 + 0.14 * float(i);
        float d = scene(p + n * h).x;
        occ += (h - d) * sca;
        sca *= 0.80;
    }
    return clamp(1.0 - 1.8 * occ, 0.0, 1.0);
}

float softShadow(vec3 ro, vec3 rd) {
    float res = 1.0, t = 0.06;
    for (int i = 0; i < SHADOW_STEPS; i++) {
        float h = scene(ro + rd * t).x;
        res = min(res, 10.0 * h / t);   // 离遮挡物越近越黑；t 越大半影越宽
        t += clamp(h, 0.05, 0.6);
        if (res < 0.005 || t > 7.0) break;
    }
    return clamp(res, 0.0, 1.0);
}

float checker(vec2 q) {         // 地面棋盘格（解析，不吃贴图）
    vec2 c = floor(q);
    return mod(c.x + c.y, 2.0);
}

void main() {
    // --------- 相机射线 ---------
    vec2 uv = (gl_FragCoord.xy - 0.5 * u_res) / u_res.y;   // 以短边归一化
    vec3 fw = normalize(u_target - u_eye);
    vec3 rt = normalize(cross(vec3(0.0, 1.0, 0.0), fw));
    vec3 up = cross(fw, rt);
    vec3 rd = normalize(uv.x * rt + uv.y * up + 1.55 * fw);  // 1.55 ≈ 焦距

    // --------- 主步进 ---------
    float t = 0.0;
    float id = 0.0;
    for (int i = 0; i < MAX_STEPS; i++) {
        vec3 p = u_eye + rd * t;
        vec2 r = scene(p);
        if (r.x < 0.0016 * t + 0.0006) { id = r.y; break; }
        t += r.x * 0.85;                 // 0.85 是安全系数：宁多走几步也不穿模
        if (t > T_MAX) break;
    }

    // --------- 天空 / 背景 ---------
    // 天空按射线**俯仰角**分层：地平线偏暖、头顶深蓝、往下逐渐变成地面色。
    // 第一版写成 mix(mix(...)) 的纯水平渐变，结果地平线是一条亮白带，
    // 整个画面发灰发白——天空的"上蓝下暖"必须靠 rd.y 才立得住。
    vec3 sky_up = vec3(0.055, 0.105, 0.235);
    vec3 sky_hz = vec3(0.44, 0.42, 0.40);
    vec3 sky_dn = vec3(0.14, 0.125, 0.115);
    float up_t = clamp(rd.y, 0.0, 1.0);
    float dn_t = clamp(-rd.y, 0.0, 1.0);
    vec3 col = mix(sky_hz, sky_up, pow(up_t, 0.55));
    col = mix(col, sky_dn, pow(dn_t, 0.45));
    vec3 sun_dir = normalize(vec3(-0.55, 0.42, 0.70));
    float sdot = max(dot(rd, sun_dir), 0.0);
    col += vec3(1.0, 0.86, 0.62) * pow(sdot, 256.0) * 1.4;  // 太阳盘
    col += vec3(1.0, 0.80, 0.55) * pow(sdot, 6.0) * 0.10;   // 太阳周围辉光

    if (id > 0.5) {
        vec3 p = u_eye + rd * t;
        vec3 n = calcNormal(p, t);
        vec3 base = u_palette;
        if (id > 1.5) {                                  // 地面
            float c = checker(p.xz * 0.55);
            base = mix(vec3(0.11), vec3(0.26), c);
            // 靠近原点的地面染一点主色，让物体与地面有色彩呼应
            base = mix(base, u_palette * 0.40, 0.20 * exp(-0.05 * dot(p.xz, p.xz)));
        }
        float ao = calcAO(p, n);
        float diff = max(dot(n, sun_dir), 0.0);
        float sh = (u_shadow > 0.5) ? softShadow(p + n * 0.02, sun_dir) : 1.0;
        vec3  sky_amb = mix(vec3(0.08, 0.10, 0.16), sky_up, 0.5);
        vec3  lit = base * (sky_amb * ao * 1.5 + vec3(0.98, 0.88, 0.74) * diff * sh * 0.95);
        // 轮廓微光：视线与法线接近垂直处补一点主色，避免边缘糊进背景
        lit += u_palette * pow(1.0 - max(dot(n, -rd), 0.0), 3.0) * 0.10;
        // 指数雾：把远处地面融进天空，藏掉 T_MAX 截断的硬边。
        // 别调太狠——第一版雾在近处就有 2 成强度，整个画面灰成一片。
        float fog = 1.0 - exp(-0.0022 * t * t - 0.02 * t);
        col = mix(lit, col, fog);
    }

    // --------- 曝光 + 色调映射（ACES 近似）+ gamma ---------
    col *= 0.85;
    col = (col * (2.51 * col + 0.03)) / (col * (2.43 * col + 0.59) + 0.14);
    col = pow(clamp(col, 0.0, 1.0), vec3(1.0 / 2.2));
    final_colors = vec4(col, 1.0);
}'''


class RaymarchDemo(DemoApp):
    TITLE = 'SDF 光线步进 · smooth-min 融合 + 软阴影 + AO'
    HELP = ('鼠标拖动 转视角   滚轮 推拉   1/2/3 配色   T 软阴影   A 自动环绕   '
            'SPACE 暂停   ESC 退出')
    DURATION = 32.0
    HUD = True
    # 地面高度：必须与片元着色器里的 GROUND 保持一致（相机取景框按它抬高）
    GROUND_Y = -1.15
    SHOTS = ((3.5, 'docs/demo_raymarch_a.png'),      # morph≈0.35：三块还分得开
             (24.0, 'docs/demo_raymarch_b.png'))     # morph≈0.27 之后的融合态

    # 三套材质主色：氧化铜 / 暖陶 / 冷钢
    PALETTES = ((0.34, 0.72, 0.62), (0.86, 0.48, 0.26), (0.52, 0.60, 0.80))

    def setup(self):
        glDisable(GL_DEPTH_TEST)                    # 全屏片元 pass，不需要深度
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        self.quad = make_fullscreen_quad(self.program)
        self.palette_i = 0
        self.shadow = 1.0
        self.auto_orbit = True
        # 相机：方位角/俯仰角 + 距离（不用 OrbitCamera，因为它把 pitch 钳在
        # 5°~89°，这里希望允许贴到地平线附近，看地面与物体的接触关系）
        self.yaw = 38.0
        self.pitch = 16.0
        self.dist = 11.0
        self.morph = 0.5          # update() 每帧覆盖；先给初值，防"首帧渲染早于 update"

    # ---- 输入 ----
    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.auto_orbit = False                     # 手一拖就停自动环绕
        self.yaw -= dx * 0.32
        self.pitch = max(-6.0, min(78.0, self.pitch + dy * 0.28))

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        self.dist = max(3.2, min(14.0, self.dist - scroll_y * 0.5))

    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol in (key._1, key._2, key._3):
            self.palette_i = symbol - key._1
        elif symbol == key.T:
            self.shadow = 0.0 if self.shadow else 1.0
        elif symbol == key.A:
            self.auto_orbit = not self.auto_orbit

    # ---- 每帧：只更新几个标量，不碰几何 ----
    def update(self, dt):
        if self.auto_orbit:
            self.yaw += dt * 6.5
        # morph 用"0.08..0.88 往返"，而不是整周期 0..1：
        # 两端留出余量，免得截图正好落在"形变到底"的那一帧（第一版 SHOTS
        # 撞在 morph=0.98 上，形状被拉成一长条，两张截图长得几乎一样）。
        # 现在 9.5s → 0.25、23s → 0.79，两张的形态差别一眼能看出来。
        self.morph = 0.48 + 0.40 * math.sin(2.0 * math.pi * self.time / self.DURATION
                                             - 0.5 * math.pi)

    def camera_vectors(self):
        yr, pr = math.radians(self.yaw), math.radians(self.pitch)
        eye = (self.dist * math.cos(pr) * math.cos(yr),
               self.GROUND_Y + 1.15 + self.dist * math.sin(pr),
               self.dist * math.cos(pr) * math.sin(yr))
        target = (0.0, self.GROUND_Y + 1.15, 0.0)
        return eye, target

    def render(self):
        w, h = self.window.width, self.window.height
        eye, target = self.camera_vectors()
        prog = self.program
        prog.use()
        prog['u_res'] = (float(w), float(h))
        prog['u_time'] = self.time
        prog['u_eye'] = eye
        prog['u_target'] = target
        prog['u_palette'] = self.PALETTES[self.palette_i]
        prog['u_shadow'] = self.shadow
        prog['u_morph'] = self.morph
        self.quad.draw(GL_TRIANGLES)

    def status_text(self):
        names = ('氧化铜', '暖陶', '冷钢')
        return (f'{names[self.palette_i]}   morph={self.morph:4.2f}   '
                f'软阴影 {"开" if self.shadow else "关"}   '
                f'eye=({self.yaw:5.1f}°,{self.pitch:4.1f}°,{self.dist:.1f})   '
                f't={self.time:5.2f}s/{self.DURATION:.0f}s   {self.fps:5.1f} fps'
                f'{"  [PAUSED]" if self.paused else ""}')


GROUND_Y = -1.15          # 兜底常量（自检脚本用；权威值是 RaymarchDemo.GROUND_Y）


if __name__ == '__main__':
    run(RaymarchDemo)

# -*- coding: utf-8 -*-
"""kkrieger 致敬场景（core 层）：程序化科幻走廊 + 纪念碑大厅 + 视距雾。

致敬 .kkrieger（.theprodukkt / Farbrausch，2004 Breakpoint 96k 冠军）的
"程序化生成"精神：本场景无任何外部素材——金属板、灯带、灯管、舱门、管道、
货箱、立柱、旋转环、风扇、无人机、全息扫描框、升降平台、能量束全部由代码
即时生成；面板颜色用确定性哈希抖动模拟"程序化纹理"（kkrieger 存的是贴图的
生成史，这里同理只存生成规则）。

另一种"kkrieger 味"来自**运行时开销的反差**：那个 96KB 的可执行文件跑起来
要占约 300MB 内存（贴图与网格都是启动时算出来的），本场景同理——所有几何在
``__init__`` 里现算，磁盘上依然是零素材。

布局（与 logic/kkrieger.py 的 96s 四幕镜头脚本对齐）：

  走廊  z 0 → -56，宽 6，高 4.5：金属板墙 + 立柱 + 天花板灯带（可逐段熄灭）
        + 三道舱门（z = -14 / -32 / -50，双开滑门）+ 管道/货箱 + 琥珀警示频闪
        + 数据流光带（墙面装饰，第五幕突入段跑马提速）
  大厅  z -56 → -76，宽 16，高 9：中央纪念碑（基座 + 五旋转环 + 脉冲核心
        + 贯通天花板的能量束）+ 全息扫描框 + 升降平台 + 顶部大风扇 + 悬浮灯环
        + 爆发碎片（第五幕终局从核心飞散的发光块）

静态几何合并为单个 VertexList（一次 draw call）；
动画对象（环/核心/灯/门/风扇/无人机…）各自独立，model 矩阵与材质逐帧驱动。
"""
import math

import pyglet
from pyglet.gl import (GL_DEPTH_TEST, GL_TRIANGLES, glClearColor, glDisable,
                       glEnable)
from pyglet.graphics import shader
from pyglet.math import Mat4, Vec3

from world3d.core.geometry import box_data
from world3d.engine.geometry import lathe, torus, torus_knot, tube

# ---- 世界常量 ----
CORRIDOR_LEN = 56            # 走廊 z: 0 → -56
CORRIDOR_W, CORRIDOR_H = 6.0, 4.5
ATRIUM_W, ATRIUM_H = 16.0, 9.0
ATRIUM_Z0, ATRIUM_Z1 = -56.0, -76.0
MONUMENT = (0.0, 4.4, -66.0)  # 环/核心枢轴（基座顶面上方）

# 第六幕「反应堆圣殿」：大厅后墙（z=-76）开门洞进去的第二个宏区
SANCTUM_W, SANCTUM_H = 20.0, 12.0
SANCTUM_Z1 = -102.0
PORTAL_HALF_W, PORTAL_H = 2.4, 4.5      # 门洞半宽 / 净高
KNOT = (0.0, 5.0, -89.5)                # 环面结反应堆枢轴
SANCTUM_COL_R = 7.6                     # 凹槽柱环半径

DOOR_Z = (-14.0, -32.0, -50.0)   # 三道舱门
DOOR_HALF_W = 1.45               # 单扇门宽（门洞半宽）
DOOR_SLIDE = 1.75                # 开门时每扇滑移距离（向外侧）
STRIP_Z0, STRIP_ZSTEP, STRIP_N = -2.0, 4.0, 14   # 天花板灯带位置

LIFT_POS = (0.0, -58.0)          # 升降平台中心 (x, z)
LIFT_REST_Y = 0.15
FOG_COLOR = (0.02, 0.025, 0.045)
FOG_NEAR, FOG_FAR = 6.0, 42.0

# ---- 着色器：默认管线 + per-object model 矩阵 + 材质（tint/glow）+ 视距雾 ----
KK_VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 v_color;
out float v_dist;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
uniform vec3 tint;
uniform float glow;
void main() {
    vec4 view_pos = window.view * model * vec4(position, 1.0);
    v_dist = length(view_pos.xyz);
    gl_Position = window.projection * view_pos;
    // tint：整体颜色乘子；glow：自发光叠加（灯带/核心/能量束靠它"亮起来"）
    v_color = vec4(colors.rgb * tint + glow * tint * 0.9, colors.a);
}'''
KK_FRAGMENT_SRC = '''#version 330 core
in vec4 v_color;
in float v_dist;
out vec4 final_colors;
uniform vec3 fog_color;
uniform float fog_near;
uniform float fog_far;
void main() {
    float f = smoothstep(fog_near, fog_far, v_dist);
    final_colors = vec4(mix(v_color.rgb, fog_color, f), v_color.a);
}'''


# ------------------------------------------------------ 程序化纹理工具 --
def _hash01(*args):
    """确定性伪随机 [0,1)——同参数恒定，模拟'生成史'而非存像素。"""
    s = math.sin(sum(a * (61.7 + 29.3 * k) for k, a in enumerate(args, 1))) * 43758.5453
    return s - math.floor(s)


def _metal(i, j, base=(0.30, 0.33, 0.38), var=0.4):
    """金属板色：基准色 ± 哈希亮度抖动。"""
    k = _hash01(i, j)
    return tuple(c * (1.0 - var / 2 + var * k) for c in base)


def _rot_y(verts, ang):
    """绕 Y 轴旋转顶点位置（弧度）。"""
    c, s = math.cos(ang), math.sin(ang)
    out = []
    for i in range(0, len(verts), 3):
        x, y, z = verts[i], verts[i + 1], verts[i + 2]
        out += [x * c + z * s, y, -x * s + z * c]
    return out


def _lerp(a, b, u):
    return a + (b - a) * u


def _translate_mat(x, y, z):
    """列主序平移矩阵（喂 MeshData.transform）。"""
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def _rot_x_90():
    """列主序绕 X 轴 +90°：(x,y,z) → (x,-z,y)，把平躺的环立起来。"""
    return [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1]


class KKRenderer:
    """kkrieger 致敬场景渲染器：程序化几何 + 材质 uniform + 雾效着色器。"""

    def __init__(self):
        self.window = pyglet.window.Window(
            1280, 720, caption='.kkrieger 致敬 — 程序化走廊漫游 (pyglet)',
            resizable=True, vsync=True)
        self.program = shader.ShaderProgram(
            shader.Shader(KK_VERTEX_SRC, 'vertex'),
            shader.Shader(KK_FRAGMENT_SRC, 'fragment'))
        glEnable(GL_DEPTH_TEST)
        glClearColor(*FOG_COLOR, 1.0)   # 清屏色 = 雾色 → 远处无缝融入黑暗

        self._sv, self._sc = [], []     # 静态几何累积缓冲
        self.animated_meta = []         # [(name, base_pos: Vec3)]
        self.animated_lists = []        # [VertexList]
        self.animated_models = []       # [Mat4]
        self.animated_tints = []        # [Vec3]
        self.animated_glows = []        # [float]

        # 顺序要紧：**所有静态几何必须先攒完再上传**（上传后 _sv/_sc 置 None
        # 释放 CPU 缓冲，之后任何 _add_box 都会 TypeError——第一版就栽在这）。
        self._build_corridor()
        self._build_atrium()
        self._build_corridor_props()
        self._build_sanctum_static()
        self.static = self.program.vertex_list(
            len(self._sv) // 3, GL_TRIANGLES,
            position=('f', self._sv), colors=('f', self._sc))
        self._sv = self._sc = None      # 已上传 GPU，释放 CPU 缓冲
        # 动画对象（各自独立顶点列表 + 逐帧 model/材质）
        self._build_monument()
        self._build_doors()
        self._build_fans()
        self._build_drones()
        self._build_atrium_show()
        self._build_act5()
        self._build_sanctum_show()

    # ---------------------------------------------------- 静态几何 --
    def _add_box(self, sx, sy, sz, ox, oy, oz, rgb):
        v, c = box_data(sx, sy, sz, ox, oy, oz, rgb)
        self._sv += v
        self._sc += c

    @staticmethod
    def _md_vc(md, color_fn):
        """MeshData → 扁平 (verts, colors)，逐顶点过 color_fn(x,y,z)。"""
        m = md.to_non_indexed() if md.indices else md
        verts = list(m.positions)
        colors = []
        for i in range(0, len(verts), 3):
            colors += list(color_fn(verts[i], verts[i + 1], verts[i + 2])) + [1.0]
        return verts, colors

    def _add_mesh(self, md, color_fn):
        """把一份 MeshData（新几何类型）并入静态几何缓冲。"""
        v, c = self._md_vc(md, color_fn)
        self._sv += v
        self._sc += c

    def _build_corridor(self):
        W, H, L = CORRIDOR_W, CORRIDOR_H, CORRIDOR_LEN
        n = int(L // 2)
        # 地板 + 天花板（2x2 金属板，稀有琥珀警戒板）
        for iz in range(n):
            zc = -iz * 2 - 1
            for ix in range(3):
                xc = -W / 2 + ix * 2 + 1
                rgb = _metal(ix, iz, (0.16, 0.18, 0.22), 0.5)
                if _hash01(ix, iz) > 0.94:
                    rgb = (0.45, 0.28, 0.08)
                self._add_box(2.0, 0.2, 2.0, xc, -0.1, zc, rgb)
                self._add_box(2.0, 0.2, 2.0, xc, H - 0.1, zc,
                              _metal(ix, iz + 40, (0.12, 0.13, 0.16), 0.4))
        # 两侧墙板（3 层 x n 列，随机深色通风格栅）
        for side in (-1, 1):
            for iz in range(n):
                zc = -iz * 2 - 1
                for row in range(3):
                    yc = 0.75 + row * 1.5
                    if _hash01(iz * 3 + row, side) > 0.88:
                        rgb = (0.08, 0.09, 0.11)
                    else:
                        rgb = _metal(iz, row * 7 + side, (0.26, 0.29, 0.34), 0.45)
                    self._add_box(0.2, 1.5, 2.0, side * (W / 2 + 0.1), yc, zc, rgb)
            # 琥珀装饰线 + 立柱（含琥珀标记条）
            self._add_box(0.14, 0.1, L, side * (W / 2 + 0.05), 3.38, -L / 2,
                          (0.75, 0.42, 0.10))
            for k in range(7):
                zc = -4 - k * 8
                self._add_box(0.7, H, 0.9, side * (W / 2 - 0.35), H / 2, zc,
                              (0.34, 0.37, 0.43))
                self._add_box(0.74, 0.12, 0.22, side * (W / 2 - 0.35), 3.2, zc,
                              (0.85, 0.50, 0.12))
        # 天花板灯带改为**动画对象**（第四幕逐段熄灭），见 _build_corridor_props
        # 舱门门框（门扇同样是动画对象）
        for dz in DOOR_Z:
            for side in (-1, 1):
                self._add_box(0.5, H, 0.6, side * (DOOR_HALF_W + 0.25), H / 2, dz,
                              (0.30, 0.33, 0.39))
            self._add_box(2 * DOOR_HALF_W + 1.0, 0.5, 0.6, 0, H - 0.25, dz,
                          (0.30, 0.33, 0.39))
            self._add_box(2 * DOOR_HALF_W + 1.0, 0.14, 0.64, 0, 3.45, dz,
                          (0.80, 0.46, 0.12))

    def _build_atrium(self):
        W, H = ATRIUM_W, ATRIUM_H
        z0, z1 = ATRIUM_Z0, ATRIUM_Z1
        D = z0 - z1
        wall = (0.18, 0.20, 0.24)
        # 地板 + 天花板
        for ix in range(8):
            for iz in range(10):
                self._add_box(2.0, 0.2, 2.0, -W / 2 + ix * 2 + 1, -0.1,
                              z0 - iz * 2 - 1,
                              _metal(ix, iz + 9, (0.15, 0.17, 0.21), 0.5))
        for ix in range(4):
            for iz in range(5):
                self._add_box(4.0, 0.2, 4.0, -W / 2 + ix * 4 + 2, H - 0.1,
                              z0 - iz * 4 - 2,
                              _metal(ix, iz + 77, (0.10, 0.11, 0.14), 0.4))
        # 后墙（留圣殿门洞 |x| < PORTAL_HALF_W、高 PORTAL_H）/ 侧墙 / 前墙
        for xa, xb in ((-W / 2 - 0.15, -PORTAL_HALF_W), (PORTAL_HALF_W, W / 2 + 0.15)):
            self._add_box(xb - xa, PORTAL_H, 0.3, (xa + xb) / 2, PORTAL_H / 2,
                          z1 - 0.15, wall)
        self._add_box(W, H - PORTAL_H, 0.3, 0, PORTAL_H + (H - PORTAL_H) / 2,
                      z1 - 0.15, wall)
        for row in range(2):
            yc = 2.25 + row * 4.5
            self._add_box(0.3, 4.5, D, -W / 2 - 0.15, yc, (z0 + z1) / 2, wall)
            self._add_box(0.3, 4.5, D, W / 2 + 0.15, yc, (z0 + z1) / 2, wall)
        # 圣殿门洞的门框（琥珀包边，提示"这里可以进去"）
        for side in (-1, 1):
            self._add_box(0.3, PORTAL_H, 0.4, side * (PORTAL_HALF_W + 0.1),
                          PORTAL_H / 2, z1 - 0.15, (0.62, 0.50, 0.30))
        self._add_box(2 * PORTAL_HALF_W + 0.7, 0.18, 0.4, 0,
                      PORTAL_H + 0.09, z1 - 0.15, (0.85, 0.50, 0.12))
        for xa, xb in ((-W / 2 - 0.15, -3.5), (3.5, W / 2 + 0.15)):
            self._add_box(xb - xa, 4.5, 0.3, (xa + xb) / 2, 2.25, z0 - 0.15, wall)
        self._add_box(W, H - 4.5, 0.3, 0, 4.5 + (H - 4.5) / 2, z0 - 0.15, wall)
        # 六根立柱（柱身 + 琥珀顶环 + 基座）
        for px, pz in ((-5.5, -60), (5.5, -60), (-5.5, -72), (5.5, -72),
                       (-7.0, -66), (7.0, -66)):
            self._add_box(1.1, H, 1.1, px, H / 2, pz, (0.34, 0.37, 0.43))
            self._add_box(1.3, 0.18, 1.3, px, H - 0.5, pz, (0.75, 0.42, 0.10))
            self._add_box(1.4, 0.4, 1.4, px, 0.2, pz, (0.22, 0.24, 0.28))
        # 大厅灯环改为动画对象（见 _build_atrium_show）；纪念碑基座仍静态
        self._add_box(3.2, 1.4, 3.2, 0, 0.7, -66, (0.30, 0.33, 0.38))
        self._add_box(3.6, 0.14, 3.6, 0, 1.47, -66, (0.85, 0.50, 0.12))
        # 升降平台导轨（平台本体是动画对象）
        for side in (-1, 1):
            self._add_box(0.3, H - 1.0, 0.3, side * 1.8, (H - 1.0) / 2, -58.0,
                          (0.30, 0.33, 0.39))

    # ---------------------------------------------------- 动画对象 --
    def _push_animated(self, name, base, verts, colors, tint=None, glow=0.0):
        """注册动画对象：局部坐标（枢轴在原点），位置/材质由时间轴提供。"""
        self.animated_meta.append((name, base))
        self.animated_lists.append(self.program.vertex_list(
            len(verts) // 3, GL_TRIANGLES,
            position=('f', verts), colors=('f', colors)))
        self.animated_models.append(Mat4())
        self.animated_tints.append(Vec3(*(tint or (1.0, 1.0, 1.0))))
        self.animated_glows.append(glow)

    def _merge(self, parts):
        """把 [(尺寸, 偏移, 颜色), ...] 合并成一份 (verts, colors)。"""
        verts, colors = [], []
        for dims, off, rgb in parts:
            v, c = box_data(dims[0], dims[1], dims[2], off[0], off[1], off[2], rgb)
            verts += v
            colors += c
        return verts, colors

    def _build_monument(self):
        cx, cy, cz = MONUMENT
        # 五旋转环（八边形环段拼接，局部原点在环心）
        for i, (R, segs) in enumerate(((2.70, 14), (2.25, 12), (1.85, 10),
                                       (1.45, 8), (1.05, 6))):
            verts, colors = [], []
            for k in range(segs):
                ang = 2 * math.pi * k / segs
                rgb = _metal(k, i * 3 + 1, (0.55, 0.38, 0.16), 0.5)
                sv, sc = box_data(2 * math.pi * R / segs * 0.72, 0.28, 0.28,
                                  0, 0, -R, rgb)
                verts += _rot_y(sv, ang)
                colors += sc
            self._push_animated(f'monument_ring_{i}', Vec3(cx, cy, cz),
                                verts, colors)
        # 脉冲核心（琥珀发光体）
        v, c = box_data(1.0, 1.0, 1.0, 0, 0, 0, (1.0, 0.62, 0.16))
        self._push_animated('monument_core', Vec3(cx, cy, cz), v, c,
                            tint=(1.0, 0.72, 0.30), glow=0.25)
        # 能量束：从核心贯通到天花板（细长方柱，局部原点在核心）
        beam_h = ATRIUM_H - MONUMENT[1]
        v, c = box_data(0.34, beam_h, 0.34, 0, beam_h / 2, 0, (1.0, 0.70, 0.30))
        self._push_animated('beam', Vec3(cx, cy, cz), v, c,
                            tint=(1.0, 0.66, 0.26), glow=0.15)

    def _build_doors(self):
        """三道双开滑门：每扇门局部原点在门洞中心，靠平移通道向两侧滑开。"""
        for i, dz in enumerate(DOOR_Z):
            for tag, sign in (('a', -1), ('b', 1)):
                v, c = self._merge([
                    ((DOOR_HALF_W - 0.05, 3.2, 0.26), (sign * DOOR_HALF_W / 2, 1.75, 0),
                     (0.42, 0.45, 0.51)),
                    ((DOOR_HALF_W - 0.05, 0.16, 0.30), (sign * DOOR_HALF_W / 2, 3.28, 0),
                     (0.85, 0.50, 0.12)),
                    ((0.20, 0.55, 0.30), (sign * (DOOR_HALF_W - 0.4), 1.75, 0),
                     (0.30, 0.33, 0.39)),
                ])
                self._push_animated(f'door{i}_{tag}', Vec3(0.0, 0.0, dz), v, c)

    def _build_corridor_props(self):
        """走廊道具：天花板灯带（可逐段熄灭）、琥珀频闪灯、管道、货箱。"""
        # 天花板灯带：14 段，逐段可控（第四幕撤离时依次熄灭）
        for i in range(STRIP_N):
            z = STRIP_Z0 - i * STRIP_ZSTEP
            v, c = box_data(1.4, 0.12, 1.7, 0, 0, 0, (0.90, 0.96, 1.0))
            self._push_animated(f'strip_{i}', Vec3(0.0, CORRIDOR_H - 0.32, z),
                                v, c, tint=(1.0, 1.0, 1.0), glow=1.15)
        # 琥珀频闪灯：两侧墙各 4 盏
        for i in range(8):
            side = -1 if i % 2 == 0 else 1
            z = -6.0 - 6.5 * i
            v, c = box_data(0.16, 0.30, 0.46, 0, 0, 0, (1.0, 0.58, 0.16))
            self._push_animated(f'strobe_{i}',
                                Vec3(side * (CORRIDOR_W / 2 - 0.08), 3.05, z),
                                v, c, tint=(1.0, 0.62, 0.22), glow=0.0)
        # 管道：两侧沿墙两条 + 每 12 单位一处竖向下垂
        for side in (-1, 1):
            for k, (y, rad) in enumerate(((3.95, 0.16), (3.62, 0.10))):
                rgb = (0.30, 0.33, 0.38) if k == 0 else (0.42, 0.32, 0.20)
                self._add_box(0.24, rad * 2, CORRIDOR_LEN,
                              side * (CORRIDOR_W / 2 - 0.30), y,
                              -CORRIDOR_LEN / 2, rgb)
            for k in range(4):
                z = -8.0 - 13.0 * k
                self._add_box(0.22, 0.85, 0.22, side * (CORRIDOR_W / 2 - 0.30),
                              3.55, z, (0.36, 0.39, 0.45))
        # 货箱：贴墙堆叠（确定性哈希决定高度与颜色），给走廊"有人用过"的痕迹
        for i in range(14):
            side = -1 if i % 2 == 0 else 1
            z = -5.0 - 3.7 * i
            h = 0.7 + 0.5 * _hash01(i, 3)
            rgb = (0.36, 0.30, 0.22) if _hash01(i, 9) > 0.5 else (0.26, 0.30, 0.34)
            self._add_box(0.9, h, 1.3, side * (CORRIDOR_W / 2 - 0.75), h / 2, z, rgb)
            if _hash01(i, 17) > 0.55:
                self._add_box(0.7, 0.6, 0.9, side * (CORRIDOR_W / 2 - 0.7),
                              h + 0.3, z + 0.1, (0.32, 0.28, 0.22))

    def _build_fans(self):
        # 侧壁风扇：两片正交桨叶 + 轮毂，绕 X 轴自旋（左右墙交替）
        for i in range(4):
            side = -1 if i % 2 == 0 else 1
            base = Vec3(side * 2.72, 2.3, -8 - 12 * i)
            verts, colors = [], []
            for dims in ((0.09, 1.5, 0.15), (0.09, 0.15, 1.5)):
                sv, sc = box_data(*dims, 0, 0, 0, (0.45, 0.48, 0.53))
                verts += sv
                colors += sc
            sv, sc = box_data(0.18, 0.34, 0.34, 0, 0, 0, (0.20, 0.22, 0.26))
            verts += sv
            colors += sc
            self._push_animated(f'fan_{i}', base, verts, colors)

    def _build_drones(self):
        # 巡逻无人机：机身 + 十字旋翼 + 琥珀前灯（三架，走廊两架 + 大厅一架）
        for i in range(3):
            verts, colors = self._merge((
                ((0.7, 0.22, 0.95), (0, 0, 0), (0.38, 0.41, 0.46)),
                ((1.5, 0.05, 0.12), (0, 0.16, 0), (0.55, 0.58, 0.62)),
                ((0.12, 0.05, 1.5), (0, 0.16, 0), (0.55, 0.58, 0.62)),
                ((0.24, 0.12, 0.1), (0, 0, -0.55), (1.0, 0.55, 0.10)),
            ))
            self._push_animated(f'drone_{i}', Vec3(0, 2.6, -4 - 20 * i), verts,
                                colors, tint=(0.95, 0.98, 1.0), glow=0.10)

    def _build_atrium_show(self):
        """大厅演出件：全息扫描框、升降平台、顶部大风扇、悬浮灯环。"""
        cx, cy, cz = MONUMENT
        # 全息扫描框：4 根细杆组成方框（局部原点在框心），上下扫描
        half = 3.0
        v, c = self._merge((
            ((2 * half + 0.24, 0.10, 0.10), (0, half, 0), (0.35, 0.85, 1.0)),
            ((2 * half + 0.24, 0.10, 0.10), (0, -half, 0), (0.35, 0.85, 1.0)),
            ((0.10, 0.10, 2 * half), (half, 0, 0), (0.35, 0.85, 1.0)),
            ((0.10, 0.10, 2 * half), (-half, 0, 0), (0.35, 0.85, 1.0)),
        ))
        self._push_animated('holo_frame', Vec3(cx, cy, cz), v, c,
                            tint=(0.45, 0.90, 1.0), glow=0.55)
        # 升降平台：平台面 + 护栏 + 琥珀边条（局部原点在台面）
        v, c = self._merge((
            ((3.4, 0.25, 3.4), (0, 0, 0), (0.34, 0.37, 0.44)),
            ((3.5, 0.08, 3.5), (0, 0.16, 0), (0.85, 0.50, 0.12)),
            ((3.4, 0.5, 0.12), (0, 0.4, -1.6), (0.42, 0.46, 0.52)),
        ))
        self._push_animated('lift', Vec3(LIFT_POS[0], LIFT_REST_Y, LIFT_POS[1]),
                            v, c)
        # 顶部大风扇：轮毂 + 四片桨叶（绕 Y 慢转）
        verts, colors = self._merge((((0.6, 0.4, 0.6), (0, 0, 0), (0.30, 0.33, 0.39)),))
        for k in range(4):
            sv, sc = box_data(0.22, 0.10, 4.2, 0, 0, 0, (0.40, 0.43, 0.49))
            verts += _rot_y(sv, 2 * math.pi * k / 4)
            colors += sc
        self._push_animated('atrium_fan', Vec3(0.0, ATRIUM_H - 0.6, -70.0),
                            verts, colors)
        # 悬浮灯环：4 盏（第四幕熄灭）
        for i, (lx, lz) in enumerate(((-5, -61), (5, -61), (-5, -71), (5, -71))):
            v, c = box_data(1.5, 0.12, 1.5, 0, 0, 0, (0.90, 0.96, 1.0))
            self._push_animated(f'alight_{i}', Vec3(lx, ATRIUM_H - 0.35, lz),
                                v, c, glow=1.0)

    def _build_sanctum_static(self):
        """第六幕「反应堆圣殿」静态壳：全新空间 + 全新几何。

        与走廊/大厅的区别不止布局——这里的构件类型是全新的：
        - 柱不再是盒子拼的，而是 lathe 凹槽旋转柱（一条轮廓线决定全部形状，
          逐顶点做"锈蚀青铜"斑驳着色）；
        - 地面嵌一道真正的圆环（torus 网格，不是方块近似的八边形环）。
        """
        W, H = SANCTUM_W, SANCTUM_H
        z0, z1 = ATRIUM_Z1, SANCTUM_Z1       # z -76 → -102
        wall = (0.15, 0.17, 0.21)
        # 地板 + 天花板（大板，青色稀有板代替琥珀——圣殿的色谱）
        for ix in range(int(W // 2)):
            for iz in range(int((z0 - z1) // 2)):
                xc, zc = -W / 2 + ix * 2 + 1, z0 - iz * 2 - 1
                rgb = _metal(ix, iz + 31, (0.13, 0.15, 0.20), 0.5)
                if _hash01(ix + 5, iz + 5) > 0.95:
                    rgb = (0.10, 0.30, 0.38)
                self._add_box(2.0, 0.2, 2.0, xc, -0.1, zc, rgb)
                self._add_box(2.0, 0.2, 2.0, xc, H - 0.1, zc,
                              _metal(ix, iz + 90, (0.09, 0.10, 0.13), 0.4))
        # 侧墙（x=±10）+ 远墙（z=-102），前墙 = 大厅后墙（共用，门洞已留）
        for row in range(2):
            yc = 3.0 + row * 6.0
            self._add_box(0.3, 6.0, z0 - z1, -W / 2 - 0.15, yc, (z0 + z1) / 2, wall)
            self._add_box(0.3, 6.0, z0 - z1, W / 2 + 0.15, yc, (z0 + z1) / 2, wall)
            self._add_box(W, 6.0, 0.3, 0, yc, z1 - 0.15, wall)
        # 八根 lathe 凹槽旋转柱（新几何：轮廓曲线绕轴旋转）
        col_profile = ((1.05, 0.0), (1.20, 0.12), (1.10, 0.55), (0.78, 1.05),
                       (0.66, 3.0), (0.70, 5.6), (0.66, 8.2), (0.80, 10.2),
                       (1.06, 10.9), (1.14, 11.2))
        for k in range(8):
            # 起始角偏 π/8：不偏的话有一根柱子正好压在门洞→反应堆的中轴上，
            # 118.2s 的揭幕帧被柱子糊满屏（实测踩坑）
            ang = math.pi / 8 + 2 * math.pi * k / 8
            cx = KNOT[0] + math.cos(ang) * SANCTUM_COL_R
            cz = KNOT[2] + math.sin(ang) * SANCTUM_COL_R
            md = lathe(col_profile, segments=20, closed_bottom=True)

            def bronze(x, y, z, k=k):
                # 锈蚀青铜：逐顶点斑驳 + 柱身凹槽明暗（按经角量化，
                # 用**局部**坐标算——移到世界坐标后再算经角就全歪了）
                th = math.atan2(z, x)
                flute = 0.82 + 0.18 * math.cos(th * 10)        # 凹槽纹
                k0 = _hash01(int(x * 7) + 31, int(y * 5) + 17, k)
                m = flute * (0.82 + 0.36 * k0)
                return (0.46 * m, 0.34 * m, 0.20 * m)

            v, c = self._md_vc(md, bronze)
            for i in range(0, len(v), 3):      # 平移到柱位（局部→世界）
                v[i] += cx
                v[i + 2] += cz
            self._sv += v
            self._sc += c
        # 地面发光嵌环（新几何：torus 网格平躺，不是八边形环拼的）
        inlay = torus(major=5.2, minor=0.09, major_segs=96, minor_segs=8)
        inlay.transform(_translate_mat(KNOT[0], 0.06, KNOT[2]))

        def cyan(x, y, z):
            return (0.20, 0.62, 0.85)

        self._add_mesh(inlay, cyan)

    def _build_sanctum_show(self):
        """第六幕演出件：环面结反应堆 + 悬浮种子 + 导管 + 门洞光环。

        全部是此前场景里不存在的元素类型：
        - ``knot``：(2,3) 环面结反应堆（torus_knot 网格），自旋 + 点火过载；
        - ``seed_i``：lathe 卵形悬浮种子（绕反应堆公转/收拢献祭）；
        - ``sconduit_i``：tube 导管（天花板蜿蜒注入反应堆，平行传输标架）；
        - ``portal_ring``：门洞光环（lathe 旋转环，穿越时高亮）。
        """
        # 反应堆：琥珀金属的 (2,3) 环面结
        md = torus_knot(2, 3, major=2.3, tube_r=0.36, tubular=192, radial=12)

        def knot_c(x, y, z, _md=md):
            th = math.atan2(z, x)
            k0 = _hash01(int(x * 3), int(y * 3), int(z * 3))
            m = 0.85 + 0.3 * k0
            return (0.72 * m, 0.48 * m, 0.24 * m)

        v, c = self._md_vc(md, knot_c)
        self._push_animated('knot', Vec3(*KNOT), v, c,
                            tint=(1.0, 0.72, 0.35), glow=0.18)
        # 悬浮种子：lathe 卵形体（6 颗，公转）
        seed_profile = ((0.0, 0.0), (0.26, 0.05), (0.40, 0.30),
                        (0.34, 0.62), (0.18, 0.85), (0.0, 0.95))
        for i in range(6):
            md = lathe(seed_profile, segments=16)
            v, c = self._md_vc(md, lambda x, y, z: (0.35, 0.80, 0.95))
            self._push_animated(f'seed_{i}', Vec3(*KNOT), v, c,
                                tint=(0.55, 0.90, 1.0), glow=0.35)
        # 导管：天花板四角蜿蜒注入反应堆（tube 扫掠，新几何）
        for i in range(4):
            ang = math.pi / 4 + math.pi / 2 * i
            x0, z0 = KNOT[0] + math.cos(ang) * 8.2, KNOT[2] + math.sin(ang) * 8.2
            pts = [(x0, SANCTUM_H - 0.2, z0)]
            for s in range(1, 7):
                u = s / 7
                # 悬链式下坠：先沿墙走，再向结心弯曲
                x = _lerp(x0, KNOT[0] + math.cos(ang) * 1.3, u ** 1.5)
                z = _lerp(z0, KNOT[2] + math.sin(ang) * 1.3, u ** 1.5)
                y = (SANCTUM_H - 0.2) * (1.0 - u ** 0.8) + 6.2 * u ** 0.8 \
                    - 1.2 * math.sin(math.pi * u)
                pts.append((x, y, z))
            md = tube(pts, 0.14, 8)
            v, c = self._md_vc(md, lambda x, y, z: (0.62, 0.44, 0.20))
            self._push_animated(f'sconduit_{i}', Vec3(0.0, 0.0, 0.0), v, c,
                                tint=(0.95, 0.70, 0.30), glow=0.35)
        # 门洞光环：lathe 旋转环立在门洞里（穿越时高亮）
        ring_profile = ((1.90, -0.10), (2.04, -0.06), (2.09, 0.0),
                        (2.04, 0.06), (1.90, 0.10), (1.78, 0.0), (1.90, -0.10))
        md = lathe(ring_profile, segments=48)
        md.transform(_rot_x_90())
        v, c = self._md_vc(md, lambda x, y, z: (0.30, 0.75, 0.95))
        self._push_animated('portal_ring', Vec3(0.0, 2.25, -76.0),
                            v, c, tint=(0.45, 0.90, 1.0), glow=0.45)

    def _build_act5(self):
        """第五幕演出件：数据流光带（走廊）+ 爆发碎片（大厅核心）。"""
        # 数据流光带：贴墙低位的青色发光条，跑马节奏由时间轴驱动
        for i in range(6):
            side = -1 if i % 2 == 0 else 1
            z = -6.0 - 9.0 * i
            v, c = box_data(0.10, 0.18, 4.2, 0, 0, 0, (0.35, 0.85, 1.0))
            self._push_animated(f'conduit_{i}',
                                Vec3(side * (CORRIDOR_W / 2 - 0.06), 0.62, z),
                                v, c, tint=(0.35, 0.85, 1.0), glow=0.45)
        # 爆发碎片：8 个小发光块，枢轴在核心（弹道平移由时间轴给出）
        for i in range(8):
            v, c = box_data(0.16, 0.16, 0.16, 0, 0, 0, (1.0, 0.62, 0.16))
            self._push_animated(f'spark_{i}', Vec3(*MONUMENT), v, c,
                                tint=(1.0, 0.70, 0.30), glow=0.0)

    # ---------------------------------------------------- 渲染 --
    def set_animated_model(self, index, matrix):
        self.animated_models[index] = matrix

    def set_animated_material(self, index, tint, glow):
        self.animated_tints[index] = tint
        self.animated_glows[index] = float(glow)

    def apply_projection(self):
        w = self.window
        # viewport 传逻辑尺寸——pyglet 2.1.16 的 setter 内部会乘 DPI scale
        w.viewport = (0, 0, w.width, w.height)
        w.projection = Mat4.perspective_projection(
            w.aspect_ratio, z_near=0.1, z_far=150, fov=62)

    # ---- 2D UI 屏幕空间切换（字幕/时间轴面板用，与 WorldRenderer 同一套约定） ----
    def begin_ui(self):
        """切入 UI 状态：正交投影 + 关深度测试（视图矩阵存档待还原）。

        深度测试必须关闭——3D 场景写入的深度缓冲会剔除 UI 的 z=0 片元，
        表现为面板/字幕被场景"掏空"。
        """
        w, h = self.window.width, self.window.height
        self._saved_view = self.window.view
        self.window.projection = Mat4.orthogonal_projection(0, w, 0, h, -255, 255)
        self.window.view = Mat4()
        glDisable(GL_DEPTH_TEST)

    def end_ui(self):
        """还原 3D 状态：深度测试 + 透视投影 + 存档的相机视图矩阵。"""
        glEnable(GL_DEPTH_TEST)
        self.apply_projection()
        self.window.view = getattr(self, '_saved_view', Mat4())

    def draw(self):
        self.window.clear()
        prog = self.program
        prog.use()
        prog['fog_color'] = FOG_COLOR
        prog['fog_near'] = FOG_NEAR
        prog['fog_far'] = FOG_FAR
        prog['model'] = Mat4()
        prog['tint'] = Vec3(1, 1, 1)
        prog['glow'] = 0.0
        self.static.draw(GL_TRIANGLES)
        for vl, model, tint, glow in zip(self.animated_lists, self.animated_models,
                                         self.animated_tints, self.animated_glows):
            prog['model'] = model
            prog['tint'] = tint
            prog['glow'] = glow
            vl.draw(GL_TRIANGLES)

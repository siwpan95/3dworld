# -*- coding: utf-8 -*-
"""太阳系：层级变换（节点树世界矩阵继承）+ 压缩轨道力学 + 实例化小行星带。

演示三件事，每件都对应一类真实工程问题：

1. **层级变换**：月球绕地球、地球绕太阳。用 ``logic/skeleton.py`` 的
   ``Skeleton`` 节点树表达——``BoneNode.local_matrix()`` 只存"相对父节点"的
   TRS，``Skeleton.compute()`` 按注册序自根向叶算 ``world = parent.world @ local``。
   于是**只需要**每帧写"行星绕太阳的公转平移"与"自身自转"，月球的世界坐标
   自动跟着地球走，一行代码都不用为月球的位置做加法::

        sun ── planet_i ── moon
                  └────── ring（土星环，只继承倾角、不继承自转）

   注意 ``Skeleton.compute()`` 的约定：父节点必须先注册（注册序 = 拓扑序），
   所以 build 阶段是先 sun、后 8 颗行星、最后才是月球/土星环。

2. **压缩轨道力学**：真实周期比例（Mercury 88d : Neptune 60190d ≈ 1 : 684）
   按"统一时间缩放"压到 60s 演示长度内——即 ``angle = 2π * t / T_i``，
   ``T_i`` 由真实公转周期乘同一个系数得到。60s 内水星转 ≈8.0 圈、地球 ≈3.15 圈、
   海王星 ≈0.16 圈。**绝不能让每颗行星各自"转够整数圈"**，那样比例就废了。

3. **实例化渲染**：小行星带 420 块用 ``ShaderProgram.vertex_list_instanced``
   （pyglet 2.1.16 原生支持，不要手搓 VAO/VBO）。每块只上传 3 个 vec4 的
   *静态*参数（轨道半径/初相/高度/角速度、尺寸、颜色对），**位置在顶点着色器里
   用 u_time 现算**——所以逐帧 CPU 侧一个实例属性都不用碰。

踩过的坑（pyglet 2.1.16 实测）：

- ``vertex_list_instanced`` 的 ``instance_attributes`` 名字**必须**同时出现在
  ``**data`` 里并给出初始数组，否则 ``_vertex_list_create`` 里那份属性字典的
  ``'instance'`` 仍是 ``False``，实例属性会被当普通顶点属性建 buffer →
  画出来全黑（没有任何报错，最坑的一种失败）。
- 初始数组长度按**顶点数 × 分量数**校验（``set_attribute_data`` 用 ``self.count``），
  所以传一个顶点长度的占位数组即可，真实数据由 ``add_instance`` 逐条写入。
- 域初始 ``_instances = 1``，每次 ``add_instance`` 才 +1；想画 N 个就要调 N 次
  （``add_instance`` 返回的 ``VertexInstance`` 可以 ``inst.i_offset = (...)`` 单条改，
  本 demo 用不到，但 water_waves.py 的浮标不需要它、因为浮标是普通顶点列表）。
- 顶点属性上限（GL_MAX_VERTEX_ATTRIBS）通常 16，实例属性也算在内；
  这里 2 个基础 + 3 个实例 = 5 个，离上限很远，别为了省事猛加。

用法::

    python demos/solar_system.py
    python demos/solar_system.py --smoke 3
    python demos/solar_system.py --shot

按键：1..8 聚焦行星 / 0 全景 / = 拉近 - 拉远 / M 月球开关
     鼠标拖动环绕、滚轮缩放、SPACE 暂停、ESC 退出。
"""
import math
import random

import pyglet
from pyglet.gl import GL_BLEND, GL_DEPTH_TEST, GL_ONE_MINUS_SRC_ALPHA, GL_SRC_ALPHA, \
    GL_TRIANGLES, glBlendFunc, glClearColor, glDepthMask, glEnable
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Mat4, Vec3

from _harness import DemoApp, orbit_camera_input, run
from logic.animation import quat_from_axis_angle, quat_to_mat4
from logic.skeleton import Skeleton

# 统一时间缩放：真实 1 年（365.25 天）压缩成 19.05 秒。
# 于是水星(0.2408y)→4.59s 一圈（60s 内 13 圈）、地球→19.05s（3.1 圈）、
# 海王星(164.8y)→3139s（60s 内 0.019 圈，几乎原地）。
# 取 19.05 是"让地球的周期落在 1/3 个演示长度"这个可读的观感档位。
ORBIT_TIME_SCALE = 19.05
DURATION = 60.0

# --------------------------------------------------------------- 几何 --
def uv_sphere(radius, stacks=18, slices=24, rgb=(0.7, 0.7, 0.75), _shade=True, bands=0):
    """程序化 UV 球：返回 (verts, colors)，均为扁平 float 列表（GL_TRIANGLES）。

    - 位置用标准球面参数化：纬度 theta ∈ [0, π]（从 +Y 极到 -Y 极），
      经度 phi ∈ [0, 2π]；极点处三角形退化成"两个顶点重合"的退化三角形，
      留着无害（共 2 * slices 个），省掉特判能让代码短一半。
    - 顶点颜色存的是**材质色**（每面一个基色 + 云带调制），**不含明暗**。
      明暗由片元着色器按"世界位置相对球心的径向 = 法线"逐片元算真漫反射。
      早期版本照 ``core/geometry.py`` 的 ``_face_shade`` 把明暗烘进顶点色，
      但那个做法在**自转**的球上会露馅：烘出来的明暗是"对象空间"的，
      随 model 矩阵一起转，于是昼夜线跟着天体一起自转（太阳永远照在同一面）。
      半径小的球和立方体看不出差别，球体一转就很明显。
    - ``bands`` 是"纬度云带"数量：沿纬度做 ``sin`` 条带调制亮度，
      给气态巨行星（木星/土星）一点可辨认的条带。只动顶点颜色，顶点数不变，
      所以仍是"setup 阶段一次成型、逐帧零重建"。
    - ``_shade`` 保留签名兼容（旧调用点传 shade=False 表示"自发光"），
      现在明暗全在片元着色器里算，这个参数不再影响 CPU 侧数据。
      顺带的坑：函数里的 ``shade`` 参数会和 ``ring_band`` 的同名参数
      "看起来是一回事"——两处语义不同（一个是自发光开关，一个是环的明暗系数），
      所以这里改名加下划线前缀，避免读代码时误判。
    """
    verts, colors = [], []
    for i in range(stacks):
        th0 = math.pi * i / stacks
        th1 = math.pi * (i + 1) / stacks
        s0, c0 = math.sin(th0), math.cos(th0)
        s1, c1 = math.sin(th1), math.cos(th1)
        for j in range(slices):
            p0 = 2.0 * math.pi * j / slices
            p1 = 2.0 * math.pi * (j + 1) / slices
            q = ((radius * s0 * math.cos(p0), radius * c0, radius * s0 * math.sin(p0)),
                 (radius * s1 * math.cos(p0), radius * c1, radius * s1 * math.sin(p0)),
                 (radius * s1 * math.cos(p1), radius * c1, radius * s1 * math.sin(p1)),
                 (radius * s0 * math.cos(p1), radius * c0, radius * s0 * math.sin(p1)))
            k = 1.0
            if bands:
                mid_y = sum(p[1] for p in q) / (4.0 * radius)
                stripe = 0.5 + 0.5 * math.sin(mid_y * bands * math.pi)
                k = 0.70 + 0.45 * stripe
            for p in (q[0], q[1], q[2], q[0], q[2], q[3]):
                verts += [p[0], p[1], p[2]]
                colors += [rgb[0] * k, rgb[1] * k, rgb[2] * k, 1.0]
    return verts, colors


def ring_band(r_in, r_out, segments=96, rgb=(0.78, 0.70, 0.52), alpha=1.0, shade=0.86):
    """土星环：XZ 平面上的扁环带（上下两面 + 内外侧壁），36 顶点/扇区。

    必须给厚度（这里 0.012）而不是纯平面——纯平面在掠射角下会闪 z-fighting，
    而且侧壁能挡住"从环下面看到环"的穿帮。
    """
    th = 0.012
    verts, colors = [], []
    for j in range(segments):
        a0 = 2.0 * math.pi * j / segments
        a1 = 2.0 * math.pi * (j + 1) / segments
        c0, s0 = math.cos(a0), math.sin(a0)
        c1, s1 = math.cos(a1), math.sin(a1)
        inner0 = (r_in * c0, 0.0, r_in * s0)
        inner1 = (r_in * c1, 0.0, r_in * s1)
        outer0 = (r_out * c0, 0.0, r_out * s0)
        outer1 = (r_out * c1, 0.0, r_out * s1)
        top = [inner0, outer0, outer1, inner0, outer1, inner1]
        bot = [(p[0], -th, p[2]) for p in top]
        wall_o = [(outer0[0], 0.0, outer0[2]), (outer0[0], -th, outer0[2]),
                  (outer1[0], -th, outer1[2]), (outer0[0], 0.0, outer0[2]),
                  (outer1[0], -th, outer1[2]), (outer1[0], 0.0, outer1[2])]
        wall_i = [(inner1[0], 0.0, inner1[2]), (inner1[0], -th, inner1[2]),
                  (inner0[0], -th, inner0[2]), (inner1[0], 0.0, inner1[2]),
                  (inner0[0], -th, inner0[2]), (inner0[0], 0.0, inner0[2])]
        # 环带按半径做几道深浅条纹（卡西尼缝的廉价版），否则一片纯色很假
        for tri_set, k in ((top, shade), (bot, shade * 0.55),
                           (wall_o, shade * 0.8), (wall_i, shade * 0.8)):
            for p in tri_set:
                rr = math.hypot(p[0], p[2])
                band = 1.0 - 0.28 * (0.5 + 0.5 * math.sin(rr * 9.0))
                verts += [p[0], p[1], p[2]]
                colors += [rgb[0] * k * band, rgb[1] * k * band, rgb[2] * k * band, alpha]
    return verts, colors


# --------------------------------------------------------------- 着色器 --
BODY_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
out vec4 v_color;
out vec3 v_world;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform mat4 model;
void main() {
    vec4 wp = model * vec4(position, 1.0);
    v_world = wp.xyz;
    v_color = colors;
    gl_Position = window.projection * window.view * wp;
}'''

# 天体着色：太阳当点光做**逐片元漫反射**（法线取"世界位置 - 天体中心"的径向，
# 球体几何上等价于解析法线；非球体如土星环另有分支）。
# 白天面/黑夜面因此是正确的：自转时昼夜线留在世界空间，不会跟着天体转。
BODY_FRAG = '''#version 330 core
in vec4 v_color;
in vec3 v_world;
out vec4 final_colors;

uniform mat4  model;          // 与顶点着色器同一个矩阵，用来反解天体中心
uniform vec3  u_sun_pos;
uniform float u_emissive;     // 0 = 行星（受光），>0 = 自发光天体（太阳）
uniform float u_light_gain;   // 距离衰减补偿（外行星纯 1/r² 会全黑）
uniform float u_flat_normal;  // 1 = 用固定法线（土星环这类扁几何）
uniform vec4  u_ring_shadow;  // x=环内径, y=环外径, zw=投影方向（0,1,0 平面法线的 xz）

void main() {
    // 天体中心的世界坐标 = model 的平移列（球体/环都构建在各自局部原点）
    vec3 center = vec3(model[3][0], model[3][1], model[3][2]);
    vec3 n = normalize(v_world - center);
    if (u_flat_normal > 0.5) {
        // 环：法线取"局部 +Y 经旋转" = 模型矩阵第 1 列（Y 基向量）
        n = normalize(vec3(model[0][1], model[1][1], model[2][1]));
    }
    vec3 to_sun = u_sun_pos - v_world;
    float d = length(to_sun);
    vec3 l = to_sun / max(d, 1e-4);

    float atten = u_light_gain / (1.0 + d * d * 0.006);
    float diff = max(dot(n, l), 0.0);
    // 环是双面的：从背光面看也要有一点亮度，否则环的暗面整片消失
    diff = max(diff, 0.22 * u_flat_normal);

    // 环投在球面上的影：从片元朝太阳走，穿过赤道面(y=0)的点若落在环带内就压暗。
    // u_ring_shadow.z 用作"是否启用"的开关（0 = 关）。
    // 注意影子中心是**本天体自己**（center），不是世界原点——土星在公转，
    // 环跟着它走，用原点当圆心的话影环会飘到太阳旁边去。
    if (u_ring_shadow.z > 0.5) {
        float ly = l.y;
        if (abs(ly) > 1e-4) {
            float tt = (center.y - v_world.y) / ly;
            if (tt > 0.0) {
                vec3 hit = v_world + l * tt;
                float rr = length(hit.xz - center.xz);
                diff *= mix(0.12, 1.0, step(u_ring_shadow.x, rr) * step(rr, u_ring_shadow.y));
            }
        }
    }

    float lam = mix(diff, 1.0, u_emissive);
    vec3 col = v_color.rgb * (0.16 + 1.30 * clamp(atten, 0.0, 1.6) * lam);
    col += u_emissive * v_color.rgb * (0.45 + 0.35 * diff);
    final_colors = vec4(col, v_color.a);
}'''

# 小行星带：实例属性携带**静态**轨道参数，位置在顶点着色器里按 u_time 现算。
BELT_VERT = '''#version 330 core
in vec3 position;
in vec4 colors;
in vec4 i_orbit;      // x=轨道半径, y=初相(弧度), z=离黄道高度, w=角速度(弧度/秒)
in vec3 i_spin;       // x=尺寸, y=自转初相, z=自转速度
in vec3 i_color;      // 颜色（每块不同）
in vec3 i_axis;       // 自转轴单位向量（决定方块朝向）
out vec4 v_color;
out vec3 v_world;
uniform WindowBlock { mat4 projection; mat4 view; } window;
uniform float u_time;

void main() {
    float ang = i_orbit.y + i_orbit.w * u_time;
    vec3 center = vec3(i_orbit.x * cos(ang), i_orbit.z, i_orbit.x * sin(ang));

    // 绕 i_axis 的旋转（Rodrigues），把"朝上的薄方块"翻成随机朝向
    vec3 a = normalize(i_axis);
    float spin = i_spin.y + i_spin.z * u_time;
    float cs = cos(spin), sn = sin(spin);
    vec3 p = position * i_spin.x;
    vec3 rotated = p * cs + cross(a, p) * sn + a * dot(a, p) * (1.0 - cs);

    v_world = center + rotated;
    float shade = 0.55 + 0.45 * abs(dot(normalize(rotated + vec3(1e-5)), normalize(a)));
    v_color = vec4(i_color * colors.rgb * shade, 1.0);
    gl_Position = window.projection * window.view * vec4(v_world, 1.0);
}'''

BELT_FRAG = '''#version 330 core
in vec4 v_color;
in vec3 v_world;
out vec4 final_colors;
uniform vec3 u_sun_pos;
void main() {
    float d = length(u_sun_pos - v_world);
    float lit = clamp(9.0 / (1.0 + d * d * 0.05), 0.25, 1.0);
    final_colors = vec4(v_color.rgb * (0.5 + 1.3 * lit), 1.0);
}'''


# ------------------------------------------------------------ 天体数据 --
class Body:
    """一颗行星/卫星的静态参数（运行期状态都在 Skeleton 节点里）。"""

    __slots__ = ('name', 'node', 'radius', 'semi_major', 'period', 'inclination',
                 'spin_period', 'rgb', 'parent', 'focus_dist', 'light_gain')

    def __init__(self, name, node, radius, semi_major, period, inclination,
                 spin_period, rgb, parent, focus_dist, light_gain=1.0):
        self.name = name
        self.node = node                    # logic.skeleton.BoneNode
        self.radius = radius
        self.semi_major = semi_major        # 轨道半长轴（场景单位）
        self.period = period                # 公转周期（秒，已压缩）
        self.inclination = inclination      # 自转轴倾角（度）
        self.spin_period = spin_period      # 自转周期（秒）
        self.rgb = rgb
        self.parent = parent                # 父天体名（'' = 太阳）
        self.focus_dist = focus_dist        # 聚焦它时的相机距离
        self.light_gain = light_gain        # 片元着色器的照度补偿


# (名字, 半径, 半长轴, 真实公转周期(年), 自转轴倾角°, 自转周期(压缩秒), 颜色, 云带数)
# 半长轴单位：1.0 = 1 AU；半径是"观感半径"，不是等比（等比的话地球只有 5e-5 AU，
# 根本看不见）——天体尺寸按对数压缩，轨道间距保留真实比例。
# 云带数只对气态巨行星给非零值（岩石行星给 0，表面没有可见条带）。
PLANET_SPEC = (
    ('Mercury', 0.42, 3.9, 0.2408, 0.03, 1.4, (0.62, 0.58, 0.54), 0),
    ('Venus', 0.58, 5.6, 0.6152, 177.4, 6.0, (0.86, 0.74, 0.50), 0),
    ('Earth', 0.62, 7.6, 1.0000, 23.4, 1.0, (0.30, 0.55, 0.85), 0),
    ('Mars', 0.48, 10.0, 1.8808, 25.2, 1.02, (0.78, 0.42, 0.28), 0),
    ('Jupiter', 1.35, 17.5, 11.862, 3.1, 0.42, (0.86, 0.74, 0.58), 9),
    ('Saturn', 1.15, 23.5, 29.457, 26.7, 0.44, (0.88, 0.81, 0.60), 7),
    ('Uranus', 0.85, 30.0, 84.011, 97.8, 0.72, (0.58, 0.82, 0.84), 4),
    ('Neptune', 0.82, 36.5, 164.79, 28.3, 0.70, (0.32, 0.44, 0.80), 3),
)

SUN_COLOR = (1.00, 0.80, 0.32)
MOON_COLOR = (0.72, 0.72, 0.70)
RING_INNER, RING_OUTER = 1.45, 2.45           # 土星环内外半径（供几何与投影阴影共用）


def tilt_spin(inclination_deg, spin_deg):
    """自转 = Rz(倾角) @ Ry(自转角)，返回 **Mat4**。

    为什么先倾后转：绕 Y 自转发生在"天体自己的参考系"里，而倾角是把这个参考系
    从黄道法线方向歪过去。写成矩阵就是 ``R_tilt @ R_spin``——右乘先作用，
    所以自转不会把倾角甩掉。

    坑：**不能**用 ``Quaternion * Quaternion`` 做这件事——pyglet 2.1.16 的
    ``Quaternion.__mul__`` 只实现了"乘标量"，两个四元数相乘会走进
    ``self.w * scalar`` 分支然后抛 ``TypeError: 'float' object cannot be
    interpreted as an integer``。所以这里直接乘 ``quat_to_mat4`` 的矩阵，
    顺带也避开了 ``Quaternion.to_mat4()`` 是转置的那个坑（见 logic/animation.py）。
    ``BoneNode.rotation`` 是四元数、``local_matrix()`` 也是四元数路径，
    因此本 demo 不用它、自己在 update() 里合成节点世界矩阵。
    """
    return (quat_to_mat4(quat_from_axis_angle((0, 0, 1), inclination_deg))
            @ quat_to_mat4(quat_from_axis_angle((0, 1, 0), spin_deg)))


class SolarSystemDemo(DemoApp):
    TITLE = '太阳系 · 层级变换 + 压缩轨道力学 + 实例化小行星带'
    HELP = ('1..8 聚焦行星   9 月球   0 全景   M 月球开关   =/- 缩放   '
            '鼠标拖动环绕/滚轮缩放   SPACE 暂停   ESC 退出')
    DURATION = DURATION
    FOV = 55.0
    Z_NEAR, Z_FAR = 0.05, 400.0
    SHOTS = ((14.0, 'docs/demo_solar_a.png'),       # 全景（水星已绕 ~3 圈）
             (34.0, 'docs/demo_solar_b.png'))       # 聚焦土星（环 + 环投影）
    FOCUS_AT = (26.0, 'Saturn')                     # 无人值守时的自动聚焦编排

    # ------------------------------------------------------------ setup --
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        # 深空底色：clear() 用 glClearColor 的当前值，所以必须在这里设
        # （每个进程一份 GL 状态，跨 demo 不共享，不用担心污染别处）
        glClearColor(0.006, 0.010, 0.024, 1.0)

        self.body_program = ShaderProgram(Shader(BODY_VERT, 'vertex'),
                                          Shader(BODY_FRAG, 'fragment'))
        self.belt_program = ShaderProgram(Shader(BELT_VERT, 'vertex'),
                                          Shader(BELT_FRAG, 'fragment'))

        # --- 节点树（父节点必须先注册！见模块 docstring）---
        self.skel = Skeleton()
        self.skel.add('sun')
        # 太阳网格：半径 1.9 的球 + 半径 2.55 的透明光晕壳（alpha=0.16）
        sv, sc = uv_sphere(1.90, 16, 28, SUN_COLOR, _shade=False)
        hv, hc = uv_sphere(2.55, 12, 20, SUN_COLOR, _shade=False)
        hc = [c if (i % 4) != 3 else 0.16 for i, c in enumerate(hc)]     # 只改 alpha
        self.halo_list = self.body_program.vertex_list(len(hv) // 3, GL_TRIANGLES,
                                                       position=('f', hv), colors=('f', hc))
        # 太阳本体也走 lists 表，render() 里按名字统一分发
        self.lists = {'sun': (self.body_program.vertex_list(
            len(sv) // 3, GL_TRIANGLES, position=('f', sv), colors=('f', sc)), 1.0)}

        # --- 8 颗行星 ---
        self.planets = []
        for (name, radius, sma, years, tilt, spin, rgb, bands) in PLANET_SPEC:
            node = self.skel.add(name, 'sun')
            period = years * ORBIT_TIME_SCALE
            body = Body(name, node, radius, sma, period, tilt, spin, rgb, '', 6.0)
            self.planets.append(body)
            pv, pc = uv_sphere(radius, 14, 20, rgb, bands=bands)
            self.lists[name] = (self.body_program.vertex_list(
                len(pv) // 3, GL_TRIANGLES, position=('f', pv), colors=('f', pc)), 0.0)
            # 聚焦距离：半径 * 13，但 OrbitCamera 的距离下限是 4.0（logic/camera.py），
            # 太近会顶到下限导致"按了键没反应"；上限 60 也一并钳住。
            body.focus_dist = max(4.0, min(60.0, radius * 13.0))
            # 点光衰减补偿：照度 gain/(1+d²·0.006) 随轨道半径掉得很快，
            # 不补偿的话木星只有地球的 1/3 亮度、海王星几乎全黑。
            # gain = 2.6·a^1.6 让"外行星更暗"这个物理事实保留，
            # 但把暗的程度压到看得见的范围（Neptune 约 0.30，Mercury 约 0.60）。
            body.light_gain = 2.6 * (sma / 7.6) ** 1.6

        # --- 地球的月球：父节点是 earth，位置自动继承（本 demo 的层级重点）---
        self.skel.add('moon', 'Earth')
        mv, mc = uv_sphere(0.20, 10, 14, MOON_COLOR)
        self.lists['moon'] = (self.body_program.vertex_list(
            len(mv) // 3, GL_TRIANGLES, position=('f', mv), colors=('f', mc)), 0.0)
        self.moon = Body('moon', self.skel.nodes['moon'], 0.20, 1.35,
                         1.0 * ORBIT_TIME_SCALE / 13.4, 6.7, 0.9,
                         MOON_COLOR, 'Earth', 4.0)

        # --- 土星环：挂在 Saturn 下，只继承倾角（节点自身不自转）---
        self.skel.add('ring', 'Saturn')
        rv, rc = ring_band(RING_INNER, RING_OUTER, 96, (0.80, 0.73, 0.56))
        self.lists['ring'] = (self.body_program.vertex_list(
            len(rv) // 3, GL_TRIANGLES, position=('f', rv), colors=('f', rc)), 0.0)

        # 可聚焦天体表：0 = 全景，1..8 = 行星，9 = 月球
        self.focus_names = [''] + [b.name for b in self.planets] + ['moon']
        self.focus = 0
        self.orbit0 = {name: self._rng_phase(name) for name in self.lists}
        self.moon_visible = True
        self.auto_focus = True
        # 每帧合成的"节点局部变换"（世界矩阵由 _apply_local 自根向叶算）
        self._local = {}

        # --- 小行星带（实例化）---
        # 数量 420：试过 820，在 1280x720 + d=60 的全景下带子会盖住太阳，
        # 变成一片"噪点雪"。少而暗一点反而读得出"带"的形状。
        self.belt = self._build_belt(420)

        # --- 相机 ---
        from logic.camera import OrbitCamera
        self.camera = OrbitCamera(target=Vec3(0, 0, 0), yaw=42.0, pitch=38.0, dist=60.0)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(self, self.camera)
        self._target = Vec3(0, 0, 0)

    @staticmethod
    def _rng_phase(name):
        """给天体一个确定的初相（用名字的哈希，保证每次运行同一构图）。"""
        h = 0
        for ch in name:
            h = (h * 131 + ord(ch)) & 0xFFFFFFFF
        return (h % 100000) / 100000.0 * 2.0 * math.pi

    def _build_belt(self, count):
        """小行星带：一个薄方块基元 + count 个实例，参数在着色器里算位置。"""
        s = 0.5
        pos = (-s, 0, -s, s, 0, -s, s, 0, s, -s, 0, -s, s, 0, s, -s, 0, s)
        col = (1.0, 1.0, 1.0, 1.0) * 6
        rng = random.Random(20261006)          # 固定种子：截图可复现
        # 关键：实例属性也要给初始数组（长度 = 顶点数 × 分量数），
        # 否则 'instance' 标志丢失、整条带子画不出来。见模块 docstring。
        vlist = self.belt_program.vertex_list_instanced(
            6, GL_TRIANGLES,
            instance_attributes=['i_orbit', 'i_spin', 'i_color', 'i_axis'],
            position=('f', pos), colors=('f', col),
            i_orbit=('f', (0.0,) * 24), i_spin=('f', (0.0,) * 18),
            i_color=('f', (0.0,) * 18), i_axis=('f', (0.0,) * 18))
        for _ in range(count):
            # 轨道半径比开普勒速度：内圈快、外圈慢，整体 60s 内转 4.6~9.4 圈
            sma = rng.uniform(12.6, 15.6)
            speed = 2.0 * math.pi / (ORBIT_TIME_SCALE * (sma / 7.6) ** 1.5)
            height = rng.gauss(0.0, 0.38)
            size = rng.uniform(0.035, 0.12)
            # 颜色：暗灰到褐灰（比行星暗一档，免得小行星带抢主体）
            k = rng.random()
            rgb = (0.22 + 0.20 * k, 0.20 + 0.15 * k, 0.18 + 0.09 * k)
            axis = (rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1))
            vlist.add_instance(
                i_orbit=(sma, rng.uniform(0, 2 * math.pi), height, speed),
                i_spin=(size, rng.uniform(0, 6.28), rng.uniform(-1.6, 1.6)),
                i_color=rgb, i_axis=axis)
        return vlist

    def _apply_local(self):
        """把"局部 TRS"写进节点树，再自根向叶算世界矩阵。

        这里不用 ``BoneNode.local_matrix()``：它走的是 ``rotation``（Quaternion）
        路径，而我们需要"倾角 @ 自转"两个矩阵相乘（pyglet 的四元数不支持
        四元数乘法，见 ``tilt_spin`` 的注释）。节点树的**继承语义**完全没变：
        ``world = parent.world @ local``。
        """
        for name in self._local:
            node = self.skel.nodes[name]
            node.world_matrix = (Mat4.from_translation(node.translation)
                                 @ self._local[name])
            for child in node.children:
                child.world_matrix = node.world_matrix @ child.local_matrix()

    # ----------------------------------------------------------- update --
    def update(self, dt):
        """把"时间"翻译成节点局部 TRS，再让 Skeleton 算世界矩阵（父子继承）。

        每帧只写**局部**量：行星写"绕太阳的平移 + 自转/倾角"，月球写"绕地球的
        平移"（相对地球！）。月球的世界坐标由 ``Skeleton.compute()`` 在
        ``earth.world @ moon.local`` 里自动得到，这里不做任何加法。
        """
        t = self.time
        # 太阳自转（纯观感）
        self._local['sun'] = tilt_spin(0.0, 360.0 * t / 12.0)
        for body in self.planets:
            ang = self.orbit0[body.name] + 2.0 * math.pi * t / body.period
            body.node.translation = Vec3(body.semi_major * math.cos(ang), 0.0,
                                         body.semi_major * math.sin(ang))
            # 自转轴倾角 = 绕 Z 轴倾，再绕自身 Y 轴自转（顺序：先倾斜参考系，再自转）
            self._local[body.name] = tilt_spin(body.inclination,
                                               360.0 * t / body.spin_period)
        # 月球：节点父级是 Earth，平移量是"相对地球"的局部量
        m = self.moon
        mang = self.orbit0['moon'] + 2.0 * math.pi * t / m.period
        m.node.translation = Vec3(m.semi_major * math.cos(mang), 0.35,
                                  m.semi_major * math.sin(mang))
        self._local['moon'] = tilt_spin(m.inclination, 360.0 * t / m.spin_period)
        # 土星环节点：不吃任何局部变换（位置继承自土星，倾角在 _model_matrix_ring 补）
        self.skel.compute()
        self._apply_local()

        # 相机 target 跟随聚焦天体的世界坐标（层级计算的直接受益者：
        # 聚焦月球时 target 会自动跟着地球一起绕太阳走）
        # 演示编排：26s 时自动把镜头交给土星（--shot 无人值守时第二张截图
        # 才拍得到"聚焦行星 + 土星环投影"；手按 1..8 会立刻覆盖这个默认编排）。
        if self.auto_focus and t >= self.FOCUS_AT[0]:
            self.auto_focus = False
            target = self.FOCUS_AT[1]
            self.focus = self.focus_names.index(target)
            self.camera.dist = max(4.0, min(60.0, self._body_of(target).focus_dist))
        if self.focus == 0:
            self._target = Vec3(0, 0, 0)
        else:
            self._target = self.skel.nodes[self.focus_names[self.focus]].world_translation()
        self.camera.target = self._target

    # ----------------------------------------------------------- render --
    def _model_matrix(self, name):
        """节点世界矩阵（层级继承的结果）→ 直接当 model 用。"""
        return self.skel.nodes[name].world_matrix

    def _model_matrix_ring(self):
        """土星环：继承土星的位置，但**剔除土星自转**、只保留倾角。

        环自己挂在 ``ring`` 节点下，而 ``ring`` 没有任何局部变换，所以它的世界
        矩阵就是土星的世界矩阵（含自转）。为了让环不跟着土星转（真实环是
        惯性系里的一圈碎冰，转起来会非常假），这里把自转"反着转回去"再叠倾角。
        """
        saturn = self.skel.nodes['Saturn']
        tilt = quat_to_mat4(quat_from_axis_angle((0, 0, 1), self._tilt_of('Saturn')))
        untwist = quat_to_mat4(quat_from_axis_angle(
            (0, 1, 0), -360.0 * self.time / self._spin_of('Saturn')))
        return saturn.world_matrix @ untwist @ tilt

    def _tilt_of(self, name):
        for b in self.planets:
            if b.name == name:
                return b.inclination
        return self.moon.inclination if name == 'moon' else 0.0

    def _spin_of(self, name):
        for b in self.planets:
            if b.name == name:
                return b.spin_period
        return self.moon.spin_period

    def _body_of(self, name):
        for b in self.planets:
            if b.name == name:
                return b
        return self.moon if name == 'moon' else None

    def render(self):
        self.window.view = self.camera.view_matrix()
        prog = self.body_program
        prog.use()
        # 太阳位置固定在原点；显式传 uniform 而不是在 shader 里写死，
        # 以后想让太阳偏一点、或加第二颗恒星时不用改着色器
        prog['u_sun_pos'] = (0.0, 0.0, 0.0)
        prog['u_light_gain'] = 1.0
        prog['u_flat_normal'] = 0.0
        prog['u_ring_shadow'] = (0.0, 0.0, 0.0, 0.0)     # z<0.5 = 关

        # 光晕（透明壳）先画：关深度写入，免得它的深度把太阳本体挡掉
        glDepthMask(0)
        prog['u_emissive'] = 0.85
        prog['model'] = self.skel.nodes['sun'].world_matrix
        self.halo_list.draw(GL_TRIANGLES)
        glDepthMask(1)

        prog['u_emissive'] = 1.0
        prog['model'] = self.skel.nodes['sun'].world_matrix
        self.lists['sun'][0].draw(GL_TRIANGLES)

        prog['u_emissive'] = 0.0
        for name, (vlist, _e) in self.lists.items():
            if name == 'sun' or (name == 'moon' and not self.moon_visible):
                continue
            if name == 'ring':
                prog['model'] = self._model_matrix_ring()
                prog['u_flat_normal'] = 1.0
                prog['u_light_gain'] = 1.0      # 环离土星很近，不需要距离补偿
            else:
                prog['model'] = self._model_matrix(name)
                prog['u_flat_normal'] = 0.0
                body = self._body_of(name)
                prog['u_light_gain'] = body.light_gain if body else 1.0
                # 只有土星要算"环投在球面上的影"
                if name == 'Saturn':
                    prog['u_ring_shadow'] = (RING_INNER, RING_OUTER, 1.0, 1.0)
                else:
                    prog['u_ring_shadow'] = (0.0, 0.0, 0.0, 0.0)
            vlist.draw(GL_TRIANGLES)
        prog['u_flat_normal'] = 0.0
        prog['u_light_gain'] = 1.0
        prog['u_ring_shadow'] = (0.0, 0.0, 0.0, 0.0)

        # 小行星带：一次 instanced 绘制，逐帧零 CPU 端顶点/实例写入
        bp = self.belt_program
        bp.use()
        bp['u_time'] = self.time
        bp['u_sun_pos'] = (0.0, 0.0, 0.0)
        self.belt.draw(GL_TRIANGLES)

    # ------------------------------------------------------------- HUD --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        self.auto_focus = False               # 手按过任何键就交还控制权
        if key._1 <= symbol <= key._8:
            self.focus = symbol - key._0          # 1..8 → 索引 1..8
            self.camera.dist = max(4.0, min(60.0, self._body_of(
                self.focus_names[self.focus]).focus_dist))
        elif symbol == key._0:
            self.focus = 0
            self.camera.dist = 60.0
        elif symbol == key._9:
            self.focus = 9
            self.camera.dist = self.moon.focus_dist
        elif symbol in (key.EQUAL, key.PLUS, key.NUM_ADD):
            self.camera.dist = max(4.0, self.camera.dist - 4.0)
        elif symbol in (key.MINUS, key.NUM_SUBTRACT):
            self.camera.dist = min(60.0, self.camera.dist + 4.0)
        elif symbol == key.M:
            self.moon_visible = not self.moon_visible

    def status_text(self):
        name = self.focus_names[self.focus] if self.focus else '全景 (Sun)'
        if self.focus == 0:
            period = '—'
        elif self.focus_names[self.focus] == 'moon':
            period = f'{self.moon.period:.1f}s/圈 (真实 27.3d 压缩)'
        else:
            b = self._body_of(self.focus_names[self.focus])
            period = f'{b.period:.2f}s/圈'
        flag = '  [PAUSED]' if self.paused else ''
        return (f'聚焦 {name}   公转周期 {period}   t={self.time:6.2f}s   '
                f'd={self.camera.dist:.1f}   {self.fps:5.1f} fps{flag}')


if __name__ == '__main__':
    run(SolarSystemDemo)

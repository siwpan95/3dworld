# -*- coding: utf-8 -*-
"""程序化地形低空飞行：确定性值噪声 + fBm 高程图 + 烘焙光照 + 指数雾。

画面构成
--------
- 一张 192x192 的高程图（世界 192x192 单位，格距 1.0），**只在 setup/重建时**
  用纯 Python 拼一次三角形汤交给 `vertex_list`；之后每帧只有 uniform 变化。
- 顶点色在 CPU 侧烘焙：高度分层（水/沙/草/岩/雪）+ 坡度（陡处露岩）+ 面向光
  亮度（法线点乘主光）。片元着色器只做一件事：按视距混入雾色——地形纹理不在
  GPU 里算，省得在 73k 三角上跑分支。
- 相机是**追尾机位**：`OrbitCamera` 的 target 坐在飞行器上，yaw 跟着航向自动
  对齐，眼点低于地面时自动抬高俯仰/拉远；视图矩阵再右乘一个滚转矩阵做转弯
  横滚（`look_at` 的 up 永远是 +Y，横滚只能这样叠）。

踩过的坑（写下来省得再踩）
--------------------------
1. ``Quaternion.to_mat4`` / ``Mat4 * Mat4``：pyglet 2.1.16 里矩阵相乘必须用
   ``@``，``*`` 只用于矩阵乘向量，混用会抛 NotImplementedError。
2. 顶点数据用 ``array('f')`` 攒：73k 三角 = 65 万个 float，用 Python list 光
   指针+对象就要几十 MB，array 只要 2.6MB，而且交给 pyglet 时实测同样快。
3. 雾色必须**等于**清屏色（``glClearColor``），否则地平线会出现一条硬边。
4. ``R`` 重建前要 ``vlist.delete()`` 把旧网格还给域分配器，不然每按一次都漏
   一份 VBO。
5. 鼠标拖动是直接改 ``camera.yaw/pitch/dist`` 的，而本 demo 又要每帧把这三个
   量当**绝对值**写回去（自动航向）——两者会打架，所以用 `_AutoOrbit` 每帧
   先"吸"出纯用户增量，再把「自动值 + 用户增量」写回去。

用法::

    python examples/terrain_flyover.py
    python examples/terrain_flyover.py --smoke 2
    python examples/terrain_flyover.py --shot

按键：R 换种子重建地形，鼠标拖动/滚轮改视角，SPACE 暂停，ESC 退出。
"""
import array
import math
import random
import time

import pyglet
from pyglet.gl import GL_DEPTH_TEST, GL_TRIANGLES, glClearColor, glEnable
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
from logic.camera import (DIST_MAX, DIST_MIN, PITCH_MAX, PITCH_MIN,
                          OrbitCamera)

TAU = 6.28318531

# ============================================================== 噪声（纯函数）==
# 全部是模块级纯函数：同 (坐标, seed) 必然得到同一个高程，不依赖任何全局状态，
# 所以 R 键重建、以及跨进程截图都是可复现的。
_MASK = 0xFFFFFFFF


def _hash01(ix, iy, seed):
    """整数格点 + seed -> [0,1) 的确定性哈希。

    用整数位运算（而不是 ``fract(sin(x)*43758.5)``）：sin 的精度在不同驱动/
    编译器下不一致，位运算在所有平台逐位相同，重建才真的可复现。
    """
    h = (ix * 0x27D4EB2D) & _MASK
    h = (h ^ (iy * 0x165667B1)) & _MASK
    h = (h ^ (seed * 0x9E3779B1)) & _MASK
    h = ((h ^ (h >> 15)) * 0x2C1B3C6D) & _MASK
    h = ((h ^ (h >> 12)) * 0x297A2D39) & _MASK
    h ^= h >> 15
    return (h & 0xFFFFFF) / 16777216.0


def _value_noise(x, y, seed):
    """双线性插值的值噪声（smoothstep 平滑，避免菱形格纹）。"""
    ix, iy = math.floor(x), math.floor(y)
    fx, fy = x - ix, y - iy
    ux = fx * fx * (3.0 - 2.0 * fx)
    uy = fy * fy * (3.0 - 2.0 * fy)
    h = _hash01
    n00 = h(ix, iy, seed)
    n10 = h(ix + 1, iy, seed)
    n01 = h(ix, iy + 1, seed)
    n11 = h(ix + 1, iy + 1, seed)
    a = n00 + (n10 - n00) * ux
    b = n01 + (n11 - n01) * ux
    return a + (b - a) * uy


def _fbm(x, y, seed, octaves=4):
    """分形叠加：每层频率 x2、振幅 x0.5（用不同 seed 偏移避免层间相关）。"""
    amp, freq, total, norm = 1.0, 1.0, 0.0, 0.0
    for i in range(octaves):
        total += amp * _value_noise(x * freq, y * freq, seed + i * 7919)
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


def height_at(wx, wz, seed):
    """世界坐标 -> 高程。三层噪声分工：大起伏 / 山脊 / 平原谷地。"""
    base = _fbm(wx * 0.0085, wz * 0.0085, seed, 4)                 # 大尺度起伏
    ridg = _fbm(wx * 0.021 + 11.3, wz * 0.021 - 5.1, seed + 977, 4)
    ridge = 1.0 - abs(2.0 * ridg - 1.0)                            # 折叠 -> 尖山脊
    plain = _fbm(wx * 0.0045 - 3.7, wz * 0.0045 + 8.9, seed + 2851, 3)
    bb = base * base * (3.0 - 2.0 * base)                          # 拉伸对比度
    return (56.0 * bb ** 1.25 + 34.0 * ridge ** 2.2 * (0.30 + 0.70 * plain)
            - 18.0 * plain - 15.0)


# ---- 配色分层（CPU 烘焙，片元着色器里不做地形纹理）----
WATER_LEVEL = 0.0
C_DEEP = (0.045, 0.16, 0.36)
C_SHALLOW = (0.13, 0.38, 0.56)
C_SAND = (0.80, 0.74, 0.52)
C_GRASS = (0.21, 0.42, 0.17)
C_ROCK = (0.40, 0.36, 0.32)
C_SNOW = (0.93, 0.95, 0.98)
LIGHT_DIR = (0.42, 0.78, 0.30)          # 主光（不做归一化也行，下面统一归一）


def _mix3(a, b, t):
    return (a[0] + (b[0] - a[0]) * t,
            a[1] + (b[1] - a[1]) * t,
            a[2] + (b[2] - a[2]) * t)


def _smooth(e0, e1, x):
    """把 x 从 [e0,e1] 映射到 [0,1] 并做平滑（分层过渡用）。"""
    if e1 == e0:
        return 0.0
    t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def terrain_color(h, slope, lambert, jitter):
    """(高度, 坡度(0平1陡), 面向光亮度, 抖动) -> 烘焙好的 RGB。

    分层顺序：水 -> 沙 -> 草 -(坡陡)-> 岩 -(高)-> 雪，全部用平滑过渡，
    避免出现"色带"硬边；最后乘 ``0.34 + 0.90*lambert`` 把光照烘进顶点色。
    """
    if h < WATER_LEVEL:
        # 水：越深越暗，且几乎不吃面向光（水面平坦）
        c = _mix3(C_SHALLOW, C_DEEP, _smooth(0.0, 9.0, WATER_LEVEL - h))
        k = 0.62 + 0.40 * lambert
    else:
        c = _mix3(C_SAND, C_GRASS, _smooth(1.2, 4.5, h))
        c = _mix3(c, C_ROCK, _smooth(0.16, 0.42, slope))       # 陡坡露岩
        c = _mix3(c, C_SNOW, _smooth(26.0, 34.0, h))           # 雪线（只盖最高峰）
        k = 0.34 + 0.90 * lambert
    j = 0.88 + 0.24 * jitter                                   # 打散色块
    return (min(1.0, c[0] * k * j),
            min(1.0, c[1] * k * j),
            min(1.0, c[2] * k * j))


# ================================================================== 着色器 ==
VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;          // CPU 已烘焙：分层色 x 面向光
uniform WindowBlock { mat4 projection; mat4 view; } window;
out vec4 v_col;
out vec3 v_world;
void main() {
    v_col = colors;
    v_world = position;
    gl_Position = window.projection * window.view * vec4(position, 1.0);
}'''

FRAGMENT_SRC = '''#version 330 core
in vec4 v_col;
in vec3 v_world;
out vec4 final_colors;
uniform vec3 u_eye;
uniform vec3 u_fog;       // 必须等于清屏色，否则地平线一条硬边
void main() {
    float d = distance(v_world, u_eye);
    float fog = 1.0 - exp(-d * 0.0105);
    final_colors = vec4(mix(v_col.rgb, u_fog, clamp(fog, 0.0, 1.0)), 1.0);
}'''

SKY = (0.58, 0.70, 0.84)


# ============================================================ 相机：自动+用户 ==
class _AutoOrbit:
    """把 OrbitCamera 拆成「自动编排」+「用户增量」两层。

    ``orbit_camera_input`` 会直接改 camera 的 yaw/pitch/dist；而本 demo 每帧都要
    把这三个量写成绝对值（自动航向）。做法：每帧先算「当前值 - 上一帧我们写入
    的基线」= 纯用户增量并累加，再把「自动值 + 用户增量」写回去。
    """

    def __init__(self, camera):
        self.camera = camera
        self.user_yaw = 0.0
        self.user_pitch = 0.0
        self.user_dist = 0.0
        self._base = (camera.yaw, camera.pitch, camera.dist)

    def absorb(self):
        c = self.camera
        self.user_yaw += c.yaw - self._base[0]
        self.user_pitch += c.pitch - self._base[1]
        self.user_dist += c.dist - self._base[2]

    def apply(self, yaw, pitch, dist):
        c = self.camera
        c.yaw = yaw + self.user_yaw
        c.pitch = max(PITCH_MIN, min(PITCH_MAX, pitch + self.user_pitch))
        c.dist = max(DIST_MIN, min(DIST_MAX, dist + self.user_dist))
        self._base = (c.yaw, c.pitch, c.dist)


def emit_row(pos, col, hs, cols, n, step, ox, oz, j):
    """把第 j 行四边形带写成三角形汤，追加到 pos/col（纯函数，可无窗口单测）。

    每个四边形按 (a,b,d) + (a,d,c) 两个三角形展开；顶点色直接取格点色，
    于是相邻四边形共享同一套颜色 —— 这就是"顶点色平滑着色"的来源。
    """
    z0 = oz + j * step
    z1 = z0 + step
    row = j * n
    for i in range(n - 1):
        x0 = ox + i * step
        x1 = x0 + step
        a, b = row + i, row + i + 1
        c, d = a + n, b + n
        pos.extend((x0, hs[a], z0, x1, hs[b], z0, x1, hs[d], z1,
                    x0, hs[a], z0, x1, hs[d], z1, x0, hs[c], z1))
        col.extend(cols[a])
        col.extend(cols[b])
        col.extend(cols[d])
        col.extend(cols[a])
        col.extend(cols[d])
        col.extend(cols[c])


class TerrainDemo(DemoApp):
    TITLE = '程序化地形低空飞行 (值噪声 + fBm + 烘焙光照)'
    HELP = 'SPACE 暂停   R 换种子重建地形   拖动/滚轮 视角   ESC 退出'
    DURATION = 48.0
    HUD = True
    SHOTS = ((9.0, 'docs/demo_terrain_a.png'),
             (33.0, 'docs/demo_terrain_b.png'))

    GRID = 192                 # 高程图边长（格点数）
    REBUILD_BUDGET_MS = 8.0     # 渐进重建的每帧预算（毫秒）
    STEP = 1.0                 # 格距 -> 世界尺寸 192x192
    CLEARANCE = 5.0            # 飞行器最小离地高度
    CAM_PITCH = 14.0           # 追尾机位俯仰（度）
    CAM_DIST = 27.0            # 追尾机位距离
    LOOKAHEAD = 2.2            # 注视点前瞻（秒）
    FOV = 55.0                 # 比默认 60 略窄：近处地面占比小一点

    # ------------------------------------------------------------ 生命周期 --
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glClearColor(SKY[0], SKY[1], SKY[2], 1.0)   # 清屏色 == 雾色（坑位 3）
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))
        self.seed = 12345
        self.heights = None
        self.terrain = None
        self._rb = None             # 渐进重建状态（None = 空闲）
        self._progress = 1.0
        self.last_rebuild_ms = 0.0
        self.tris = 0
        self.roll = 0.0
        self._lift_p, self._lift_d = 0.0, 0.0
        self.camera = OrbitCamera(target=Vec3(0.0, 10.0, 0.0),
                                  yaw=45.0, pitch=self.CAM_PITCH,
                                  dist=self.CAM_DIST)
        self.auto = _AutoOrbit(self.camera)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(
            self, self.camera)
        self.rebuild(self.seed)
        # --flag reseed：1 秒后自动换一次种子。配合 --bench 用来量化
        # "渐进重建不冻画面"（对比有无该 flag 的 worst_frame_ms）。
        if 'reseed' in self.args.flags:
            pyglet.clock.schedule_once(
                lambda dt: self.begin_rebuild(self.seed + 1), 1.0)
        if 'reseed-sync' in self.args.flags:
            # 对照组：老的同步重建（会冻住画面）。保留这条路径只为 A/B 量化，
            # 正常运行永远走渐进式。
            pyglet.clock.schedule_once(lambda dt: self.rebuild(self.seed + 1), 1.0)

    def _height(self, wx, wz):
        return height_at(wx, wz, self.seed)

    # ------------------------------------------------------- 渐进式重建 --
    def begin_rebuild(self, seed):
        """开始**渐进式**重建：只准备状态，真正计算分摊到 update() 的每帧预算里。

        为什么不做同步重建：整张 192×192 高程图 + 7.3 万三角一次性算完实测
        1.1~1.3s，期间画面完全冻住（玩家看到的就是"按 R 卡一下"）。
        拆成"每帧最多 8ms"之后，重建期间帧率不掉，只是旧地形多留一两秒，
        完成时一次性换网格——观感上从"卡顿"变成"渐显"。
        """
        n, step = self.GRID, self.STEP
        self.seed = seed
        self._rb = {
            'n': n, 'step': step,
            'ox': -0.5 * (n - 1) * step, 'oz': -0.5 * (n - 1) * step,
            'hs': [0.0] * (n * n), 'cols': [(0.0, 0.0, 0.0, 1.0)] * (n * n),
            'pos': array.array('f'), 'col': array.array('f'),
            'row': 0, 'phase': 0, 'done': 0, 'total': 3 * n,
        }
        self._progress = 0.0
        self._rb_t0 = time.perf_counter()

    def step_rebuild(self, budget_ms=8.0):
        """按毫秒预算推进重建；返回 True 表示还没完。"""
        rb = self._rb
        if rb is None:
            return False
        n, step = rb['n'], rb['step']
        hs, cols = rb['hs'], rb['cols']
        deadline = time.perf_counter() + budget_ms / 1000.0
        while True:
            if rb['phase'] == 0:                       # 高程图（逐行）
                j = rb['row']
                base = j * n
                oz = rb['oz'] + j * step
                for i in range(n):
                    hs[base + i] = height_at(rb['ox'] + i * step, oz, self.seed)
                rb['row'] += 1
                rb['done'] += 1
                if rb['row'] >= n:
                    rb['phase'], rb['row'] = 1, 0
            elif rb['phase'] == 1:                     # 顶点色（需要邻域，故在高程之后）
                j = rb['row']
                for i in range(n):
                    cols[j * n + i] = self._vertex_color(hs, cols, n, step, i, j)
                rb['row'] += 1
                rb['done'] += 1
                if rb['row'] >= n:
                    rb['phase'], rb['row'] = 2, 0
            else:                                      # 三角形汤（逐行四边形带）
                j = rb['row']
                emit_row(rb['pos'], rb['col'], hs, cols, n, step,
                         rb['ox'], rb['oz'], j)
                rb['row'] += 1
                rb['done'] += 1
                if rb['row'] >= n - 1:
                    self._finish_rebuild()
                    return False
            self._progress = rb['done'] / float(rb['total'])
            if time.perf_counter() >= deadline:
                return True                            # 预算用完，下一帧接着算

    def _vertex_color(self, hs, cols, n, step, i, j):
        """单个格点的顶点色（高度 + 坡度 + 面向光 + 哈希抖动）。"""
        k = j * n + i
        il, ir = max(0, i - 1), min(n - 1, i + 1)
        jd, ju = max(0, j - 1), min(n - 1, j + 1)
        dx = (hs[j * n + ir] - hs[j * n + il]) / ((ir - il) * step)
        dz = (hs[ju * n + i] - hs[jd * n + i]) / ((ju - jd) * step)
        nx, ny, nz = -dx, 1.0, -dz
        nl = math.sqrt(nx * nx + ny * ny + nz * nz)
        lx, ly, lz = LIGHT_DIR
        ll = math.sqrt(lx * lx + ly * ly + lz * lz)
        lam = max(0.0, (nx * lx + ny * ly + nz * lz) / (nl * ll))
        r, g, b = terrain_color(hs[k], 1.0 - 1.0 / nl, lam,
                                _hash01(i, j, self.seed + 4241))
        # 必须补上 alpha 变成 4 元组：colors 属性是 vec4，返回 3 元组会让顶点
        # 缓冲少 1/4 的 float，pyglet 直接报 Invalid data size for 'colors'
        return (r, g, b, 1.0)

    def _finish_rebuild(self):
        """收尾：一次性换网格（旧的在换完后再还回去）。"""
        rb = self._rb
        pos, col = rb['pos'], rb['col']
        if self.terrain is not None:
            self.terrain.delete()                   # 坑位 4：旧网格要还回去
        self.terrain = self.program.vertex_list(
            len(pos) // 3, GL_TRIANGLES,
            position=('f', pos), colors=('f', col))
        self.tris = len(pos) // 9
        self.heights = rb['hs']
        self._rb = None
        self._progress = 1.0
        self.last_rebuild_ms = (time.perf_counter() - self._rb_t0) * 1000.0

    def rebuild(self, seed):
        """同步重建（只给 setup 用：首帧就该有地形，不能等渐进）。

        运行中换种子一律走 ``begin_rebuild`` + ``step_rebuild``（见 R 键）。
        """
        self.begin_rebuild(seed)
        while self.step_rebuild(1e9):
            pass
        return self.last_rebuild_ms

    # --------------------------------------------------------------- 航线 --
    def path_point(self, t):
        """航线 (x, z)：所有频率都取 DURATION 的整数倍 -> 循环播放首尾无缝。"""
        u = t / self.DURATION
        a = TAU * u
        r = 62.0 * (0.76 + 0.24 * math.sin(a * 2.0 + 1.0))
        wob = 0.42 * math.sin(a * 3.0 + 0.7)        # 角度微扰 -> 不是纯圆
        return r * math.cos(a + wob), r * math.sin(a + wob)

    def _flight(self, t):
        """返回 (位置 Vec3, 航向角弧度)；高度跟随地形并保持最小离地高度。"""
        u = t / self.DURATION
        x, z = self.path_point(t)
        ground = max(self._height(x, z), WATER_LEVEL)
        # 离地高度沿航线在 5~19 之间缓慢起伏（周期 = DURATION，循环无缝）：
        # 前半夜贴地擦谷、后半夜爬升越脊 —— 两张截图的高度差就来自这里。
        clr = self.CLEARANCE + 14.0 * (0.5 - 0.5 * math.cos(TAU * u))
        y = ground + clr
        x2, z2 = self.path_point(t + 1.0)
        head = math.atan2(z2 - z, x2 - x)
        return Vec3(x, y, z), head

    # ------------------------------------------------------------- 逐帧 --
    def update(self, dt):
        if self._rb is not None:
            self.step_rebuild(self.REBUILD_BUDGET_MS)   # 每帧最多啃 8ms
        if self.paused:
            return                                  # 暂停时不动相机也不吸增量
        self.auto.absorb()                          # 先吸出用户增量（坑位 5）
        # 注视点取**前方 2.2s 的航点**而不是机身本身：视野更多留给地平线，
        # 否则近处地面会糊满屏幕下半部分。
        pos, head = self._flight(self.time + self.LOOKAHEAD)
        self.camera.target = Vec3(pos.x, pos.y + 1.5, pos.z)

        # --- 追尾机位：yaw 让眼点落在航向的**反方向**，pitch/dist 可被地形顶高 ---
        yaw = math.degrees(math.atan2(-math.sin(head), -math.cos(head)))
        pitch, dist = self.CAM_PITCH, self.CAM_DIST
        for _ in range(8):                          # 眼点钻进山里就抬高、拉远
            pr, yr = math.radians(pitch), math.radians(yaw)
            ex = pos.x + dist * math.cos(pr) * math.cos(yr)
            ez = pos.z + dist * math.cos(pr) * math.sin(yr)
            ey = pos.y + 1.5 + dist * math.sin(pr)
            if ey >= self._height(ex, ez) + 1.8:
                break
            pitch += 2.5
            dist += 1.5
        k = min(1.0, dt * 4.0)                      # 平滑，避免顶高时跳变
        self._lift_p += (pitch - self.CAM_PITCH - self._lift_p) * k
        self._lift_d += (dist - self.CAM_DIST - self._lift_d) * k
        self.auto.apply(yaw, self.CAM_PITCH + self._lift_p,
                        self.CAM_DIST + self._lift_d)

        # --- 转弯横滚：用 1 秒后的航向差当作转弯速率 ---
        _, head2 = self._flight(self.time + self.LOOKAHEAD + 1.0)
        dh = (head2 - head + math.pi) % TAU - math.pi
        want = max(-0.16, min(0.16, dh * 1.1))
        self.roll += (want - self.roll) * min(1.0, dt * 1.5)

    def render(self):
        if self.terrain is None:
            return
        cam = self.camera
        self.window.view = cam.view_matrix() @ Mat4.from_rotation(
            self.roll, Vec3(0.0, 0.0, 1.0))         # 坑位 1：矩阵相乘用 @
        prog = self.program
        prog.use()
        prog['u_eye'] = (cam.eye.x, cam.eye.y, cam.eye.z)
        prog['u_fog'] = SKY
        self.terrain.draw(GL_TRIANGLES)

    # --------------------------------------------------------------- 输入 --
    def on_key(self, symbol, modifiers):
        if symbol == pyglet.window.key.R:
            rng = random.Random()
            # 换一张可复现的新地形：渐进重建，不冻画面（见 begin_rebuild）
            self.begin_rebuild(rng.randrange(1, 10 ** 6))

    def status_text(self):
        if self._rb is not None:
            return (f'seed {self.seed}   重建中 {self._progress * 100:4.1f}%   '
                    f'（每帧 {self.REBUILD_BUDGET_MS:.0f}ms 预算，旧地形保持可玩）   '
                    f'{self.fps:5.1f} fps')
        g = self._height(self.camera.target.x, self.camera.target.z)
        alt = self.camera.target.y - 1.5 - max(g, WATER_LEVEL)
        return (f'seed {self.seed}   网格 {self.GRID}x{self.GRID}   三角 '
                f'{self.tris}   离地 {alt:4.1f}   横滚 '
                f'{math.degrees(self.roll):+4.1f}°   t={self.time:5.1f}s/'
                f'{self.DURATION:.0f}s   重建 {self.last_rebuild_ms:.0f}ms   {self.fps:5.1f} fps'
                f'{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(TerrainDemo)

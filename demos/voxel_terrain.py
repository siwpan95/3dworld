# -*- coding: utf-8 -*-
"""体素地形（面剔除网格化）：只生成暴露在外的面，并把"省了多少"摆在 HUD 上。

核心实证
--------
setup 里**同时**建两套网格，用同一份世界数据、同一段构建代码（只有 ``cull``
开关不同）：

- 暴露面网格：相邻体素是实心就跳过该面 -> 只剩表面；
- 全部面网格：每个实心体素都发 6 个面（内部面被外层挡住，肉眼几乎看不出差别，
  白白多烧几倍的三角形）。

HUD 同时显示两者的面数/三角数与剔除率，按 H 可以在两套网格之间切换，配合
F 线框就能直接看到"内部面本来也在被画"这件事。

其它要点
--------
- 世界 = 40x40 列、1~14 层的确定性高度图；块色 = 高度分层 + 哈希抖动，再乘
  一个逐面亮度（法线调亮度，思路同 ``core.geometry.box_data``）。
- 顶点数据全部在 setup 里用 ``array('f')`` 拼一次（全部面网格约 40 万顶点，
  用 Python list 会多占几十 MB 且更慢），运行时一个顶点都不重建。
- ``1/2`` 两种机位共用一个 ``OrbitCamera``：自动值每帧写死，用户的鼠标增量由
  `_AutoOrbit` 单独累加（见下），切机位时对自动值做插值，所以是平滑过渡而不是
  瞬移。
- ``glPolygonMode`` 画完必须还原成 ``GL_FILL``：HUD 文字也是三角形，留在
  GL_LINE 会把字画成空心轮廓（这个坑很容易踩）。
- 面的顶点偏移要存成 ``(dx,dy,dz)`` 三元组，**不能**摊平成一串 float：摊平后
  加体素基坐标时会分不清分量，把 x 加到 y/z 上（实测把整块地形压成一条斜线，
  排查了半天）。

用法::

    python demos/voxel_terrain.py
    python demos/voxel_terrain.py --smoke 2
    python demos/voxel_terrain.py --shot

按键：1 环绕俯瞰 / 2 贴地漫游，F 线框，H 面剔除开关，鼠标拖动/滚轮 视角，
SPACE 暂停，ESC 退出。
"""
import array
import math

import pyglet
from pyglet.gl import (GL_DEPTH_TEST, GL_FILL, GL_FRONT_AND_BACK, GL_LINE,
                       GL_TRIANGLES, glClearColor, glEnable, glPolygonMode)
from pyglet.graphics.shader import Shader, ShaderProgram
from pyglet.math import Vec3

from _harness import DemoApp, orbit_camera_input, run
from logic.camera import (DIST_MAX, DIST_MIN, PITCH_MAX, PITCH_MIN,
                          OrbitCamera)

# ================================================================ 世界参数 ==
W = 40                 # 世界边长（列数），x/z 都是 0..W-1
MAXH = 14              # 最大层数（1~14）
CX = CZ = 0.5 * W      # 世界中心

# 单位立方体 6 个面：法线 + 4 个角（角按逆时针给，正面朝外）
FACES = (
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, -1, 0), ((0, 0, 1), (0, 0, 0), (1, 0, 0), (1, 0, 1))),
    ((1, 0, 0), ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1))),
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
    ((0, 0, -1), ((1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0))),
)
NORMALS = tuple(f[0] for f in FACES)
# 每个面的 6 个顶点偏移（两个三角形 = 角 0,1,2 + 0,2,3）。
# 注意：必须是 (dx,dy,dz) 三元组而不是摊平的 float —— 摊平后 x/y/z 分量分不清，
# 加体素基坐标时会把 x 加到 y/z 上（实测直接把地形压成一条斜线）。
FACE_QUAD = tuple((c[0], c[1], c[2], c[0], c[2], c[3]) for _, c in FACES)
# 法线调亮度：顶面最亮、背面最暗（和 box_data 的 _face_shade 同一个意思）
FACE_SHADE = (1.00, 0.45, 0.82, 0.66, 0.90, 0.58)

# 表层生物群系 / 内部岩层配色
C_LOW = (0.22, 0.46, 0.19)      # 低地草
C_MID = (0.33, 0.48, 0.22)      # 中段草坡
C_HIGH = (0.47, 0.43, 0.34)     # 高处岩
C_SNOW = (0.88, 0.91, 0.96)     # 峰顶雪
C_STRATA = ((0.30, 0.29, 0.33), (0.38, 0.34, 0.28),
            (0.45, 0.42, 0.40), (0.56, 0.55, 0.53))

_MASK = 0xFFFFFFFF


def _hash01(x, y, z, seed):
    """确定性哈希 -> [0,1)：色块抖动与高度图都用它，重建必然一致。"""
    h = (x * 0x27D4EB2D) & _MASK
    h = (h ^ (y * 0x165667B1)) & _MASK
    h = (h ^ (z * 0x9E3779B1)) & _MASK
    h = (h ^ (seed * 0x85EBCA6B)) & _MASK
    h = ((h ^ (h >> 15)) * 0x2C1B3C6D) & _MASK
    h = ((h ^ (h >> 13)) * 0x297A2D39) & _MASK
    h ^= h >> 16
    return (h & 0xFFFFFF) / 16777216.0


def build_heights(seed):
    """高度图：白噪声 -> 几轮 3x3 均值（连绵起伏）-> 混回一点原始噪声 -> 量化。"""
    raw = [_hash01(x, 0, z, seed) for z in range(W) for x in range(W)]
    field = raw[:]
    for _ in range(3):
        nxt = []
        for z in range(W):
            for x in range(W):
                s, c = 0.0, 0
                for dz in (-1, 0, 1):
                    zz = z + dz
                    if zz < 0 or zz >= W:
                        continue
                    for dx in (-1, 0, 1):
                        xx = x + dx
                        if 0 <= xx < W:
                            s += field[zz * W + xx]
                            c += 1
                nxt.append(s / c)
        field = nxt
    mixed = [0.72 * field[i] + 0.28 * raw[i] for i in range(W * W)]
    lo, hi = min(mixed), max(mixed)
    span = max(1e-6, hi - lo)
    return [1 + int((v - lo) / span * (MAXH - 1.6) + 0.45) for v in mixed]


def voxel_rgb(x, y, z, h, seed):
    """块色 = 分层色 x 哈希抖动；表层按高度给生物群系，内部给岩层。"""
    if y == h - 1:
        base = (C_LOW if y <= 4 else C_MID if y <= 8
                else C_HIGH if y <= 11 else C_SNOW)
    else:
        base = C_STRATA[min(3, y // 4)]
    k = 0.86 + 0.28 * _hash01(x, y * 7 + 1, z, seed + 31337)
    return base[0] * k, base[1] * k, base[2] * k


def build_mesh(heights, seed, cull):
    """把世界网格化成三角形汤。

    ``cull=True`` 时相邻体素为实心就跳过该面——这就是"面剔除"，也是本 demo 的
    主角；``cull=False`` 时每个实心体素都发 6 个面，用来做数量对比。
    返回 (位置 array, 颜色 array, 面数)。
    """
    pos = array.array('f')
    col = array.array('f')
    faces = 0
    ext_p, ext_c = pos.extend, col.extend
    for z in range(W):
        zrow = z * W
        for x in range(W):
            h = heights[zrow + x]
            for y in range(h):
                r, g, b = voxel_rgb(x, y, z, h, seed)
                for ni in range(6):
                    nx, ny, nz = NORMALS[ni]
                    if cull:
                        ax, ay, az = x + nx, y + ny, z + nz
                        if az < 0 or az >= W or ax < 0 or ax >= W:
                            pass                    # 世界外侧 = 空气，要画
                        elif ay < 0:
                            continue                # 地基以下算实心：不画底面
                        elif ay < heights[az * W + ax]:
                            continue                # 被邻居挡住：剔除
                    k = FACE_SHADE[ni]
                    c = (r * k, g * k, b * k, 1.0)
                    ext_p([v for p in FACE_QUAD[ni]
                           for v in (x + p[0], y + p[1], z + p[2])])
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    ext_c(c)
                    faces += 1
    return pos, col, faces


VERTEX_SRC = '''#version 330 core
in vec3 position;
in vec4 colors;          // 已含分层色与逐面亮度
uniform WindowBlock { mat4 projection; mat4 view; } window;
out vec4 v_col;
void main() {
    v_col = colors;
    gl_Position = window.projection * window.view * vec4(position, 1.0);
}'''

FRAGMENT_SRC = '''#version 330 core
in vec4 v_col;
out vec4 final_colors;
void main() { final_colors = v_col; }'''


class _AutoOrbit:
    """把 OrbitCamera 拆成「自动机位」+「用户增量」两层。

    ``orbit_camera_input`` 直接改 camera.yaw/pitch/dist，而本 demo 每帧都要把这三
    个量写成绝对量（机位编排 + 切机位插值）。做法：每帧先算「当前值 - 上一帧写
    入的基线」= 纯用户增量，再写回「自动值 + 用户增量」。
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


def _nearest_angle(cur, want):
    """把目标角度折算到离 cur 最近的同名角：避免切机位时绕 360 度大回环。"""
    return want + 360.0 * round((cur - want) / 360.0)


class VoxelDemo(DemoApp):
    TITLE = '体素地形 · 面剔除网格化 (pyglet 逐面剔除)'
    HELP = 'SPACE 暂停   1 俯瞰 2 漫游   F 线框   H 面剔除开关   拖动/滚轮   ESC'
    DURATION = 36.0
    SHOTS = ((8.0, 'docs/demo_voxel_a.png'),
             (27.0, 'docs/demo_voxel_b.png'))

    SEED = 20240607
    AUTO_SWITCH = 0.5          # 循环过半自动切到第二种机位（1/2 键可手动接管）

    # ------------------------------------------------------------ 生命周期 --
    def setup(self):
        glEnable(GL_DEPTH_TEST)
        glClearColor(0.09, 0.11, 0.15, 1.0)
        self.program = ShaderProgram(Shader(VERTEX_SRC, 'vertex'),
                                     Shader(FRAGMENT_SRC, 'fragment'))

        self.heights = build_heights(self.SEED)
        self.voxels = sum(self.heights)
        self.mesh = {}                          # True=剔除后的暴露面 / False=全部面
        self.faces = {}
        for cull in (True, False):
            pos, col, faces = build_mesh(self.heights, self.SEED, cull)
            self.faces[cull] = faces
            self.mesh[cull] = self.program.vertex_list(
                len(pos) // 3, GL_TRIANGLES,
                position=('f', pos), colors=('f', col))

        self.cull = True                        # H 键：画暴露面 / 画全部面
        self.wire = False                       # F 键：线框
        self.mode = 0                           # 1/2 键：机位
        self.auto_mode = True
        self._auto = [0.0, 30.0, 52.0, CX, 7.0, CZ]   # yaw/pitch/dist/注视点

        self.camera = OrbitCamera(target=Vec3(CX, 7.0, CZ),
                                  yaw=0.0, pitch=30.0, dist=52.0)
        self.auto = _AutoOrbit(self.camera)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(
            self, self.camera)

    # --------------------------------------------------------------- 机位 --
    def _roam_point(self, t):
        """贴地漫游的注视点：绕世界中心画圈，高度贴着体素地表。"""
        a = t * 0.22
        x = CX + 13.0 * math.cos(a)
        z = CZ + 13.0 * math.sin(a)
        zh = max(1, min(MAXH, self.heights[int(z) * W + int(x)]))
        return x, zh + 3.2, z

    def _mode_target(self, t):
        """当前机位的自动参数 (yaw, pitch, dist, tx, ty, tz)。"""
        if self.mode == 0:
            # 环绕俯瞰：缓慢自转，注视世界中心
            return (t * 9.0, 30.0, 48.0, CX, 7.0, CZ)
        x0, y0, z0 = self._roam_point(t)
        x1, y1, z1 = self._roam_point(t + 0.8)
        head = math.atan2(z1 - z0, x1 - x0)      # 行进方向（OrbitCamera 的 yaw 从 +X 量起）
        # 眼点要落在行进方向的反侧 -> 镜头顺着路看过去
        yaw = math.degrees(head) + 180.0
        return (yaw, 16.0, 19.0, x0, y0, z0)

    def update(self, dt):
        if self.paused:
            return
        self.auto.absorb()
        if self.auto_mode:
            want = 0 if (self.time / self.DURATION) < self.AUTO_SWITCH else 1
            self.mode = want
        yaw, pitch, dist, tx, ty, tz = self._mode_target(self.time)
        yaw = _nearest_angle(self._auto[0], yaw)     # 切机位不绕远路
        k = min(1.0, dt * 1.4)                       # 平滑过渡（切机位不瞬移）
        want = (yaw, pitch, dist, tx, ty, tz)
        for i in range(6):
            self._auto[i] += (want[i] - self._auto[i]) * k
        if self.mode == 1:
            # 漫游时眼点可能陷进山体：抬高俯仰直到离地
            ex = self._auto[3] + self._auto[2] * math.cos(math.radians(self._auto[1]))
            ez = self._auto[5] + self._auto[2] * math.sin(math.radians(self._auto[1]))
            ey = self._auto[4] + self._auto[2] * math.sin(math.radians(self._auto[1]))
            xi, zi = int(min(W - 1, max(0, ex))), int(min(W - 1, max(0, ez)))
            if ey < self.heights[zi * W + xi] + 2.0:
                self._auto[1] = min(PITCH_MAX, self._auto[1] + 60.0 * k)
        self.camera.target = Vec3(self._auto[3], self._auto[4], self._auto[5])
        self.auto.apply(self._auto[0], self._auto[1], self._auto[2])

    def render(self):
        self.window.view = self.camera.view_matrix()
        self.program.use()
        vlist = self.mesh[self.cull]
        if self.wire:
            glPolygonMode(GL_FRONT_AND_BACK, GL_LINE)
            vlist.draw(GL_TRIANGLES)
            # 必须还原：HUD 的文字也是三角形，留在 GL_LINE 会变成空心轮廓
            glPolygonMode(GL_FRONT_AND_BACK, GL_FILL)
        else:
            vlist.draw(GL_TRIANGLES)

    # --------------------------------------------------------------- 输入 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol in (key._1, key._2):
            self.auto_mode = False
            self.mode = symbol - key._1
        elif symbol == key.F:
            self.wire = not self.wire
        elif symbol == key.H:
            self.cull = not self.cull

    def status_text(self):
        ex, allf = self.faces[True], self.faces[False]
        cut = 100.0 * (1.0 - ex / max(1, allf))
        name = ('俯瞰' if self.mode == 0 else '漫游') + ('自动' if self.auto_mode
                                                      else '手动')
        return (f'体素 {self.voxels}   面 暴露 {ex} / 全部 {allf}   '
                f'剔除 {cut:4.1f}%   画 {"暴露面" if self.cull else "全部面"}   '
                f'{name}   线框 {"开" if self.wire else "关"}   '
                f't={self.time:4.1f}s/{self.DURATION:.0f}s   {self.fps:5.1f} fps'
                f'{"  [PAUSED]" if self.paused else ""}')


if __name__ == '__main__':
    run(VoxelDemo)

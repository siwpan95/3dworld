# -*- coding: utf-8 -*-
"""程序化地形（值噪声 fBm 高程场 + 分层烘焙色 + 逐行网格发射）。

自 ``examples/terrain_flyover.py`` 上提（2026-10-15 demo 剥离）：demo 删除后，
本模块是「程序化地形」这项能力的引擎侧落点；``tests/run_tests.py`` 的
``test_terrain_row_emitter`` 直接导入本模块的函数做逐位断言。

设计约束（与原 demo 一致，改动即破坏测试契约）
----------------------------------------------
- **纯函数、零依赖**：只 import ``math``，不碰 pyglet / GL / 随机数全局状态；
  同 ``(坐标, seed)`` 必然得到同一高程 —— R 键重建与跨进程截图因此可复现。
- **哈希用整数位运算**（不是 ``fract(sin(x)*43758.5)``）：sin 的精度在不同
  驱动/编译器下不一致，位运算在所有平台逐位相同。
- **顶点色 CPU 烘焙**：高度分层（水/沙/草/岩/雪）+ 坡度（陡处露岩）+ 面向光
  亮度，片元着色器只做雾混入 —— 地形纹理不在 GPU 里算。
- **``emit_row`` 是渐进重建的核心**：把第 j 行四边形带写成三角形汤；逐行拼接
  与整块拼接必须逐位相同（测试钉住），否则重建完的地形会与同步版本不一致。
- **颜色必须 4 元组**：着色器 ``colors`` 属性是 ``vec4``，3 元组会让顶点缓冲
  少 1/4 的 float（pyglet 报 ``Invalid data size for 'colors'``）。
"""
from __future__ import annotations

import math

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
    b = n01 + (n11 - n01) * uy
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


def emit_row(pos, col, hs, cols, n, step, ox, oz, j):
    """把第 j 行四边形带写成三角形汤，追加到 pos/col（纯函数，可无窗口单测）。

    每个四边形按 (a,b,d) + (a,d,c) 两个三角形展开；顶点色直接取格点色，
    于是相邻四边形共享同一套颜色 —— 这就是"顶点色平滑着色"的来源。

    渐进重建把整张地形拆成"每帧啃几行"，本函数就是那一行的装配单元；
    逐行拼接与整块拼接逐位相同由 ``test_terrain_row_emitter`` 钉住。
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


# ================================================================== 着色器 ==
# 顶点色 CPU 已烘焙（分层色 x 面向光）；片元只做按视距混雾。
# 雾色必须等于清屏色（glClearColor），否则地平线出现一条硬边。
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

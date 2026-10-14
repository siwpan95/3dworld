# -*- coding: utf-8 -*-
"""点云渲染 + EDL（P2-4）。

**合规**：参考 Easy3D `drawable_points.cpp`（imposter 概念）与 `eye_dome_lighting.cpp`
（EDL 用于**无算法线**的点云轮廓增强）的**思路**，**独立实现**；未借鉴其代码（GPL-3.0）。

两块内容：

**① 点云**：``generate_*`` 系列程序化生成点云（零外部素材）。
   绘制走 ``gl_PointSize`` + 片元内圆形裁剪（"imposter 圆盘"）——
   比 quad imposter 简单，代价是点大小受驱动上限约束（通常 ≥ 63，够用）。

**② EDL**（Eye-Dome Lighting）：比较**邻域深度**与自身深度，累加出暗边::

       Σ = Σ_nb max(0, (z_self - z_nb)) / z_self        （邻域"更近"才贡献）
       edl = exp(-k · Σ)

   效果：点云从"一团散点"变成"有体积感的实体"（轮廓处变暗）。
   本模块提供 GLSL 片段 + **CPU 参考实现**（测试对照）。
"""
from __future__ import annotations

import math
import random


# ============================================================ 程序化点云
def generate_terrain_cloud(n=40000, half=18.0, seed=20261011) -> tuple:
    """点云地形：值噪声高度的 XZ 网格上撒点，返回 ``(positions, colors, sizes)``。

    positions 扁平 ``[x,y,z,...]``；colors 扁平 ``[r,g,b,...]``；sizes 每点一个。
    """
    rng = random.Random(seed)

    def val_noise(x, z, f):
        return (math.sin(x * f + 1.3) * math.cos(z * f * 0.9 - 0.7)
                + 0.5 * math.sin(x * f * 2.1 + z * f * 1.7) * 0.5)

    pos, col, siz = [], [], []
    side = int(math.isqrt(n))
    for i in range(side):
        for j in range(side):
            x = (i / max(1, side - 1) - 0.5) * 2.0 * half + rng.uniform(-0.18, 0.18)
            z = (j / max(1, side - 1) - 0.5) * 2.0 * half + rng.uniform(-0.18, 0.18)
            h = 3.4 * val_noise(x, z, 0.28) + 1.2 * val_noise(x, z, 0.9) \
                + 0.45 * val_noise(x, z, 2.6)
            y = h + 4.0
            pos.extend((x, y, z))
            # 高度着色：低处蓝绿 -> 高处雪白
            t = max(0.0, min(1.0, (h + 5.0) / 10.0))
            col.extend((0.25 + 0.65 * t, 0.45 + 0.45 * t, 0.70 + 0.25 * t))
            siz.append(1.7 + 0.8 * rng.random())
    return pos, col, siz


def generate_helix_cloud(n=30000, turns=7.0, radius=6.0, height=12.0, seed=7) -> tuple:
    """双螺旋点云（"能量柱"），返回 ``(positions, colors, sizes)``。"""
    rng = random.Random(seed)
    pos, col, siz = [], [], []
    for i in range(n):
        t = i / n
        a = turns * 2.0 * math.pi * t
        for branch, tint in ((0.0, (0.35, 0.85, 1.0)), (math.pi, (1.0, 0.45, 0.75))):
            r = radius * (1.0 + 0.12 * math.sin(a * 3.0 + branch))
            x = r * math.cos(a + branch) + rng.uniform(-0.12, 0.12)
            z = r * math.sin(a + branch) + rng.uniform(-0.12, 0.12)
            y = height * t + rng.uniform(-0.06, 0.06)
            pos.extend((x, y, z))
            col.extend(tint)
            siz.append(1.6 + 1.1 * rng.random())
    return pos, col, siz


# ================================================================== EDL
# 8 邻域（3x3 去中心），用于 depth 差的求和
EDL_NEIGHBORS = ((-1, -1), (0, -1), (1, -1),
                 (-1, 0), (1, 0),
                 (-1, 1), (0, 1), (1, 1))


def edl_glsl(radius: float = 1.5) -> str:
    """EDL 全屏 pass 的 GLSL（片元）。宿主需提供 ``uDepth``(sampler2D)、``uTexel``(vec2)。"""
    nb = ',\n        '.join(f'vec2({dx}.0, {dy}.0)' for dx, dy in EDL_NEIGHBORS)
    return f'''#version 330 core
in vec2 vNdc;
layout(location = 0) out vec4 outColor;
uniform sampler2D uDepth;
uniform sampler2D uColor;
uniform vec2  uTexel;
uniform float uStrength;      // k：越大轮廓越强
uniform float uRadius;        // 邻域半径（纹素）

const vec2 NB[8] = vec2[8](
        {nb}
);

void main() {{
    vec2 uv = vNdc * 0.5 + 0.5;
    float z = texture(uDepth, uv).r;
    float sum = 0.0;
    for (int i = 0; i < 8; ++i) {{
        float zn = texture(uDepth, uv + NB[i] * uRadius * uTexel).r;
        // 邻域"更近"（zn < z）才贡献：轮廓处邻域通常是背景（更远）或更近的实体
        sum += max(0.0, z - zn) / max(1e-5, z);
    }}
    float shade = exp(-uStrength * sum);
    vec3 c = texture(uColor, uv).rgb * clamp(shade, 0.0, 1.0);
    outColor = vec4(c, 1.0);
}}'''


def cpu_edl(z: float, neighbor_zs, strength: float) -> float:
    """CPU 参考实现（测试对照用）：返回该像素的 EDL 明暗系数。"""
    s = 0.0
    for zn in neighbor_zs:
        if zn < z:
            s += (z - zn) / max(1e-5, z)
    return math.exp(-strength * s)


__all__ = ['generate_terrain_cloud', 'generate_helix_cloud',
           'edl_glsl', 'cpu_edl', 'EDL_NEIGHBORS']

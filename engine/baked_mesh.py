# -*- coding: utf-8 -*-
"""烘焙明暗的简单几何（单位立方体 / 单位球）—— "position + shade 三角汤" 格式。

**P0-3 试点转正**：这两个函数原先定义在 ``experiments/cordis/tower_defense_kernel.py``，
却被 fps / puzzle / storm / hall / pulse / void **六个玩法内核横向 import** ——
游戏之间互相依赖，违反"依赖只允许向下"的铁律（见 `docs/5_引擎化改造规划.md` §3.1）。
它们本身是纯几何（只 import math、不碰 GL），因此上提到 engine 层：
调用方从"依赖另一个游戏"变成"依赖引擎"。

输出格式：``(positions, shades)`` 两个等长列表，直接喂 ``RenderService.upload_mesh``
（``GL_TRIANGLES``；每个顶点烘焙一个明度值，作为"不做光照计算"的替代）。
原先的私有名 ``_unit_cube`` / ``_unit_sphere`` 在这里改为公开名，调用方同步更新。

⚠️ **面的明暗值是历史值（1.00 / 0.82 / 0.75 / 0.50），搬运时逐字未改** ——
它们直接决定 td / fps / storm / boxward / stillhall / pulsefall / voidward 的出图效果。
另注：``engine/greedy_mesh.py`` 的合并 quad 用的是自己那组常量（1.00 / 0.82 / 0.72），
与本模块**不是同一组数**，两者各自独立、都属"烘焙 shade"家族。
"""
from __future__ import annotations

import math

__all__ = ['unit_cube', 'unit_sphere']


def unit_cube():
    """单位立方体（边长 1、原点居中）→ ``(positions, shades)``。

    六面各自烘焙一个明度：前 +z = 1.00、后 −z = 0.50、上/下 = 0.82、左右 ±x = 0.75。
    每面两个三角形（a,b,c / a,c,d），顶点顺序与原来的实现逐字一致。
    """
    faces = [
        ([(-.5, -.5, +.5), (+.5, -.5, +.5), (+.5, +.5, +.5), (-.5, +.5, +.5)], 1.00),
        ([(-.5, -.5, -.5), (+.5, -.5, -.5), (+.5, +.5, -.5), (-.5, +.5, -.5)], 0.50),
        ([(-.5, -.5, +.5), (+.5, -.5, +.5), (+.5, -.5, -.5), (-.5, -.5, -.5)], 0.82),
        ([(-.5, +.5, +.5), (+.5, +.5, +.5), (+.5, +.5, -.5), (-.5, +.5, -.5)], 0.82),
        ([(+.5, -.5, +.5), (+.5, -.5, -.5), (+.5, +.5, -.5), (+.5, +.5, +.5)], 0.75),
        ([(-.5, -.5, +.5), (-.5, -.5, -.5), (-.5, +.5, -.5), (-.5, +.5, +.5)], 0.75),
    ]
    pos, shade = [], []
    for quad, s in faces:
        a, b, c, d = quad
        for p in (a, b, c, a, c, d):
            pos.extend(p)
            shade.extend((s, s, s))
    return pos, shade


def unit_sphere(seg=10, rings=8):
    """单位球（半径 1）→ ``(positions, shades)``，纬度渐变明暗（0.6..1.0）。"""
    pos, shade = [], []
    for i in range(rings):
        lat0 = (i / rings - 0.5) * math.pi
        lat1 = ((i + 1) / rings - 0.5) * math.pi
        for j in range(seg):
            lon0 = (j / seg) * 2 * math.pi
            lon1 = ((j + 1) / seg) * 2 * math.pi
            a = (math.cos(lat0) * math.cos(lon0), math.sin(lat0), math.cos(lat0) * math.sin(lon0))
            b = (math.cos(lat0) * math.cos(lon1), math.sin(lat0), math.cos(lat0) * math.sin(lon1))
            c = (math.cos(lat1) * math.cos(lon1), math.sin(lat1), math.cos(lat1) * math.sin(lon1))
            d = (math.cos(lat1) * math.cos(lon0), math.sin(lat1), math.cos(lat1) * math.sin(lon0))
            for (v, lat) in ((a, lat0), (b, lat0), (c, lat1), (a, lat0), (c, lat1), (d, lat1)):
                pos.extend(v)
                sh = 0.6 + 0.4 * (math.sin(lat) * 0.5 + 0.5)
                shade.extend((sh, sh, sh))
    return pos, shade

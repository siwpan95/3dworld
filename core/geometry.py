# -*- coding: utf-8 -*-
"""几何构建（pyglet 2.x ShaderProgram.vertex_list API）。

注意：默认着色器属性为 position(vec3) / colors(vec4 RGBA)，
1.x 的 Batch.add/c3f 已在 2.1.16 移除，勿回退。

- make_ground：静态地面，入 Batch（默认着色器）
- make_cube_list：可动立方体，不入 Batch（动画着色器 + model 矩阵逐帧驱动）
"""
import math
from pyglet.gl import GL_TRIANGLES


def _face_shade(normal):
    """法线与主光方向的点积，用于简单的面向光照明。"""
    light = (0.5, 0.8, 0.3)
    nl = math.sqrt(sum(c * c for c in normal))
    ll = math.sqrt(sum(c * c for c in light))
    return sum(a * b for a, b in zip(normal, light)) / (nl * ll)


def cube_data(cx, cy, cz, s, rgb):
    """以 (cx,cy,cz) 为中心、边长 s 的立方体几何数据（36 顶点，法线调亮度）。"""
    h = s / 2.0
    faces = [
        ((0, 0, 1),  (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)),    # 前
        ((0, 0, -1), (h, -h, -h), (-h, -h, -h), (-h, h, -h), (h, h, -h)),# 后
        ((0, 1, 0),  (-h, h, h), (h, h, h), (h, h, -h), (-h, h, -h)),    # 上
        ((0, -1, 0), (-h, -h, -h), (h, -h, -h), (h, -h, h), (-h, -h, h)),# 下
        ((1, 0, 0),  (h, -h, h), (h, -h, -h), (h, h, -h), (h, h, h)),    # 右
        ((-1, 0, 0), (-h, -h, -h), (-h, -h, h), (-h, h, h), (-h, h, -h)),# 左
    ]
    verts, colors = [], []
    for n, a, b, c, d in faces:
        k = 0.4 + 0.6 * abs(_face_shade(n))
        for x, y, z in (a, b, c, a, c, d):
            verts += [cx + x, cy + y, cz + z]
            colors += [rgb[0] * k, rgb[1] * k, rgb[2] * k, 1.0]
    return verts, colors


def make_cube_list(program, cx, cy, cz, s, rgb):
    """创建独立顶点列表（不进 Batch，由动画着色器逐帧 model 矩阵驱动）。"""
    verts, colors = cube_data(cx, cy, cz, s, rgb)
    return program.vertex_list(36, GL_TRIANGLES,
                               position=('f', verts), colors=('f', colors))


def make_ground(program, batch, half=10, tile=1.0):
    """以原点为中心铺一块棋盘格地面（GL_TRIANGLES），返回顶点列表。"""
    verts, colors = [], []
    for ix in range(-half, half):
        for iz in range(-half, half):
            x0, z0 = ix * tile, iz * tile
            x1, z1 = x0 + tile, z0 + tile
            quad = [(x0, 0.0, z0), (x1, 0.0, z0), (x1, 0.0, z1),
                    (x0, 0.0, z0), (x1, 0.0, z1), (x0, 0.0, z1)]
            verts += [c for p in quad for c in p]
            g = 0.55 if (ix + iz) % 2 == 0 else 0.35
            colors += [g * 0.8, g, g * 0.8, 1.0] * 6
    return program.vertex_list(len(verts) // 3, GL_TRIANGLES, batch=batch,
                               position=('f', verts), colors=('f', colors))

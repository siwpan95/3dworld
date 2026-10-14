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


def box_data(sx, sy, sz, ox, oy, oz, rgb):
    """盒体几何数据：尺寸 (sx,sy,sz)，中心偏移 (ox,oy,oz)，法线调亮度。

    骨骼段用法：局部原点设在关节（枢轴），几何沿 +Y 展开——
    例如 make_box_list(0.4, 2.4, 0.4, 0, 1.2, 0) 的旋转枢轴在盒体底部。
    """
    hx, hy, hz = sx / 2.0, sy / 2.0, sz / 2.0
    faces = [
        ((0, 0, 1),  (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)),
        ((0, 0, -1), (hx, -hy, -hz), (-hx, -hy, -hz), (-hx, hy, -hz), (hx, hy, -hz)),
        ((0, 1, 0),  (-hx, hy, hz), (hx, hy, hz), (hx, hy, -hz), (-hx, hy, -hz)),
        ((0, -1, 0), (-hx, -hy, -hz), (hx, -hy, -hz), (hx, -hy, hz), (-hx, -hy, hz)),
        ((1, 0, 0),  (hx, -hy, hz), (hx, -hy, -hz), (hx, hy, -hz), (hx, hy, hz)),
        ((-1, 0, 0), (-hx, -hy, -hz), (-hx, -hy, hz), (-hx, hy, hz), (-hx, hy, -hz)),
    ]
    verts, colors = [], []
    for n, a, b, c, d in faces:
        k = 0.4 + 0.6 * abs(_face_shade(n))
        for x, y, z in (a, b, c, a, c, d):
            verts += [ox + x, oy + y, oz + z]
            colors += [rgb[0] * k, rgb[1] * k, rgb[2] * k, 1.0]
    return verts, colors


def cube_data(cx, cy, cz, s, rgb):
    """以 (cx,cy,cz) 为中心、边长 s 的立方体几何数据（36 顶点，法线调亮度）。"""
    return box_data(s, s, s, cx, cy, cz, rgb)


def make_box_list(program, sx, sy, sz, ox, oy, oz, rgb):
    """创建带枢轴偏移的盒体顶点列表（不入 Batch，model 矩阵逐帧驱动）。"""
    verts, colors = box_data(sx, sy, sz, ox, oy, oz, rgb)
    return program.vertex_list(36, GL_TRIANGLES,
                               position=('f', verts), colors=('f', colors))


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
            # 冷灰蓝石板：早期版本是偏绿的灰（g*0.8, g, g*0.8），与天空蓝对冲，
            # 满屏地面时很晃眼；改成同色系冷灰后，琥珀色的动态物体才跳得出来。
            colors += [g * 0.90, g * 0.94, g, 1.0] * 6
    return program.vertex_list(len(verts) // 3, GL_TRIANGLES, batch=batch,
                               position=('f', verts), colors=('f', colors))


def make_static_boxes(program, batch, boxes):
    """把一批**静态**盒体合并成一个顶点列表（入 Batch，一次 draw call）。

    Args:
        program: 着色器程序（默认着色器即可；本函数不使用 model uniform）
        batch:   目标 Batch
        boxes:   [(sx, sy, sz, ox, oy, oz, rgb), ...]，盒体尺寸 + 中心偏移 + 颜色

    场景装饰（立柱 / 台座 / 门拱 / 灯带）都走这里：CPU 端合并、GPU 端一次画完，
    不会因为装饰件多而增加 draw call。
    """
    verts, colors = [], []
    for sx, sy, sz, ox, oy, oz, rgb in boxes:
        v, c = box_data(sx, sy, sz, ox, oy, oz, rgb)
        verts += v
        colors += c
    return program.vertex_list(len(verts) // 3, GL_TRIANGLES, batch=batch,
                               position=('f', verts), colors=('f', colors))

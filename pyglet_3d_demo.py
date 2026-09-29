# -*- coding: utf-8 -*-
"""
pyglet 三维世界示例（pyglet 2.x，现代可编程管线）
====================================================
构成一个"简单三维世界"的四个要素：
  1. 窗口 + 透视投影（Mat4.perspective_projection）
  2. 几何体：地面棋盘格 + 若干彩色立方体（Batch 批处理）
  3. 相机：轨道相机（窗口 view 矩阵，鼠标拖动旋转 / 滚轮缩放）
  4. 主循环：pyglet.app.run() + clock 驱动动画

操作：鼠标左键拖动旋转视角，滚轮缩放，ESC 退出。
冒烟测试：python pyglet_3d_demo.py --smoke  （2 秒后自动关闭）
"""
import sys
import math
import pyglet
from pyglet.gl import *
from pyglet.math import Mat4, Vec3

WIDTH, HEIGHT = 960, 540

# ---------------------------------------------------------------- 几何工具 --
def cube_vertices(cx, cy, cz, s):
    """返回以 (cx,cy,cz) 为中心、边长 s 的立方体 36 个三角面顶点（12 个三角形）。"""
    h = s / 2.0
    # 每面 4 个角点，按两个三角形展开
    faces = [
        # (法线方向用于着色深浅, 4 个角点)
        ((0, 0, 1),  (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)),    # 前
        ((0, 0, -1), (h, -h, -h), (-h, -h, -h), (-h, h, -h), (h, h, -h)),# 后
        ((0, 1, 0),  (-h, h, h), (h, h, h), (h, h, -h), (-h, h, -h)),    # 上
        ((0, -1, 0), (-h, -h, -h), (h, -h, -h), (h, -h, h), (-h, -h, h)),# 下
        ((1, 0, 0),  (h, -h, h), (h, -h, -h), (h, h, -h), (h, h, h)),    # 右
        ((-1, 0, 0), (-h, -h, -h), (-h, -h, h), (-h, h, h), (-h, h, -h)),# 左
    ]
    tris = []
    normals = []
    for n, a, b, c, d in faces:
        tris += [a, b, c, a, c, d]
        normals += [n] * 6
    verts = [(cx + x, cy + y, cz + z) for x, y, z in tris]
    flat = [v for tri in verts for v in tri]
    shade = [(0.4 + 0.6 * abs(dot(n))) for n in normals]  # 依法线朝向调亮度
    return flat, shade

def dot(n):
    """法线与主光方向的点积，用于简单的面向光照明（代替固定管线光照）。"""
    light = (0.5, 0.8, 0.3)
    nl = math.sqrt(sum(c * c for c in n))
    ll = math.sqrt(sum(c * c for c in light))
    return sum(a * b for a, b in zip(n, light)) / (nl * ll)

def add_colored_cube(batch, cx, cy, cz, s, rgb):
    flat, shade = cube_vertices(cx, cy, cz, s)
    colors = []
    for k in shade:
        colors += [rgb[0] * k, rgb[1] * k, rgb[2] * k]
    batch.add(36, GL_TRIANGLES, None,
              ('v3f', flat),
              ('c3f', colors))

def add_ground(batch, half=10, tile=1.0):
    """以原点为中心铺一块棋盘格地面（GL_TRIANGLES）。"""
    verts, colors = [], []
    for ix in range(-half, half):
        for iz in range(-half, half):
            x0, z0 = ix * tile, iz * tile
            x1, z1 = x0 + tile, z0 + tile
            y = 0.0
            # 两个三角形组成一个方形地块
            quad = [(x0, y, z0), (x1, y, z0), (x1, y, z1),
                    (x0, y, z0), (x1, y, z1), (x0, y, z1)]
            verts += [c for p in quad for c in p]
            g = 0.55 if (ix + iz) % 2 == 0 else 0.35
            colors += [g * 0.8, g, g * 0.8] * 6
    batch.add(len(verts) // 3, GL_TRIANGLES, None,
              ('v3f', verts), ('c3f', colors))

# ------------------------------------------------------------------- 窗口 --
def look_at_matrix(eye, target, up):
    """手写 look-at 视图矩阵（仅依赖 Vec3 的 x/y/z 属性，跨 pyglet 2.x 版本稳定）。

    部分旧版 pyglet（如 2.1.0~2.1.x 过渡版）的 Mat4.look_at 存在
    cross_product/cross 改名不一致的缺陷，这里用纯算术实现规避。
    矩阵构造参数为列主序，布局与官方实现一致。
    """
    # 纯标量运算：f = normalize(target - eye)（视线方向）
    fx, fy, fz = target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]
    fl = math.sqrt(fx * fx + fy * fy + fz * fz)
    fx, fy, fz = fx / fl, fy / fl, fz / fl
    # s = normalize(f × up)（相机右方向）
    sx, sy, sz = fy * up[2] - fz * up[1], fz * up[0] - fx * up[2], fx * up[1] - fy * up[0]
    sl = math.sqrt(sx * sx + sy * sy + sz * sz)
    sx, sy, sz = sx / sl, sy / sl, sz / sl
    # u = s × f（重算的相机上方向）
    ux, uy, uz = sy * fz - sz * fy, sz * fx - sx * fz, sx * fy - sy * fx
    return Mat4(
        sx, ux, -fx, 0.0,
        sy, uy, -fy, 0.0,
        sz, uz, -fz, 0.0,
        -(sx * eye[0] + sy * eye[1] + sz * eye[2]),
        -(ux * eye[0] + uy * eye[1] + uz * eye[2]),
        (fx * eye[0] + fy * eye[1] + fz * eye[2]),
        1.0)


class WorldWindow(pyglet.window.Window):
    def __init__(self, **kw):
        super().__init__(WIDTH, HEIGHT, caption='pyglet 简单三维世界',
                         resizable=True, vsync=True, **kw)
        self.batch = pyglet.graphics.Batch()
        glEnable(GL_DEPTH_TEST)          # 深度测试：保证近处遮挡远处
        glClearColor(0.53, 0.81, 0.92, 1)  # 天空蓝背景

        # --- 场景内容：地面 + 一圈彩色立方体 ---
        add_ground(self.batch, half=10)
        palette = [(0.9, 0.3, 0.3), (0.3, 0.6, 0.9), (0.95, 0.75, 0.2),
                   (0.4, 0.75, 0.4), (0.7, 0.5, 0.85)]
        for i in range(10):
            ang = i / 10 * 2 * math.pi
            add_colored_cube(self.batch,
                             math.cos(ang) * 5, 0.5, math.sin(ang) * 5,
                             1.0, palette[i % len(palette)])
        add_colored_cube(self.batch, 0, 1.0, 0, 2.0, (0.95, 0.95, 0.95))

        # --- 轨道相机参数 ---
        self.yaw, self.pitch, self.dist = 45.0, 30.0, 18.0
        self.target = Vec3(0, 1, 0)
        self.time = 0.0
        self._update_camera()

    # ---------- 相机 ----------
    def _update_camera(self):
        yr, pr = math.radians(self.yaw), math.radians(self.pitch)
        eye = Vec3(
            self.target.x + self.dist * math.cos(pr) * math.cos(yr),
            self.target.y + self.dist * math.sin(pr),
            self.target.z + self.dist * math.cos(pr) * math.sin(yr))
        self.view_mat = look_at_matrix(eye, self.target, Vec3(0, 1, 0))
        self.view = self.view_mat

    # ---------- 事件 ----------
    def on_resize(self, width, height):
        self.viewport = (0, 0, width, height)
        self.projection = Mat4.perspective_projection(
            self.aspect_ratio, z_near=0.1, z_far=255, fov=60)
        return pyglet.event.EVENT_HANDLED   # 覆盖默认的正交投影

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.yaw += dx * 0.4
        self.pitch = max(5.0, min(89.0, self.pitch + dy * 0.4))
        self._update_camera()

    def on_mouse_scroll(self, x, y, sx, sy):
        self.dist = max(4.0, min(60.0, self.dist - sy * 2))
        self._update_camera()

    def on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            self.close()

    # ---------- 每帧 ----------
    def update(self, dt):
        self.time += dt
        # 让白色大立方体缓慢自转（重建立方体几何，教学演示用简单做法）
        self.yaw += dt * 8.0            # 相机缓慢环绕，产生"世界在转"的效果
        self._update_camera()

    def on_draw(self):
        self.clear()
        self.batch.draw()

# -------------------------------------------------------------------- 入口 --
if __name__ == '__main__':
    win = WorldWindow()
    pyglet.clock.schedule_interval(win.update, 1 / 60.0)
    if '--smoke' in sys.argv:            # 冒烟测试：2 秒后自动关闭
        pyglet.clock.schedule_once(lambda dt: win.close(), 2.0)
    pyglet.app.run()
    print('EXIT_OK')

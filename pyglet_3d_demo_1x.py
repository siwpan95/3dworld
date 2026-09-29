# -*- coding: utf-8 -*-
"""
pyglet 三维世界示例 —— pyglet 1.x 固定管线版（适配 cocos2d 环境）
================================================================
cocos2d（Python 版）依赖 pyglet 1.x，本版本使用 1.x 的固定管线 API：
gluPerspective / glMatrixMode / glRotatef，与 cocos2d 同环境共存。

操作：鼠标左键拖动旋转视角，滚轮缩放，ESC 退出。
冒烟测试：python pyglet_3d_demo_1x.py --smoke  （2 秒后自动关闭）

注意：本文件不依赖 pyglet.math（2.0 才有），纯 1.x 可运行。
"""
import sys
import math
import pyglet
from pyglet.gl import *

WIDTH, HEIGHT = 960, 540

# ---------------------------------------------------------------- 几何工具 --
def add_colored_cube(batch, cx, cy, cz, s, rgb):
    """以 (cx,cy,cz) 为中心、边长 s 的立方体：12 个三角形，法线朝向调亮度。"""
    h = s / 2.0
    faces = [
        ((0, 0, 1),  (-h, -h, h), (h, -h, h), (h, h, h), (-h, h, h)),    # 前
        ((0, 0, -1), (h, -h, -h), (-h, -h, -h), (-h, h, -h), (h, h, -h)),# 后
        ((0, 1, 0),  (-h, h, h), (h, h, h), (h, h, -h), (-h, h, -h)),    # 上
        ((0, -1, 0), (-h, -h, -h), (h, -h, -h), (h, -h, h), (-h, -h, h)),# 下
        ((1, 0, 0),  (h, -h, h), (h, -h, -h), (h, h, -h), (h, h, h)),    # 右
        ((-1, 0, 0), (-h, -h, -h), (-h, -h, h), (-h, h, h), (-h, h, -h)),# 左
    ]
    light = (0.5, 0.8, 0.3)
    verts, colors = [], []
    for n, a, b, c, d in faces:
        tris = [a, b, c, a, c, d]
        nl = math.sqrt(sum(v * v for v in n))
        ll = math.sqrt(sum(v * v for v in light))
        k = 0.4 + 0.6 * abs(sum(x * y for x, y in zip(n, light)) / (nl * ll))
        for x, y, z in tris:
            verts += [cx + x, cy + y, cz + z]
            colors += [rgb[0] * k, rgb[1] * k, rgb[2] * k]
    batch.add(36, GL_TRIANGLES, None, ('v3f', verts), ('c3f', colors))

def add_ground(batch, half=10, tile=1.0):
    """以原点为中心铺一块棋盘格地面。"""
    verts, colors = [], []
    for ix in range(-half, half):
        for iz in range(-half, half):
            x0, z0 = ix * tile, iz * tile
            x1, z1 = x0 + tile, z0 + tile
            y = 0.0
            quad = [(x0, y, z0), (x1, y, z0), (x1, y, z1),
                    (x0, y, z0), (x1, y, z1), (x0, y, z1)]
            verts += [c for p in quad for c in p]
            g = 0.55 if (ix + iz) % 2 == 0 else 0.35
            colors += [g * 0.8, g, g * 0.8] * 6
    batch.add(len(verts) // 3, GL_TRIANGLES, None, ('v3f', verts), ('c3f', colors))

# ------------------------------------------------------------------- 窗口 --
class WorldWindow(pyglet.window.Window):
    def __init__(self, **kw):
        super(WorldWindow, self).__init__(
            WIDTH, HEIGHT, caption='pyglet 简单三维世界 (1.x 固定管线)',
            resizable=True, vsync=True, **kw)
        self.batch = pyglet.graphics.Batch()
        glEnable(GL_DEPTH_TEST)            # 深度测试：近处遮挡远处
        glClearColor(0.53, 0.81, 0.92, 1)  # 天空蓝背景

        add_ground(self.batch, half=10)
        palette = [(0.9, 0.3, 0.3), (0.3, 0.6, 0.9), (0.95, 0.75, 0.2),
                   (0.4, 0.75, 0.4), (0.7, 0.5, 0.85)]
        for i in range(10):
            ang = i / 10.0 * 2 * math.pi
            add_colored_cube(self.batch,
                             math.cos(ang) * 5, 0.5, math.sin(ang) * 5,
                             1.0, palette[i % len(palette)])
        add_colored_cube(self.batch, 0, 1.0, 0, 2.0, (0.95, 0.95, 0.95))

        # 轨道相机参数（固定管线：直接喂给 glRotatef/glTranslatef）
        self.yaw, self.pitch, self.dist = 45.0, 30.0, 18.0

    # ---------- 1.x 固定管线：在 on_resize 设置透视投影 ----------
    def on_resize(self, width, height):
        glViewport(0, 0, width, height)
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        gluPerspective(60.0, width / float(height), 0.1, 255.0)
        glMatrixMode(GL_MODELVIEW)
        return pyglet.event.EVENT_HANDLED   # 覆盖默认的正交投影

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.yaw += dx * 0.4
        self.pitch = max(5.0, min(89.0, self.pitch + dy * 0.4))

    def on_mouse_scroll(self, x, y, sx, sy):
        self.dist = max(4.0, min(60.0, self.dist - sy * 2))

    def on_key_press(self, symbol, modifiers):
        if symbol == pyglet.window.key.ESCAPE:
            self.close()

    def update(self, dt):
        self.yaw += dt * 8.0                # 相机缓慢环绕

    def on_draw(self):
        self.clear()
        glLoadIdentity()
        # 固定管线的"轨道相机"：平移到远处 → 俯仰 → 偏航（逆序应用）
        glTranslatef(0.0, -1.0, -self.dist)
        glRotatef(self.pitch, 1.0, 0.0, 0.0)
        glRotatef(self.yaw, 0.0, 1.0, 0.0)
        self.batch.draw()

# -------------------------------------------------------------------- 入口 --
if __name__ == '__main__':
    win = WorldWindow()
    pyglet.clock.schedule_interval(win.update, 1 / 60.0)
    if '--smoke' in sys.argv:
        pyglet.clock.schedule_once(lambda dt: win.close(), 2.0)
    pyglet.app.run()
    print('EXIT_OK')

# -*- coding: utf-8 -*-
"""GPU-Driven Rendering demo（P3-1）—— GPU 剔除 + GPU LOD + 命令生成。

一场几万个实例的"方块/球"阵列：**剔除、LOD 选择、绘制命令生成全在 GPU 的 compute
里完成**，CPU 每帧只做一次 dispatch + 一次 ``glMultiDrawArraysIndirect``。

按键：``F2`` 切 GPU/CPU 剔除　``F3`` 关/开 LOD（关=全用 LOD0）　``SPACE`` 暂停
　　　拖动=旋转　滚轮=缩放　``ESC`` 退出

运行::

    python demos/gpu_cull_field.py
    python demos/gpu_cull_field.py --smoke
    python demos/gpu_cull_field.py --shot

按 P3-1 决策②：**运行时能力探测 + CPU 回退**——若 compute/indirect 不可用，自动落 CPU 路径
（画面一致，只是命令在 CPU 生成）。
"""
from __future__ import annotations

import ctypes
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pyglet                                                   # noqa: E402
from pyglet.gl import (GL_ARRAY_BUFFER, GL_COLOR_BUFFER_BIT,     # noqa: E402
                       GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST, GL_FALSE,
                       GL_FLOAT, GL_STATIC_DRAW, GL_TRIANGLES, glBindBuffer,
                       glBindVertexArray, glBufferData, glClear,
                       glClearColor, glEnable, glEnableVertexAttribArray,
                       glGenBuffers, glGenVertexArrays, glVertexAttribPointer)
from pyglet.gl import GLuint                                    # noqa: E402
from pyglet.math import Mat4, Vec3                              # noqa: E402

from engine.app import EngineApp, run                           # noqa: E402
from engine.camera import OrbitCamera                            # noqa: E402
from engine.frustum import Frustum                               # noqa: E402
from engine.geometry import sphere                               # noqa: E402
from engine.gpu_cull import GpuCullSupport, GpuDrivenField       # noqa: E402
from engine.shaders import get, get_compute                      # noqa: E402

TITLE = '3dworld GPU-Driven · 剔除+LOD+命令生成（P3-1）'
HELP = ('ESC quit  F2 GPU/CPU cull  F3 LOD  SPACE pause  '
        'drag orbit  wheel zoom')
SHOTS = ((1.0, 'docs/gpu_cull_01.png'),
         (2.0, 'docs/gpu_cull_02.png'),
         (3.0, 'docs/gpu_cull_03.png'))

N_SIDE = 64                       # 64×64 = 4096 实例（可调；几万也扛得住）
LOD_DISTS = [9.0, 20.0]           # LOD 距离阈值（升序）


class GpuCullFieldApp(EngineApp):
    TITLE = TITLE
    HELP = HELP
    SHOTS = SHOTS
    Z_FAR = 400.0

    # ------------------------------------------------------------ 构建 --
    def setup(self):
        self.info = GpuCullSupport.detect(verbose=True)
        self.use_gpu = bool(self.info['available'])

        # ---- 3 级 LOD 几何（程序化球，逐级减面），拼进一个大 VBO ----
        self.lods = [sphere(0.68, 16, 12), sphere(0.68, 10, 7), sphere(0.68, 6, 4)]
        lod_pos, lod_nrm, self.lod_data = [], [], []
        offset = 0
        for md in self.lods:
            if md.indices:
                md = md.to_non_indexed()
            if not md.normals:
                md.compute_normals()
            self.lod_data.append((offset, md.vertex_count))
            lod_pos += md.positions
            lod_nrm += md.normals
            offset += md.vertex_count

        self.prog = get('gpu_driven')
        self._build_vao(lod_pos, lod_nrm)

        # ---- 实例阵列（XZ 平面网格 + 高度起伏），世界包围球 ----
        half = N_SIDE / 2.0
        mats, centers, radii = [], [], []
        ident = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
                 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
        for ix in range(N_SIDE):
            for iz in range(N_SIDE):
                x = (ix - half + 0.5) * 1.45
                z = (iz - half + 0.5) * 1.45
                y = 1.2 + 1.0 * math.sin(x * 0.28) * math.cos(z * 0.28)
                # 模型矩阵：平移（保持单位朝向，避免每实例建矩阵的开销）
                m = list(ident)
                m[12], m[13], m[14] = x, y, z
                mats.append(tuple(m))
                centers.append((x, y, z))
                radii.append(0.72)
                # 保留位置供 CPU 路径与相机取景
        self.centers = centers
        self.radii = radii

        self.field = GpuDrivenField(mats, centers, radii, self.lod_data,
                                    get_compute('gpu_cull'), draw_program=self.prog)
        self.cam = OrbitCamera(target=(0.0, 1.2, 0.0), yaw=38.0, pitch=20.0,
                               dist=38.0, dist_range=(6.0, 400.0),
                               fov=self.FOV, far=self.Z_FAR)
        self.paused = False
        self.lod_enabled = True
        self.gpu_counts = {'visible': 0, 'lod': [0, 0, 0, 0]}
        self.frame = 0

    def _build_vao(self, pos, nrm):
        self.vao = GLuint(0)
        glGenVertexArrays(1, ctypes.byref(self.vao))
        glBindVertexArray(self.vao)

        def buf(data):
            b = GLuint(0)
            glGenBuffers(1, ctypes.byref(b))
            glBindBuffer(GL_ARRAY_BUFFER, b)
            arr = (ctypes.c_float * len(data))(*data)
            glBufferData(GL_ARRAY_BUFFER, ctypes.sizeof(arr), arr, GL_STATIC_DRAW)
            return b

        self.vbo_pos = buf(pos)
        self.vbo_nrm = buf(nrm)
        loc_pos = self.prog.attributes['position']['location']
        loc_nrm = self.prog.attributes['normals']['location']
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_pos)
        glEnableVertexAttribArray(loc_pos)
        glVertexAttribPointer(loc_pos, 3, GL_FLOAT, GL_FALSE, 0, None)
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_nrm)
        glEnableVertexAttribArray(loc_nrm)
        glVertexAttribPointer(loc_nrm, 3, GL_FLOAT, GL_FALSE, 0, None)
        glBindVertexArray(0)

    # ------------------------------------------------------------ 每帧 --
    def update(self, dt):
        if self.paused:
            return
        self.frame += 1
        self.cam.advance(dt * 0.12)                     # 缓慢环绕

    def _frustum(self):
        aspect = self.window.width / max(1, self.window.height)
        vp = self.cam.view_projection(aspect)
        return Frustum.from_view_projection(vp)

    def render(self):
        self.window.view = Mat4(*self.cam.view_matrix())
        glClearColor(0.055, 0.06, 0.075, 1.0)
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        glEnable(GL_DEPTH_TEST)

        eye = self.cam.eye
        cam_pos = (eye.x, eye.y, eye.z)
        frustum = self._frustum()
        lod_dists = LOD_DISTS if self.lod_enabled else [1e30, 1e30]

        self.prog.use()
        self.prog['uBaseColor'] = Vec3(0.32, 0.72, 0.95)
        self.prog['uLightDir'] = Vec3(0.42, 0.80, 0.44)

        if self.use_gpu:
            self.field.update(frustum, cam_pos, lod_dists)
            self.gpu_counts = self.field.read_counts()  # 决策④：本 demo 便于对照，开读回
        else:
            self.gpu_counts = self.field.cpu_update(frustum, cam_pos, lod_dists)

        self.field.bind_instance_matrix_ssbo(1)
        self.field.bind_visible_ssbo(5)
        glBindVertexArray(self.vao)
        self.field.draw()
        glBindVertexArray(0)

    # ------------------------------------------------------------ 交互 --
    def on_key(self, symbol, modifiers):
        key = pyglet.window.key
        if symbol == key.F2:
            if self.info['available']:
                self.use_gpu = not self.use_gpu
            else:
                print('[gpu_cull] compute 不可用，无法切到 GPU 路径（回退保持）')
        elif symbol == key.F3:
            self.lod_enabled = not self.lod_enabled
        elif symbol == key.SPACE:
            self.paused = not self.paused
        elif symbol == key.ESCAPE:
            self.close()
        else:
            return pyglet.event.EVENT_UNHANDLED
        return None

    def on_mouse_drag(self, x, y, dx, dy, buttons, modifiers):
        self.cam.orbit(dx, dy)

    def on_mouse_scroll(self, x, y, scroll_x, scroll_y):
        self.cam.zoom(scroll_y)

    def status_text(self):
        c = self.gpu_counts
        mode = 'GPU' if self.use_gpu else 'CPU'
        lod = 'on' if self.lod_enabled else 'off'
        cap = 'OK' if self.info['available'] else 'N/A'
        n = len(self.centers)
        return (f'{mode} 剔除 [compute {cap}] · LOD {lod} · '
                f'可见 {c["visible"]}/{n} · LOD组 {c["lod"][:3]} · '
                f'{"暂停" if self.paused else "运行"}')


def main(argv=None):
    return run(GpuCullFieldApp, argv)


if __name__ == '__main__':
    sys.exit(main())

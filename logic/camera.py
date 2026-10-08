# -*- coding: utf-8 -*-
"""轨道相机 —— 兼容再导出层。

实现已下沉到 :mod:`engine.camera`（核心引擎抽取，2026-10）。差异只有一处：
本层的 ``view_matrix()`` / ``look_at_matrix()`` 返回 pyglet ``Mat4``
（presentation 层直接赋给 ``window.view``），而 engine 版返回列主序 16 元组。
"""
from pyglet.math import Mat4, Vec3

from engine.camera import DIST_MAX, DIST_MIN, PITCH_MAX, PITCH_MIN  # noqa: F401
from engine.camera import OrbitCamera as _EngineOrbitCamera
from engine.mathkit import look_at_matrix as _look_at, mat4_to_Mat4

__all__ = ['OrbitCamera', 'look_at_matrix',
           'PITCH_MIN', 'PITCH_MAX', 'DIST_MIN', 'DIST_MAX']


def look_at_matrix(eye, target, up):
    """手写 look-at 视图矩阵（返回 Mat4，兼容旧签名）。"""
    return mat4_to_Mat4(_look_at(eye, target, up))


class OrbitCamera(_EngineOrbitCamera):
    """轨道相机。engine 实现 + Mat4 视图矩阵 + Vec3 target 兼容。"""

    def __init__(self, target=Vec3(0, 1, 0), yaw=45.0, pitch=30.0, dist=18.0,
                 pitch_range=(PITCH_MIN, PITCH_MAX),
                 dist_range=(DIST_MIN, DIST_MAX)):
        super().__init__(target=tuple(target), yaw=yaw, pitch=pitch, dist=dist,
                         pitch_range=pitch_range, dist_range=dist_range)
        # 旧代码直接读写 .target 并与 Vec3 做运算，保持 Vec3 类型
        self.target = Vec3(*tuple(self.target))

    def set_target(self, t):
        super().set_target(t)
        self.target = Vec3(*tuple(self.target))
        return self

    def view_matrix(self):
        return mat4_to_Mat4(super().view_matrix())

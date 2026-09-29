# -*- coding: utf-8 -*-
"""轨道相机（纯数学，无 GL）。

包含手写 look-at 视图矩阵：仅依赖 Vec3 的 x/y/z 属性，
规避部分 pyglet 2.x 过渡版 Mat4.look_at 的 cross_product/cross 改名缺陷。
矩阵构造参数为列主序，布局与官方实现一致。
"""
import math
from pyglet.math import Mat4, Vec3

PITCH_MIN, PITCH_MAX = 5.0, 89.0
DIST_MIN, DIST_MAX = 4.0, 60.0


def look_at_matrix(eye, target, up):
    """手写 look-at 视图矩阵（纯标量运算，跨 pyglet 2.x 版本稳定）。"""
    # f = normalize(target - eye)（视线方向）
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


class OrbitCamera:
    """轨道相机状态机：只维护参数，产出视图矩阵；与窗口/输入完全解耦。"""

    def __init__(self, target=Vec3(0, 1, 0),
                 yaw=45.0, pitch=30.0, dist=18.0):
        self.target = target
        self.yaw = yaw
        self.pitch = pitch
        self.dist = dist

    # ---- 状态变更（由输入层或时间轴调用） ----
    def orbit(self, dx, dy):
        """鼠标拖动：偏航 + 俯仰（俯仰钳制）。"""
        self.yaw += dx * 0.4
        self.pitch = max(PITCH_MIN, min(PITCH_MAX, self.pitch + dy * 0.4))

    def zoom(self, sy):
        """滚轮缩放（距离钳制）。"""
        self.dist = max(DIST_MIN, min(DIST_MAX, self.dist - sy * 2))

    def advance(self, dt):
        """默认编排：相机以 8°/s 自动环绕（M2 将由时间轴接管）。"""
        self.yaw += dt * 8.0

    # ---- 纯查询 ----
    @property
    def eye(self):
        yr, pr = math.radians(self.yaw), math.radians(self.pitch)
        return Vec3(
            self.target.x + self.dist * math.cos(pr) * math.cos(yr),
            self.target.y + self.dist * math.sin(pr),
            self.target.z + self.dist * math.cos(pr) * math.sin(yr))

    def view_matrix(self):
        return look_at_matrix(self.eye, self.target, Vec3(0, 1, 0))

# -*- coding: utf-8 -*-
"""轨道相机（纯数学，无 GL）。

包含手写 look-at 视图矩阵：仅依赖 Vec3 的 x/y/z 属性，
规避部分 pyglet 2.x 过渡版 Mat4.look_at 的 cross_product/cross 改名缺陷。
矩阵构造参数为列主序，布局与官方实现一致。
"""
import math
from pyglet.math import Mat4, Vec3

# 默认钳制范围。这组默认值是**给"站在地面上看场景"选的**：
# 俯仰下限 5° 保证相机不入地、上限 89° 避开 up 向量与视线共线的退化；
# 距离 4~60 避免贴脸与把场景缩成一个点。
# 但空间场景（太阳系、太空视角的 SDF）需要更大的自由度：要贴近黄道面
# （甚至略低于目标平面）才看得到行星相位，也要能拉远到装下整个星系。
# 因此范围做成**实例参数**、默认值不变——既有调用方零影响，
# 需要放开的 demo 自己传 pitch_range/dist_range（见 demos/solar_system.py）。
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
    """轨道相机状态机：只维护参数，产出视图矩阵；与窗口/输入完全解耦。

    Args:
        pitch_range: (min, max) 俯仰钳制（度），默认地面视角的 (5, 89)
        dist_range:  (min, max) 距离钳制，默认 (4, 60)
    """

    def __init__(self, target=Vec3(0, 1, 0),
                 yaw=45.0, pitch=30.0, dist=18.0,
                 pitch_range=(PITCH_MIN, PITCH_MAX),
                 dist_range=(DIST_MIN, DIST_MAX)):
        self.target = target
        self.yaw = yaw
        self.pitch = pitch
        self.dist = dist
        self.pitch_range = (float(pitch_range[0]), float(pitch_range[1]))
        self.dist_range = (float(dist_range[0]), float(dist_range[1]))
        assert self.pitch_range[0] < self.pitch_range[1], 'pitch_range 需递增'
        assert self.dist_range[0] < self.dist_range[1], 'dist_range 需递增'
        assert abs(self.pitch_range[1]) < 90.0, \
            'pitch 上限必须 < 90°：等于 90° 时视线与 up 共线，look-at 退化'

    # ---- 状态变更（由输入层或时间轴调用） ----
    def orbit(self, dx, dy):
        """鼠标拖动：偏航 + 俯仰（俯仰按本实例的 pitch_range 钳制）。"""
        lo, hi = self.pitch_range
        self.yaw += dx * 0.4
        self.pitch = max(lo, min(hi, self.pitch + dy * 0.4))

    def zoom(self, sy):
        """滚轮缩放（按本实例的 dist_range 钳制）。"""
        lo, hi = self.dist_range
        self.dist = max(lo, min(hi, self.dist - sy * 2))

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

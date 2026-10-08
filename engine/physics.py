# -*- coding: utf-8 -*-
"""极简物理：AABB 碰撞体 + 重力 + 分轴解算（move-and-slide）。

不是刚体引擎（没有旋转、没有冲量求解），只解决"角色/物体在世界里
走不穿墙、能站地上"这一档需求：

- 碰撞体统一是 **AABB**，静态（墙/地/台）与动态（角色/箱子）分开存；
- 动态体每帧积分速度，然后 **X -> Y -> Z 分轴移动 + 分别解算**：
  分轴的好处是天然产生"沿墙滑动"——撞墙时只清掉那个轴的速度分量，
  另外两轴照走，不会像一次性解算那样直接卡死；
- 站地判定：Y 轴解算时若从上方撞到静态体，则 ``on_ground = True``
  并把竖直速度清零（否则会被重力一直往下压、下一帧又反弹）。

Broadphase 用 :class:`engine.frustum.SpatialHash`：静态几何上千块时，
逐块测 AABB 的常数压不下去，先按格子取候选。
"""
from engine.frustum import AABB, SpatialHash

__all__ = ['Body', 'StaticCollider', 'PhysicsWorld', 'aabb_overlap',
           'resolve_axis']


def aabb_overlap(a, b):
    return a.intersects(b)


def resolve_axis(box, colliders, axis, velocity, eps=1e-6):
    """沿单轴解算：返回 (是否碰撞, 碰撞法向)。

    关键前提：**另外两个轴必须"严格重叠"才解算**（留 eps 余量，接触不算）。
    否则站在地面上时 y 恰好与地面相接，会被误判成"与地面在 x/z 也重叠"，
    一开始走动就被地面当成墙沿水平方向弹出去（实测直接飞到场地外）。
    """
    hit = False
    normal = 0.0
    others = [i for i in range(3) if i != axis]
    for c in colliders:
        if not all(box.min[a] < c.max[a] - eps and box.max[a] > c.min[a] + eps
                   for a in others):
            continue
        if not (box.min[axis] <= c.max[axis] and box.max[axis] >= c.min[axis]):
            continue
        hit = True
        if velocity > 0.0:
            # 向正方向走 -> 推到碰撞体左侧（min 侧）
            shift = c.min[axis] - box.max[axis]
            normal = -1.0
        elif velocity < 0.0:
            shift = c.max[axis] - box.min[axis]
            normal = 1.0
        else:
            continue
        box.min = tuple(v + (shift if i == axis else 0.0)
                        for i, v in enumerate(box.min))
        box.max = tuple(v + (shift if i == axis else 0.0)
                        for i, v in enumerate(box.max))
    return hit, normal


class StaticCollider:
    """静态碰撞体（墙、地面、平台）。"""

    __slots__ = ('aabb', 'name', 'friction')

    def __init__(self, aabb, name='static', friction=1.0):
        self.aabb = aabb
        self.name = name
        self.friction = friction

    def __repr__(self):
        return f'StaticCollider({self.name}, {self.aabb})'


class Body:
    """动态刚体（AABB，无旋转）。"""

    def __init__(self, position=(0.0, 2.0, 0.0), half=(0.35, 0.9, 0.35),
                 mass=70.0, gravity=-22.0, name='body'):
        self.half = tuple(half)
        self.position = tuple(position)
        self.velocity = (0.0, 0.0, 0.0)
        self.mass = float(mass)
        self.gravity = float(gravity)
        self.name = name
        self.on_ground = False
        self.max_speed = 30.0

    @property
    def aabb(self):
        return AABB.from_center_half(self.position, self.half)

    def apply_impulse(self, v):
        self.velocity = tuple(self.velocity[i] + v[i] for i in range(3))
        return self

    def clamp_speed(self):
        vx, vy, vz = self.velocity
        s = (vx * vx + vy * vy + vz * vz) ** 0.5
        if s > self.max_speed:
            k = self.max_speed / s
            self.velocity = (vx * k, vy * k, vz * k)
        return self

    def __repr__(self):
        return f'Body({self.name}, pos={self.position}, on_ground={self.on_ground})'


class PhysicsWorld:
    """物理世界：静态碰撞体集合 + 动态体积分与解算。"""

    def __init__(self, gravity=-22.0, cell=4.0, use_hash=True):
        self.gravity = float(gravity)
        self.statics = []
        self.bodies = []
        self.use_hash = use_hash
        self._hash = SpatialHash(cell)
        self._hash_dirty = True
        self.contacts = 0

    # ---- 构建 ----
    def add_static(self, aabb, name='static', friction=1.0):
        self.statics.append(StaticCollider(aabb, name, friction))
        self._hash_dirty = True
        return self

    def add_body(self, body):
        self.bodies.append(body)
        return body

    def rebuild_hash(self):
        self._hash = SpatialHash(self._hash.cell)
        for i, s in enumerate(self.statics):
            self._hash.insert(i, s.aabb)
        self._hash_dirty = False
        return self._hash

    def _candidates(self, box):
        if not self.use_hash:
            return [s.aabb for s in self.statics]
        if self._hash_dirty:
            self.rebuild_hash()
        idxs = self._hash.candidate_indices(box)
        return [self.statics[i].aabb for i in idxs]

    # ---- 步进 ----
    def step(self, dt, substeps=1):
        """推进一帧（substeps>1 时把 dt 切小，防止高速穿薄墙）。"""
        h = dt / max(1, substeps)
        for _ in range(substeps):
            for b in self.bodies:
                self._step_body(b, h)
        return self

    def _step_body(self, body, dt):
        vx, vy, vz = body.velocity
        vy += body.gravity * dt
        body.velocity = (vx, vy, vz)
        body.clamp_speed()

        box = body.aabb
        # 候选查询用的包围盒要外扩一点（覆盖本帧位移 + 皮肤厚度），
        # 但**不能原地扩**——expand 是原地修改，第一版在这里把碰撞盒整体
        # 撑大 0.5，物体会悬空半格停住。
        candidates = self._candidates(box.copy().expand(0.5))
        body.on_ground = False
        self.contacts = 0
        # 分轴：X -> Y -> Z，撞到就清掉该轴速度（沿墙滑动的来源）
        for axis, vel in enumerate(body.velocity):
            delta = vel * dt
            if abs(delta) < 1e-9:
                continue
            box.min = tuple(v + (delta if i == axis else 0.0)
                            for i, v in enumerate(box.min))
            box.max = tuple(v + (delta if i == axis else 0.0)
                            for i, v in enumerate(box.max))
            hit, normal = resolve_axis(box, candidates, axis, vel)
            if hit:
                v = list(body.velocity)
                v[axis] = 0.0
                body.velocity = tuple(v)
                self.contacts += 1
                if axis == 1 and normal > 0.0:      # 从上方落到静态体上
                    body.on_ground = True
        body.position = box.center
        return body

    # ---- 查询 ----
    def raycast(self, origin, direction, max_t=1e9):
        """射线与静态几何求交（复用 picking 的 slab 法）。

        返回 (t, StaticCollider) 或 None。用于落地探测、视线遮挡。
        """
        from engine.picking import ray_aabb
        best, hit = max_t, None
        for s in self.statics:
            t = ray_aabb(origin, direction, s.aabb, max_t)
            if t is not None and t < best:
                best, hit = t, s
        return (best, hit) if hit is not None else None

    def ground_height(self, x, z, from_y=100.0):
        """在 (x,z) 处向下探测地面高度（角色贴地/相机净空检查用）。"""
        res = self.raycast((x, from_y, z), (0.0, -1.0, 0.0))
        return (from_y - res[0]) if res else None

    def __repr__(self):
        return f'PhysicsWorld(statics={len(self.statics)}, bodies={len(self.bodies)})'

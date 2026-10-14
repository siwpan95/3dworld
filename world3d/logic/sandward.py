# -*- coding: utf-8 -*-
"""沙垒 / Sandward —— UV 投影规则演示场（纯数学，零 GL）。

dust 风格**块状竞技场**（致敬结构，非复制地图）+ 5 检查点巡游 + 碰撞。
两种消费者：

* ``presentation/sandward_scene.py`` —— 用 ``build_geometry()`` 的平铺数组建 VBO；
* ``tests/test_sandward.py`` —— 巡游/碰撞/UV 密度（无窗口）。

场地拓扑（俯视，X 右 / Z 下，原点在中庭中心）::

    ┌──────────────────────────────┐   z=-36 北缘
    │  NW 角场          NE 角场     │
    │      ┌──────────┐            │   中庭：x∈±11, z∈±13（四向门）
    │      │  中庭 C1  │            │
    │      └──────────┘            │
    │  SW 角场          SE 角场     │
    └──────────────────────────────┘   z=+36 南缘（出生点 (0,32)）

材质即"贴图规则"样本：五种材质、五档贴图密度（world units per UV）——
沙岩 160 / 石膏 144 / 木箱 96 / 金属 128 / 砂石地 176，与 §18 实证的
CS2 "多档密度共存" 呼应。
"""
import math

from world3d.engine.texproj import UvProjection, dominant_axis, project_uv

#: 材质表：图集格名 + 贴图密度（一张贴图覆盖的世界单位）
MATERIALS = {
    'sandstone': dict(atlas='sandstone', tile=160.0),
    'plaster': dict(atlas='plaster', tile=144.0),
    'crate': dict(atlas='crate', tile=96.0),
    'metal': dict(atlas='metal', tile=128.0),
    'sand': dict(atlas='sand', tile=176.0),
}

#: 图集格顺序（渲染器与测试共用）
ATLAS_NAMES = ('sandstone', 'plaster', 'crate', 'metal', 'sand')

WALL_H = 6.0          # 墙高
BOUND_X = 50.0        # 场地半宽（x）
BOUND_Z = 36.0        # 场地半深（z）
ROOM_X = 11.0         # 中庭半宽（墙中心线 ±10，厚 2）
ROOM_Z = 13.0         # 中庭半深
DOOR_GAP = 3.0        # 门洞半宽（x∈[-3,3]）
PLAYER_R = 0.45       # 玩家碰撞半径
EYE_H = 1.6

SPAWN = (0.0, 32.0)

#: 检查点（顺序即巡游顺序）：标签 + 坐标
CHECKPOINTS = (
    ('中庭', 0.0, 0.0),
    ('西北角场', -34.0, 27.0),
    ('东北角场', 34.0, 27.0),
    ('东南角场', 34.0, -27.0),
    ('西南角场', -34.0, -27.0),
)
CHECK_RADIUS = 2.6

#: 巡游路线（含门洞/转角的安全航点；直线段全部无障碍——由测试保证）
ROUTE = (
    (0.0, 32.0),      # 出生
    (0.0, 0.0),       # C1 中庭（穿南门）
    (-14.0, 0.0),     # 穿西门
    (-34.0, 16.0),
    (-34.0, 27.0),    # C2 西北
    (34.0, 27.0),     # C3 东北（沿北环）
    (34.0, -27.0),    # C4 东南（沿东环）
    (-34.0, -27.0),   # C5 西南（沿南环）
    (-34.0, 30.0),    # 沿西环回北
    (0.0, 32.0),      # 回出生点
)

AUTO_SPEED = 6.2      # 自动巡游速度（m/s）
WALK_SPEED = 4.4
SPRINT_SPEED = 6.8


# ------------------------------------------------------------------ 场地 --

def _box(x0, z0, x1, z1, y0, y1, mat):
    return (float(min(x0, x1)), float(min(z0, z1)),
            float(max(x0, x1)), float(max(z0, z1)), float(y0), float(y1), mat)


def _crate_cluster(cx, cz):
    """三箱堆：两只并排 + 一只叠上（边长 0.9）。"""
    return [
        _box(cx - 0.45, cz - 0.45, cx + 0.45, cz + 0.45, 0.0, 0.9, 'crate'),
        _box(cx + 0.50, cz - 0.45, cx + 1.40, cz + 0.45, 0.0, 0.9, 'crate'),
        _box(cx - 0.45, cz - 0.45, cx + 0.45, cz + 0.45, 0.9, 1.8, 'crate'),
    ]


def build_arena():
    """块状竞技场 brush 表：``(x0, z0, x1, z1, y0, y1, material)``。"""
    bs = []
    # 地面（砂石地）
    bs.append(_box(-BOUND_X - 2, -BOUND_Z - 2, BOUND_X + 2, BOUND_Z + 2,
                   -2.0, 0.0, 'sand'))
    # 外缘墙（沙岩）
    bs.append(_box(-BOUND_X - 2, BOUND_Z, BOUND_X + 2, BOUND_Z + 2, 0, WALL_H, 'sandstone'))
    bs.append(_box(-BOUND_X - 2, -BOUND_Z - 2, BOUND_X + 2, -BOUND_Z, 0, WALL_H, 'sandstone'))
    bs.append(_box(-BOUND_X - 2, -BOUND_Z - 2, -BOUND_X, BOUND_Z + 2, 0, WALL_H, 'sandstone'))
    bs.append(_box(BOUND_X, -BOUND_Z - 2, BOUND_X + 2, BOUND_Z + 2, 0, WALL_H, 'sandstone'))
    # 中庭墙（石膏；四向门洞 ±3）
    g = DOOR_GAP
    bs.append(_box(-ROOM_X - 1, -ROOM_Z, -ROOM_X + 1, -g, 0, WALL_H, 'plaster'))
    bs.append(_box(-ROOM_X - 1, g, -ROOM_X + 1, ROOM_Z, 0, WALL_H, 'plaster'))
    bs.append(_box(ROOM_X - 1, -ROOM_Z, ROOM_X + 1, -g, 0, WALL_H, 'plaster'))
    bs.append(_box(ROOM_X - 1, g, ROOM_X + 1, ROOM_Z, 0, WALL_H, 'plaster'))
    bs.append(_box(-ROOM_X - 1, ROOM_Z - 1, -g, ROOM_Z + 1, 0, WALL_H, 'plaster'))
    bs.append(_box(g, ROOM_Z - 1, ROOM_X + 1, ROOM_Z + 1, 0, WALL_H, 'plaster'))
    bs.append(_box(-ROOM_X - 1, -ROOM_Z - 1, -g, -ROOM_Z + 1, 0, WALL_H, 'plaster'))
    bs.append(_box(g, -ROOM_Z - 1, ROOM_X + 1, -ROOM_Z + 1, 0, WALL_H, 'plaster'))
    # 中庭内：金属护栏柱（装饰，不挡 C1 直线）
    bs.append(_box(-9.5, -9.5, -8.5, -8.5, 0, 1.2, 'metal'))
    bs.append(_box(8.5, 8.5, 9.5, 9.5, 0, 1.2, 'metal'))
    # 四角场木箱堆（避让环线 |x|=34 / |z|=27）
    for (cx, cz) in ((-24, 20), (-18, 25), (24, 20), (18, 25),
                     (24, -20), (18, -25), (-24, -20), (-18, -25)):
        bs.extend(_crate_cluster(cx, cz))
    # 中庭内两堆（避开 x=0 / z=0 直线）
    bs.extend(_crate_cluster(7.0, 8.0))
    bs.extend(_crate_cluster(-8.0, -8.0))
    return bs


# ------------------------------------------------------------------ 碰撞 --

def make_boxes(brushes=None):
    """碰撞盒（与 brush 同构；供快速遍历）。"""
    return list(brushes if brushes is not None else build_arena())


def blocked(x, z, boxes, radius=PLAYER_R, y0=0.0, y1=1.7):
    """玩家（圆柱）位置是否与任何 brush 相交（只算与玩家高度带相交的盒）。"""
    for (bx0, bz0, bx1, bz1, by0, by1, _m) in boxes:
        if by1 <= y0 + 0.05 or by0 >= y1:
            continue
        if x + radius > bx0 and x - radius < bx1 \
                and z + radius > bz0 and z - radius < bz1:
            return True
    return False


def move_with_slide(x, z, dx, dz, boxes, radius=PLAYER_R):
    """分轴滑动的移动求解（撞墙沿墙滑，不卡缝）。"""
    nx = x + dx
    if not blocked(nx, z, boxes, radius):
        x = nx
    nz = z + dz
    if not blocked(x, nz, boxes, radius):
        z = nz
    return x, z


def segment_clear(a, b, boxes, radius=PLAYER_R, step=0.25):
    """直线段是否全程可通行（测试/路线校验用）。"""
    (x0, z0), (x1, z1) = a, b
    d = math.hypot(x1 - x0, z1 - z0)
    n = max(1, int(d / step))
    for i in range(n + 1):
        t = i / n
        if blocked(x0 + (x1 - x0) * t, z0 + (z1 - z0) * t, boxes, radius):
            return False
    return True


# ------------------------------------------------------------------ 巡游 --

class TourState:
    """检查点巡游状态机：按序触达 5 点 → done + 全程计时。"""

    def __init__(self, checkpoints=CHECKPOINTS, radius=CHECK_RADIUS):
        self.checkpoints = tuple(checkpoints)
        self.radius = radius
        self.visited = [False] * len(self.checkpoints)
        self.time = 0.0
        self.done = False

    @property
    def count(self):
        return sum(self.visited)

    def update(self, dt, x, z):
        if self.done:
            return
        self.time += dt
        for i, (_label, cx, cz) in enumerate(self.checkpoints):
            if not self.visited[i] and math.hypot(x - cx, z - cz) < self.radius:
                self.visited[i] = True
        if all(self.visited):
            self.done = True


class SandwardKernel:
    """演示场内核：场地 + 玩家 + 自动巡游（headless 可跑）。"""

    def __init__(self, seed=20261015):
        self.seed = seed
        self.brushes = build_arena()
        self.boxes = make_boxes(self.brushes)
        self.player = {'x': SPAWN[0], 'z': SPAWN[1]}
        self.tour = TourState()
        self.wp = 0

    def restart(self):
        self.player = {'x': SPAWN[0], 'z': SPAWN[1]}
        self.tour = TourState()
        self.wp = 0

    # ---- 移动 ----
    def move(self, dx, dz, dt, speed=WALK_SPEED):
        """方向向量 (dx, dz)（不要求归一）× 速度 × dt，分轴滑动。"""
        n = math.hypot(dx, dz)
        if n < 1e-9:
            return
        dx, dz = dx / n * speed * dt, dz / n * speed * dt
        self.player['x'], self.player['z'] = move_with_slide(
            self.player['x'], self.player['z'], dx, dz, self.boxes)

    def auto_step(self, dt):
        """朝下一航点走（无人值守/录制）；到达即推进，末点后停在原地。"""
        wx, wz = ROUTE[self.wp]
        x, z = self.player['x'], self.player['z']
        d = math.hypot(wx - x, wz - z)
        if d < 0.4:
            self.wp = min(self.wp + 1, len(ROUTE) - 1)
            return
        self.move(wx - x, wz - z, dt, speed=AUTO_SPEED)

    def update(self, dt):
        self.tour.update(dt, self.player['x'], self.player['z'])

    # ---- 状态 ----
    @property
    def progress(self):
        return self.tour.count

    def status(self):
        t = self.tour
        return (f'检查点 {t.count}/{len(t.checkpoints)}'
                f'{"  完成!" if t.done else ""}   用时 {t.time:5.1f}s')


# ------------------------------------------------------------ 几何生成 --

#: 盒体六面：法线 + 顶点序号（序号对应下面 v[] 的 8 角，逆时针=外法线朝外）
_CORNERS = ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1),
            (0, 1, 0), (1, 1, 0), (1, 1, 1), (0, 1, 1))
_FACES = (
    ((0, 0, 1), (3, 2, 6, 7)),    # +z
    ((0, 0, -1), (1, 0, 4, 5)),   # -z
    ((1, 0, 0), (2, 1, 5, 6)),    # +x
    ((-1, 0, 0), (0, 3, 7, 4)),   # -x
    ((0, 1, 0), (7, 6, 5, 4)),    # +y
    ((0, -1, 0), (0, 1, 2, 3)),   # -y
)
_LIGHT = (0.5, 0.8, 0.3)


def _face_shade(normal):
    l = math.sqrt(sum(c * c for c in normal))
    ll = math.sqrt(sum(c * c for c in _LIGHT))
    return sum(a * b for a, b in zip(normal, _LIGHT)) / (l * ll)


def build_geometry(brushes=None):
    """把 brush 表展平成渲染数组（纯数据，无 GL）。

    返回 ``dict(positions, colors, uvs, cells, extras, indices, counts)``：

    * ``uv``  = **世界投影 UV（贴图数）**——由 ``texproj.UvProjection`` 按面
      主导轴生成（这就是要被演示/目检的"贴图规则"本体）；
    * ``cell`` = 图集矩形 (u0,v0,u1,v1)（图集格内平铺在片元里做）；
    * ``extra`` = 该材质的贴图密度（世界单位/UV，与 CS2 ``m_flUvDensity`` 同语义）。
    """
    from world3d.engine.texproj import Atlas
    atlas = Atlas(ATLAS_NAMES, cols=3, rows=2)
    brushes = brushes if brushes is not None else build_arena()
    pos, col, uv, cell, extra, idx = [], [], [], [], [], []
    n = 0
    for (x0, z0, x1, z1, y0, y1, matname) in brushes:
        m = MATERIALS[matname]
        tile = m['tile']
        rect = atlas.rect(m['atlas'])
        v = []
        for (cx, cy, cz) in _CORNERS:
            v.append((x0 + (x1 - x0) * cx, y0 + (y1 - y0) * cy,
                      z0 + (z1 - z0) * cz))
        for (fn, order) in _FACES:
            axis = dominant_axis(fn)
            proj = UvProjection.from_tile(tile, axis=axis)
            k = 0.4 + 0.6 * abs(_face_shade(fn))
            for vi in order:
                x, y, z = v[vi]
                pos.extend((x, y, z))
                col.extend((k, k, k, 1.0))
                u, w = project_uv((x, y, z), proj)
                uv.extend((u, w))
                cell.extend(rect)
                extra.append(tile)
            idx.extend((n, n + 1, n + 2, n, n + 2, n + 3))
            n += 4
    return dict(positions=pos, colors=col, uvs=uv, cells=cell, extras=extra,
                indices=idx, vertex_count=n, counts=len(brushes))

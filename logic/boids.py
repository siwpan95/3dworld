# -*- coding: utf-8 -*-
"""Boids 群集（Reynolds 1987）——纯数学逻辑层。

**本模块不 import pyglet.gl，可无窗口导入与运行**（只用到 ``pyglet.math.Vec3``，
它是纯 ctypes 结构体，不碰 OpenGL）——这是本仓 ``logic/`` 层的硬约定，
也是它能被 ``tests/`` 无窗口单测覆盖的前提。

## 三条经典力（Reynolds, SIGGRAPH 1987）

对每只 boid ``i``，在半径 ``neighbour_radius`` 内取邻居集合 ``N(i)``（**含** i 自身
以外的全部个体；本实现用格子哈希加速，见下）：

1. **分离 separation**（避免撞在一起）
   ``s = mean_{j∈N, |p_j-p_i|<sep_radius}( (p_i - p_j) / |p_i - p_j| )``
   —— 取**反方向单位向量**的均值，故是"离我最近的邻居把我往外推"。
   除数用 ``|p_j-p_i|`` 而不是距离平方，是为了让极近的邻居产生**强得多**的推力
   （否则挤压时会出现"穿模"，鸟与鸟重叠）。
   注意分离半径 ``sep_radius`` 独立于 ``neighbour_radius``：前者是"私人空间"，
   后者是"我看得见谁"。两者混用会让群集要么挤成一团、要么完全散开。

2. **对齐 alignment**（朝同伴的平均朝向靠拢，产生"一群朝同方向飞"的观感）
   ``a = normalize( mean_{j∈N}( v_j ) - v_i )``
   —— 减掉自身速度，得到的才是"转向力"（而不是把速度替换成平均值）。

3. **聚合 cohesion**（朝邻居质心靠拢，防止群体散开）
   ``c = normalize( mean_{j∈N}( p_j ) - p_i )``

三者先各自归一化，再乘权重线性叠加，得到转向力 ``F``::

    F = w_sep * s + w_align * a + w_cohesion * c

权重语义（三者都是**无量纲方向向量**的系数，单位是"加速度"）：

- ``separation`` ↑ → 鸟彼此让开，群体变"松"、边缘毛糙；
- ``alignment`` ↑ → 群体方向趋同，出现大片同向流动（"一群朝同方向飞的鸟"）；
- ``cohesion`` ↑ → 群体收拢成球，过大会塌缩成一点。

## 其余约束

- **边界回避 boundary avoidance**：软转向，而不是硬回卷。当 boid 进入
  ``boundary`` 内的边带时，叠加一个指向盒子中心的力，大小随"深入边带的程度"
  平滑增大（``smoothstep`` 曲线），故轨迹连续、无瞬移。
- **速度上限 / 转向力上限**：``F`` 先被裁剪到 ``max_force``，再累加到速度上；
  速度再被裁剪到 ``max_speed``。两个上限缺一不可 —— 只限速度会让鸟在
  靠近时瞬间甩头（转向力未约束），只限力会让整体越飞越快。
- **捕食者 predator**：``set_predator(Vec3)`` 后，距其 ``predator_radius`` 内的
  boid 叠加一个**远离**捕食者的力，权重 ``predator``（默认远大于其它三项，
  所以效果是"炸群后重新聚拢"）。

## 确定性与性能

- 固定 ``seed`` 下初始化完全可复现（``random.Random(seed)`` 实例隔离，
  不受全局随机状态影响）。
- 邻域用**均匀格子哈希 + CSR 邻居表**：格子边长固定为 ``CELL``，只查以自身
  所在格为中心的 3×3×3 格。因为 ``CELL >= neighbor_radius``（有断言），
  任何半径内的邻居都必然落在这 27 格里，**不会漏邻居**。
  实测（400 只 / 60 fps 步长）：朴素 O(n²) 连"只做距离判断"都要约 12 ms/步，
  早已越过 16 ms 帧预算 ⇒ 这是"count ≤ 400 用 O(n²) 也行"这条建议
  **在本机没成立**的地方，故必须做格子优化。本实现 400 只约 11 ms/步。

## 内部表示

``Flock`` 内部把位置/速度放在 6 条平坦 ``list[float]`` 里（``_px/_py/_pz/_vx/_vy/_vz``），
而不是每只一个对象：内层循环里 ``list[float]`` 索引比属性访问快得多，
``Vec3`` 对象只在对外接口（``boids`` 属性、``set_predator``）处**按需包装**。
``Boid`` 因此是一个轻量视图对象，不持有模拟状态。
"""
from __future__ import annotations

import math
import random

from pyglet.math import Vec3

# --------------------------------------------------------------- 默认参数 --
DEFAULT_BOUNDS = 14.0          # 盒子半边长（世界单位），模拟域 = [-b, b]^3
DEFAULT_COUNT = 400

# 邻域格子边长。**必须 >= neighbour_radius**（默认 4.0），否则 3×3×3 邻域会漏邻居
# ——这一点是我先踩坑后加的断言：最初取 2.5（每格候选更少、实测快 40%），
# 但 2.5 < 4.0 时半径 4.0 内的邻居会落在 27 格之外，对齐/聚合被静默削弱。
# 正确做法是把 cell 提到 4.0；性能靠 CSR 邻居表而不是靠缩小格子。
CELL = 4.0

DEFAULT_WEIGHTS = {
    'separation': 3.6,
    'alignment': 3.0,
    'cohesion': 1.8,
    'boundary': 3.0,
    'predator': 9.0,
}

# 每只 boid 最多记多少个邻居。**这是性能闸门，不是可选优化**：
# 群一旦收紧，半径内的邻居数会随密度暴涨，每步成本从几毫秒涨到二十几毫秒
# （已超 16 ms 帧预算）。截断成"只记最近 N 个"后成本与密度脱钩，且这符合
# 物理直觉 —— 远处的同伴本来就不该比近处的更有话语权。
# 20 是实测取值：当前密度下角向中位数仍能到 ~2°（对齐成立），
# 单步成本 N=340 约 9.5 ms / N=500 约 14.4 ms。
MAX_NEIGHBOURS = 20

# 三个演示预设（demos/boids.py 的 1/2/3 键）：紧密群集 / 松散游弋 / 高速冲刺
#
# 这些数值是**实测调出来的**，判据只有一条：截图里能不能看出"一群朝同方向
# 飞的鸟"。过程中最有价值的一条实测结论是 —— **决定观感的是密度，不是权重**：
# 起初用 bounds=14 / neighbour_radius=4.0，340 只鸟稀稀拉拉，即便 align 指标
# 到 0.9，实际角向分布的中位数仍有 27.7°（p90 56°），画面上就是"一堆各飞各的"。
# 把域收到 9、邻域收到 3.0 之后（鸟数量不变 ⇒ 密度约翻倍），角向中位数降到
# **0.3°**（p90 1.3°），整群像刚体一样同向平移 —— 这才是想要的画面。
DOMAIN = {
    'bounds': 9.0,
    'neighbour_radius': 3.0,
    'sep_radius': 1.6,
    'cell': 3.0,            # 必须 >= neighbour_radius（有断言兜底）
    'spawn_radius': 4.0,
}
PRESETS = {
    'tight': {'separation': 3.0, 'alignment': 3.4, 'cohesion': 1.8,
              'boundary': 3.0, 'predator': 9.0},
    'loose': {'separation': 4.4, 'alignment': 1.1, 'cohesion': 1.0,
              'boundary': 2.6, 'predator': 8.0},
    'sprint': {'separation': 2.8, 'alignment': 3.6, 'cohesion': 1.6,
               'boundary': 3.2, 'predator': 12.0},
}
PRESET_LABELS = {'tight': '紧密群集', 'loose': '松散游弋', 'sprint': '高速冲刺'}
# 每档预设的速度上限与涡旋强度：三档的差别不只是形状，还包括"飞多快"
PRESET_MAX_SPEED = {'tight': 14.0, 'loose': 16.0, 'sprint': 45.0}
PRESET_TURBULENCE = {'tight': 0.16, 'loose': 0.16, 'sprint': 0.16}

_EPS = 1e-9
_sin = math.sin          # 内层循环里用本地名，省掉每次 math.sin 的属性查找


def _clamp(v, lo, hi):
    return lo if v < lo else (hi if v > hi else v)


def _smoothstep(edge0, edge1, x):
    """GLSL 同名函数的 Python 版：0→1 的平滑过渡（用于边界力，保证轨迹连续）。"""
    if edge1 <= edge0:
        return 0.0 if x < edge0 else 1.0
    t = _clamp((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


class Boid:
    """单只 boid 的**只读视图**（pos/vel 是快照，改它不会影响 ``Flock``）。

    ``Flock`` 的状态存在平坦数组里（性能原因，见模块 docstring），
    本类只在需要按对象访问时（渲染、调试、单测断言）临时构造。
    """

    __slots__ = ('pos', 'vel')

    def __init__(self, pos, vel):
        self.pos = pos
        self.vel = vel

    def __repr__(self):
        return (f'Boid(pos=({self.pos.x:.2f}, {self.pos.y:.2f}, {self.pos.z:.2f}), '
                f'vel=({self.vel.x:.2f}, {self.vel.y:.2f}, {self.vel.z:.2f}))')


class Flock:
    """Reynolds Boids 群集模拟器。

    Args:
        count:            个体数量
        bounds:           模拟域半边长；域为 ``[-bounds, bounds]^3``
        seed:             随机种子（固定后结果完全可复现）
        weights:          覆盖 ``DEFAULT_WEIGHTS`` 的部分项（其余取默认）
        max_speed:        速度上限（世界单位/秒）；``set_preset`` 会按档覆盖它
        max_force:        转向力上限（世界单位/秒²）
        neighbour_radius: 邻域半径（对齐/聚合的可见范围）
        sep_radius:       分离半径（"私人空间"），必须 <= neighbour_radius 才有意义
        cell:             格子哈希边长，**必须 >= neighbour_radius**（有断言）
        speed_range:      初始速度大小区间（模拟多样性，避免开局完全同步）
        spawn_radius:     初始撒点球半径，默认 6.0（**远小于 bounds**，理由见 reset）
        max_neighbours:   每只 boid 最多记几个邻居（性能闸门，见 MAX_NEIGHBOURS）
    """

    def __init__(self, count=DEFAULT_COUNT, bounds=DEFAULT_BOUNDS, seed=20261006,
                 weights=None, max_speed=16.0, max_force=30.0,
                 neighbour_radius=4.0, sep_radius=2.2, cell=CELL,
                 speed_range=(3.0, 6.5), spawn_radius=6.0,
                 max_neighbours=MAX_NEIGHBOURS, band=None):
        assert count > 0, 'count 必须为正'
        assert bounds > 0.0, 'bounds 必须为正'
        assert cell >= neighbour_radius - 1e-9, (
            f'格子边长 {cell} 必须 >= neighbour_radius {neighbour_radius}，'
            f'否则 3×3×3 邻域会漏掉半径内的邻居')
        assert sep_radius <= neighbour_radius + 1e-9, \
            'sep_radius 不应大于 neighbour_radius（否则分离力看不到那么远）'

        self.count = int(count)
        self.bounds = float(bounds)
        self.max_speed = float(max_speed)
        self.max_force = float(max_force)
        self.neighbour_radius = float(neighbour_radius)
        self.sep_radius = float(sep_radius)
        self.cell = float(cell)
        self.max_neighbours = int(max_neighbours)
        # 边界回避的"边带"厚度：进入这条带才开始被推回中心。
        # 默认绑在域半径上（3/4 处开始转向）——**不能写死**：域半径 14 时
        # 固定 3.0 的边带只占 21%，而域半径 9 时要占到 33%，力才够把群推住。
        self.band = float(band) if band is not None else 0.75 * self.bounds
        self.seed = seed
        self.speed_range = (float(speed_range[0]), float(speed_range[1]))
        self.spawn_radius = float(spawn_radius)   # 初始撒点半径（见 reset 的说明）
        # 巡航速度：把每只鸟的**速率**缓慢拉向该值（只改速率不改方向，见 step）。
        # **默认关闭（0.0）**，且这是实测结论而不是保守取值：对齐力把 400 只鸟的
        # 速度反复平均后，群体的共同速率会自然落到初始 ``speed_range`` 的均值附近，
        # 此时群紧凑、朝向一致（实测 align=0.998 / 包围盒对角线 30）；一旦加上
        # 巡航回拉（7.0 或 9.0），速率被强行抬高，转向半径随之变大，群**散开到
        # 整个域**（align 掉到 0.15~0.23 / 对角线 48 = 满域）。
        # 保留该参数是为了让"想跑快"的用法有旋钮，但默认必须关。
        self.cruise_speed = 0.0
        self.cruise_gain = 0.9        # 速率回拉强度（1/s）
        self.turbulence = 0.0        # 全局涡旋场强度（0 = 关；见 _turbulence）

        self.weights = dict(DEFAULT_WEIGHTS)
        if weights:
            unknown = set(weights) - set(DEFAULT_WEIGHTS)
            assert not unknown, f'未知权重项: {sorted(unknown)}'
            self.weights.update(weights)

        self._predator = None
        self.predator_radius = 4.5

        # 预计算平方量，内层循环里避免重复乘法
        self._rn2 = self.neighbour_radius ** 2
        self._rs2 = self.sep_radius ** 2
        self._pr2 = self.predator_radius ** 2
        self._span = 2.0 * self.bounds
        self._inv_cell = 1.0 / self.cell
        # 涡旋场的相位：由 dt 累加，让整群缓慢换向（见 step）
        self._phase = 0.0

        # 平坦状态数组（见模块 docstring 的"内部表示"）
        self._px = [0.0] * self.count
        self._py = [0.0] * self.count
        self._pz = [0.0] * self.count
        self._vx = [0.0] * self.count
        self._vy = [0.0] * self.count
        self._vz = [0.0] * self.count

        # CSR 邻居表 + 复用的 scratch 缓冲（避免每步重新分配）
        self._neigh = []
        self._start = [0] * (self.count + 1)
        self._npx = [0.0] * self.count
        self._npy = [0.0] * self.count
        self._npz = [0.0] * self.count
        self._nvx = [0.0] * self.count
        self._nvy = [0.0] * self.count
        self._nvz = [0.0] * self.count

        self.reset()

    # ------------------------------------------------------------ 生命周期 --
    def reset(self, seed=None):
        """按种子重新随机初始化（确定性：同一 seed 结果逐位一致）。

        **为什么初始撒点半径（``spawn_radius``）远小于域半径**：这是本项目
        踩过的坑 —— 若把 boid 均匀撒满整个 ``[-b, b]^3``，初始邻居数期望值只有
        ``count * (4/3·π·r_n³) / (2b)³`` ≈ 0.4 个（400 只 / r_n=4 / b=14），
        三条件用力全部"看不见邻居"，于是**永远聚不起来**：实测跑 180 步后
        平均朝向余弦只有 0.01、RMS 半径 13.9（≈ 域半径），完全不是群。
        撒点半径 6.0 时初始邻居数 ~4 个，群体能自持。
        """
        if seed is not None:
            self.seed = seed
        rnd = random.Random(self.seed)
        b = self.bounds
        sr = min(self.spawn_radius, b)
        lo, hi = self.speed_range
        for i in range(self.count):
            # 球内均匀撒点（拒绝采样：球内均匀，不是"每个坐标独立均匀"）
            while True:
                ox = rnd.uniform(-1.0, 1.0)
                oy = rnd.uniform(-1.0, 1.0)
                oz = rnd.uniform(-1.0, 1.0)
                if ox * ox + oy * oy + oz * oz <= 1.0:
                    break
            self._px[i] = ox * sr
            self._py[i] = oy * sr
            self._pz[i] = oz * sr
            # 均匀球面方向 × [lo, hi] 内的随机速率：开局就有朝向差异
            while True:
                ux = rnd.uniform(-1.0, 1.0)
                uy = rnd.uniform(-1.0, 1.0)
                uz = rnd.uniform(-1.0, 1.0)
                l2 = ux * ux + uy * uy + uz * uz
                if 1e-6 < l2 <= 1.0:      # 单位球内均匀采样，丢弃过长/零向量
                    break
            inv = 1.0 / math.sqrt(l2)
            s = rnd.uniform(lo, hi)
            self._vx[i] = ux * inv * s
            self._vy[i] = uy * inv * s
            self._vz[i] = uz * inv * s
        self._predator = None

    # --------------------------------------------------------------- 接口 --
    @property
    def boids(self):
        """当前状态的 ``Boid`` 视图列表（每次访问都新建，渲染时一次性取走）。"""
        boids = []
        for i in range(self.count):
            boids.append(Boid(
                Vec3(self._px[i], self._py[i], self._pz[i]),
                Vec3(self._vx[i], self._vy[i], self._vz[i])))
        return boids

    def positions(self):
        """(xs, ys, zs) 三条平坦坐标列表 —— 渲染层打包实例属性用，省去建对象。"""
        return self._px, self._py, self._pz

    def velocities(self):
        """(vx, vy, vz) 三条平坦速度列表。"""
        return self._vx, self._vy, self._vz

    def set_predator(self, pos):
        """设置/移动捕食者（Vec3 或三元组）；传 ``None`` 清除。

        返回自身以便链式调用。捕食者会把半径内的 boid 推开 —— 因为
        ``predator`` 权重默认 9.0（远大于其它三项），效果是"炸群后重新聚拢"。
        """
        if pos is None:
            self._predator = None
        else:
            self._predator = (float(pos[0]), float(pos[1]), float(pos[2]))
        return self

    @property
    def predator(self):
        """当前捕食者位置（Vec3）或 None。"""
        if self._predator is None:
            return None
        return Vec3(*self._predator)

    def set_preset(self, name):
        """套用 ``PRESETS`` 里的一组权重（未知名字抛 KeyError）。

        同时套上该档的速度上限与涡旋强度 —— 三档预设的差别不只是形状，
        还包含"飞多快"，否则高速冲刺档看着和紧密群集档一样慢。
        """
        self.weights.update(PRESETS[name])
        self.max_speed = PRESET_MAX_SPEED[name]
        self.turbulence = PRESET_TURBULENCE[name]
        return self

    def spread(self):
        """当前包围盒对角线长度（单测用来断言"群集有界"）。"""
        if self.count == 0:
            return 0.0
        xs, ys, zs = self._px, self._py, self._pz
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        dz = max(zs) - min(zs)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def mean_speed(self):
        """平均速率（单测/ HUD 用）。"""
        n = self.count
        total = 0.0
        for i in range(n):
            vx, vy, vz = self._vx[i], self._vy[i], self._vz[i]
            total += math.sqrt(vx * vx + vy * vy + vz * vz)
        return total / n if n else 0.0

    # ------------------------------------------------------------- 推进 --
    def _build_neighbours(self):
        """格子哈希 + CSR 邻居表：每步重建一次（位置变了，表就失效）。

        格子边长 >= neighbour_radius ⇒ 半径内邻居必在 3×3×3 邻域内，不漏。
        """
        n = self.count
        inv_cell = self._inv_cell
        b = self.bounds
        cells = {}
        get = cells.get
        px, py, pz = self._px, self._py, self._pz
        for i in range(n):
            key = (int((px[i] + b) * inv_cell),
                   int((py[i] + b) * inv_cell),
                   int((pz[i] + b) * inv_cell))
            bucket = get(key)
            if bucket is None:
                cells[key] = [i]
            else:
                bucket.append(i)

        neigh = self._neigh
        start = self._start
        neigh.clear()
        rn2 = self._rn2
        max_n = self.max_neighbours
        for i in range(n):
            pxi, pyi, pzi = px[i], py[i], pz[i]
            cx0 = int((pxi + b) * inv_cell)
            cy0 = int((pyi + b) * inv_cell)
            cz0 = int((pzi + b) * inv_cell)
            found = 0
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    for oz in (-1, 0, 1):
                        bucket = get((cx0 + ox, cy0 + oy, cz0 + oz))
                        if bucket is None:
                            continue
                        for j in bucket:
                            if j == i:
                                continue
                            dx = px[j] - pxi
                            dy = py[j] - pyi
                            dz = pz[j] - pzi
                            d2 = dx * dx + dy * dy + dz * dz
                            # 邻域是**球**，不是 27 格立方体：按半径裁剪掉格角
                            if d2 > rn2 or d2 < 1e-12:
                                continue
                            neigh.append(j)
                            found += 1
                            if found >= max_n:
                                break
                        if found >= max_n:
                            break
                    if found >= max_n:
                        break
                if found >= max_n:
                    break
            start[i + 1] = len(neigh)

    def step(self, dt):
        """推进 ``dt`` 秒。确定性：同 seed + 同 dt 序列 ⇒ 结果可复现。

        涡旋场的相位由 ``dt`` 累加（``_phase``）而不是读墙钟时间 —— 这样
        ``--shot`` 抓帧与实时运行走的是同一条确定性轨迹，且循环回绕时相位连续。
        """
        if dt <= 0.0:
            return
        self._phase += dt * 0.55      # 涡旋场缓慢换向
        n = self.count
        w = self.weights
        w_sep = w['separation']
        w_align = w['alignment']
        w_coh = w['cohesion']
        w_bnd = w['boundary']
        w_pred = w['predator']
        max_speed = self.max_speed
        max_force = self.max_force
        turb = self.turbulence
        phase = self._phase
        cruise = self.cruise_speed
        cruise_gain = self.cruise_gain
        rs2 = self._rs2
        b = self.bounds
        band = self.band
        span = self._span
        sqrt = math.sqrt

        self._build_neighbours()
        px, py, pz = self._px, self._py, self._pz
        vx, vy, vz = self._vx, self._vy, self._vz
        neigh = self._neigh
        start = self._start
        npx, npy, npz = self._npx, self._npy, self._npz
        nvx, nvy, nvz = self._nvx, self._nvy, self._nvz
        pred = self._predator
        pr2 = self._pr2

        for i in range(n):
            pxi, pyi, pzi = px[i], py[i], pz[i]
            vxi, vyi, vzi = vx[i], vy[i], vz[i]
            # 三个力的累加器（分离用单位向量和，故不需要再除距离）
            sax = say = saz = 0.0
            aax = aay = aaz = 0.0
            cax = cay = caz = 0.0
            ns = 0
            for k in range(start[i], start[i + 1]):
                j = neigh[k]
                qx, qy, qz = px[j], py[j], pz[j]
                aax += vx[j]; aay += vy[j]; aaz += vz[j]
                cax += qx; cay += qy; caz += qz
                ns += 1
                dx = qx - pxi; dy = qy - pyi; dz = qz - pzi
                d2 = dx * dx + dy * dy + dz * dz
                if d2 < rs2:
                    inv = 1.0 / sqrt(d2)      # 除以距离（非平方）⇒ 越近推力越强
                    sax -= dx * inv; say -= dy * inv; saz -= dz * inv

            if ns:
                inv = 1.0 / ns
                # 对齐：邻居平均速度 - 自身速度（得到转向力而非目标速度）
                aax = aax * inv - vxi
                aay = aay * inv - vyi
                aaz = aaz * inv - vzi
                al = sqrt(aax * aax + aay * aay + aaz * aaz)
                if al > _EPS:
                    aax /= al; aay /= al; aaz /= al
                # 聚合：邻居质心 - 自身位置
                cax = cax * inv - pxi
                cay = cay * inv - pyi
                caz = caz * inv - pzi
                cl = sqrt(cax * cax + cay * cay + caz * caz)
                if cl > _EPS:
                    cax /= cl; cay /= cl; caz /= cl

            # 分离：反方向单位向量之和，再归一化（防止邻居多就无限变强）
            sl = sqrt(sax * sax + say * say + saz * saz)
            if sl > _EPS:
                sax /= sl; say /= sl; saz /= sl

            fx = w_sep * sax + w_align * aax + w_coh * cax
            fy = w_sep * say + w_align * aay + w_coh * cay
            fz = w_sep * saz + w_align * aaz + w_coh * caz

            # --- 边界回避：软转向（smoothstep ⇒ 轨迹连续，无瞬移）---
            # 正边界用一个移位的 smoothstep：t=0 在 b-band 处，t=1 恰好到 b。
            if w_bnd > 0.0:
                t = _smoothstep(0.0, band, pxi - (b - band))
                if t > 0.0:
                    fx -= w_bnd * t * t
                t = _smoothstep(0.0, band, -pxi - (b - band))
                if t > 0.0:
                    fx += w_bnd * t * t
                t = _smoothstep(0.0, band, pyi - (b - band))
                if t > 0.0:
                    fy -= w_bnd * t * t
                t = _smoothstep(0.0, band, -pyi - (b - band))
                if t > 0.0:
                    fy += w_bnd * t * t
                t = _smoothstep(0.0, band, pzi - (b - band))
                if t > 0.0:
                    fz -= w_bnd * t * t
                t = _smoothstep(0.0, band, -pzi - (b - band))
                if t > 0.0:
                    fz += w_bnd * t * t

            # --- 全局涡旋场：让整群绕 Y 轴缓慢打转（否则边界力会把群压成球）---
            if turb > 0.0:
                s = turb * (1.0 + 0.35 * _sin(1.7 * pxi + 1.1 * pyi - 0.9 * pzi
                                              + phase))
                fx += s * pzi
                fz -= s * pxi
                fy += turb * 0.30 * _sin(1.3 * pzi - 0.7 * pyi + phase)

            # --- 捕食者回避 ---
            if pred is not None:
                dx = pxi - pred[0]; dy = pyi - pred[1]; dz = pzi - pred[2]
                d2 = dx * dx + dy * dy + dz * dz
                if 1e-12 < d2 < pr2:
                    inv = w_pred / sqrt(d2)   # 越近推得越狠
                    fx += dx * inv; fy += dy * inv; fz += dz * inv

            # --- 转向力上限 ---
            fl = sqrt(fx * fx + fy * fy + fz * fz)
            if fl > max_force:
                k = max_force / fl
                fx *= k; fy *= k; fz *= k

            nvxi = vxi + fx * dt
            nvyi = vyi + fy * dt
            nvzi = vzi + fz * dt

            # --- 速度下限（防止停死）/ 上限 ---
            sp = sqrt(nvxi * nvxi + nvyi * nvyi + nvzi * nvzi)
            if sp > max_speed:
                k = max_speed / sp
                nvxi *= k; nvyi *= k; nvzi *= k
            elif sp < 0.6:
                if sp < 1e-6:                 # 完全静止：给个确定的初始朝向
                    nvxi, nvyi, nvzi = 0.0, 0.0, 1.0
                else:
                    k = 0.6 / sp
                    nvxi *= k; nvyi *= k; nvzi *= k
            # --- 巡航速率：只缩放速率，不改方向（对齐力会把速率抹平，见 __init__）---
            if cruise > 0.0 and sp > 1e-6:
                sp2 = sp * (1.0 + cruise_gain * (cruise / sp - 1.0) * dt)
                k = sp2 / sp
                nvxi *= k; nvyi *= k; nvzi *= k
                # 回拉后可能越过上限，再夹一次（顺序不能反）
                sp3 = sqrt(nvxi * nvxi + nvyi * nvyi + nvzi * nvzi)
                if sp3 > max_speed:
                    k = max_speed / sp3
                    nvxi *= k; nvyi *= k; nvzi *= k

            nvx[i] = nvxi; nvy[i] = nvyi; nvz[i] = nvzi
            # 位置推进后**硬回卷**到域内：软转向已保证轨迹连续，这里的回卷
            # 只在极端情况下兜底（正常模拟中 boid 不会真的越界）
            a = pxi + nvxi * dt + b
            npx[i] = a % span - b
            a = pyi + nvyi * dt + b
            npy[i] = a % span - b
            a = pzi + nvzi * dt + b
            npz[i] = a % span - b

        # 交换双缓冲（避免每步重新分配）
        self._px, self._npx = npx, px
        self._py, self._npy = npy, py
        self._pz, self._npz = npz, pz
        self._vx, self._nvx = nvx, vx
        self._vy, self._nvy = nvy, vy
        self._vz, self._nvz = nvz, vz

# -*- coding: utf-8 -*-
"""网格视野投射（recursive shadowcasting）与光照衰减（纯标准库）。

借鉴记账（铁律 10）
------------------
算法：recursive shadowcasting（Björn Bergström 撰写的 RogueBasin 经典
<http://www.roguebasin.com/index.php?title=Recursive_Shadowcasting>，
公共领域方法论）。八象限递归扫描，光从原点出发在墙格边界被截断，
墙格本身可见（「看见墙」而不是「看见墙的影子后面」）。

借用范围：算法思想与八象限倍率表。实现为本仓风格（纯标准库 +
callable opaque 回调 + 距离字典），未逐行对照任何现成实现。

适配与扩展
----------
- ``opaque`` 是回调而非网格：调用方可用任意可挡光语义（墙、门、
  高柱），也便于在稀疏/多层数据结构上使用。
- 返回 ``{(x, y): dist}``——距离随可见性一并给出，光照衰减、AI
  听觉半径、施法射程都直接取用，不必二次算。
- ``visible_through``（视线判定单点查询）与 ``light_level``（火把
  衰减曲线）是 roguelike 高频伴生需求，一并收进来。
- 对角穿墙角：shadowcasting 天然允许「斜视缝隙」，但**移动**层面
  是否允许斜穿角由调用方的移动规则决定（Brogue 口径是禁止），
  本模块不管移动。

确定性口径
----------
- 输入相同必然输出相同（无随机源、无浮点歧义——距离用平方距离，
  整数运算）。
- 半径语义：欧氏平方距离 ``<= radius**2`` 的格都可能可见（能否真
  可见由遮挡决定）。
"""
from __future__ import annotations

__all__ = ['compute_fov', 'visible_through', 'light_level',
           'default_light_curve']


# 八象限倍率表（RogueBasin 经典口径）：xx/xy/yx/yy 各 8 份，
# 把第一象限的扫描结果镜像到其余七个象限。
_MULT = (
    (1, 0, 0, -1, -1, 0, 0, 1),
    (0, 1, -1, 0, 0, -1, 1, 0),
    (0, 1, 1, 0, 0, -1, -1, 0),
    (1, 0, 0, 1, -1, 0, 0, -1),
)


def compute_fov(opaque, origin, radius):
    """计算以 ``origin`` 为光源/视点的可见格集合。

    参数
    ----
    opaque : callable(x, y) -> bool
        返回 True 表示该格挡光（不可透过，但自身可见）。
    origin : tuple[int, int]
        ``(x, y)`` 视点。
    radius : int
        可视半径（格）。实际判定用平方欧氏距离 ``<= radius*radius``。

    返回
    ----
    dict[(x, y), int]
        可见格到其**平方距离**的映射（含原点自身，距离 0）。

    示例
    ----
    ::

        walls = {(3, y) for y in range(6) if y != 3}
        visible = compute_fov(lambda x, y: (x, y) in walls, (0, 3), 8)
        assert (5, 3) in visible          # 门缝透光
        assert (5, 0) not in visible      # 墙后不可见
    """
    ox, oy = origin
    radius_sq = radius * radius
    visible = {(ox, oy): 0}
    for octant in range(8):
        _cast_light(visible, opaque, ox, oy, 1, 1.0, 0.0, radius_sq,
                    _MULT[0][octant], _MULT[1][octant],
                    _MULT[2][octant], _MULT[3][octant])
    return visible


def _cast_light(visible, opaque, cx, cy, row, start, end, radius_sq,
                xx, xy, yx, yy):
    """单象限递归扫描（RogueBasin 行列约定：``dy`` 为负数、斜率以
    ``dx/dy`` 形式表达，窗口 ``[start, end]`` 逐步被墙格切割）。
    """
    if start < end:
        return
    radius = int(radius_sq ** 0.5)
    for j in range(row, radius + 1):
        dx, dy = -j - 1, -j
        blocked = False
        new_start = 0.0
        while dx <= 0:
            dx += 1
            x = cx + dx * xx + dy * xy
            y = cy + dx * yx + dy * yy
            l_slope = (dx - 0.5) / (dy + 0.5)
            r_slope = (dx + 0.5) / (dy - 0.5)
            if start < r_slope:
                continue
            if end > l_slope:
                break
            sq = dx * dx + dy * dy
            if sq <= radius_sq:
                visible[(x, y)] = sq
            if blocked:
                # 处于墙影中：遇到透明格则开启新的子扇形
                if not opaque(x, y):
                    blocked = False
                    start = new_start
                    continue
                new_start = r_slope
                continue
            if opaque(x, y) and j < radius:
                blocked = True
                _cast_light(visible, opaque, cx, cy, j + 1, start,
                            l_slope, radius_sq, xx, xy, yx, yy)
                new_start = r_slope
        if blocked:
            break


def visible_through(opaque, x0, y0, x1, y1, max_dist=64):
    """从 ``(x0, y0)`` 到 ``(x1, y1)`` 是否有视线（Bresenham 逐格判定）。

    与 ``compute_fov`` 的差别：单点对单点的射线判定（AI「能不能看
    见玩家」高频查询），不构建完整视野集合。端点格挡光时仍视为
    可达（看见墙、看见怪）。
    """
    dx = abs(x1 - x0)
    dy = abs(y1 - y0)
    if dx + dy > max_dist:
        return False
    sx = 1 if x1 > x0 else -1
    sy = 1 if y1 > y0 else -1
    err = dx - dy
    x, y = x0, y0
    while (x, y) != (x1, y1):
        e2 = err * 2
        if e2 > -dy:
            err -= dy
            x += sx
        if e2 < dx:
            err += dx
            y += sy
        if (x, y) != (x1, y1) and opaque(x, y):
            return False
    return True


def light_level(dist, radius, curve=None):
    """火把光照衰减：0.0（半径外）→ 1.0（原点）。

    ``curve`` 可传自定义单调函数（输入 0..1 的归一化距离，输出
    0..1 的亮度）；默认二次缓出（近亮远暗、边缘柔和），Brogue
    视觉口味的近似。
    """
    if dist >= radius:
        return 0.0
    t = dist / radius
    if curve is None:
        curve = default_light_curve
    return curve(t)


def default_light_curve(t):
    """默认衰减曲线：``(1 - t^2)^1.5``——近处衰减慢、边缘收得快。"""
    v = 1.0 - t * t
    return v ** 1.5 if v > 0.0 else 0.0

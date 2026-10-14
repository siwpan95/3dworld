# -*- coding: utf-8 -*-
"""诺德之境（core 层）：上古卷轴风格雪原山谷。

对齐《上古卷轴V：天际》的视觉语言，程序化重建"北境"标志性元素：

  雪原雪丘 / 松林 / 诺德长屋（木板墙 + 茅草顶 + 暖窗光）/ 石柱阵与塌拱
  / 龙语墙（符文刻痕，低语点亮时自发光）/ 篝火 / 远山环 / 星空月轮
  / 极光帘幕 / 飘雪 / 飞龙（管扫掠躯体 + 翼膜 + 口中龙焰）

零素材：几何全部即时生成，材质走 engine.proctex 的程序化贴图
（wood_planks / thatch / rock / rune_stone / snow / pine / dragon_scale /
aurora）。世界布局常量供 logic/nordic_saga.py 的相机与龙轨迹对齐。

静态几何按材质合并成少量大网格（draw call ≈ 8）；动态对象（龙/翼/焰/
极光/篝火/光柱/飘雪）各自独立，逐帧驱动 model 矩阵与材质。
"""
import math

from world3d.engine.geometry import (box, cylinder, lathe, merge_meshes, plane,
                             sphere, tube)
from world3d.engine.mathkit import quat_from_axis_angle
from world3d.engine.material import Material, WHITE_TEX
from world3d.engine.mesh import Mesh
from world3d.engine.proctex import material_from

# ------------------------------------------------------------- 世界布局 --
GROUND_W, GROUND_D = 190.0, 240.0       # 雪原尺寸（中心在 (0, -40)）
GROUND_CZ = -40.0

WORDWALL = (0.0, -43.0)                 # 龙语墙中心 (x, z)，墙面朝 +z
WORDWALL_W, WORDWALL_H, WORDWALL_D = 9.4, 5.4, 1.4
COL_ROWS = ((-21.0, 5.9), (-25.6, 6.1), (-30.2, 2.6), (-34.8, 6.0))
COL_X = 6.6                             # 石柱排 |x|
ARCH_X, ARCH_Z = 11.6, -30.0            # 拱门（左完好 / 右坍塌）
CAMPFIRE = (5.8, -15.0)                 # 篝火 (x, z)
LONGHOUSE = (-10.5, -8.0)               # 长屋中心 (x, z)
HUT = (11.0, -13.5)                     # 小仓房 (x, z)
DRAGON_HOVER = (0.0, 7.2, -33.5)        # 龙悬停点（龙语墙前上空）
BEAM_POS = (0.0, 5.9, -43.0)            # 符文光柱（墙顶）

# 远山环：(x, z, 半径, 高度)——环抱北面与两翼；h/r 比拉高（陡峭雪峰感）
MOUNTAIN_DEFS = (
    (-92, -150, 34, 58), (-30, -170, 40, 70), (34, -150, 30, 52),
    (86, -128, 25, 44), (-140, -122, 29, 50), (132, -112, 22, 36),
    (-190, -112, 26, 46), (-64, -106, 17, 30), (152, -168, 37, 62),
    (-234, -98, 22, 36), (196, -104, 20, 32),
    (-40, -92, 13, 20), (52, -96, 15, 23),
    (16, -66, 8.0, 10.5), (-26, -70, 9.0, 12.0),
)

_TAU = 2.0 * math.pi


def _hash01(*args):
    """确定性伪随机 [0,1)——与 kkrieger_scene 同款（存生成史不存像素）。"""
    s = math.sin(sum(a * (61.7 + 29.3 * k) for k, a in enumerate(args, 1))) \
        * 43758.5453
    return s - math.floor(s)


def _rot_z(deg):
    """列主序绕 Z 旋转矩阵（俯仰斜面用）。"""
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [c, s, 0, 0, -s, c, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def _rot_x(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [1, 0, 0, 0, 0, c, s, 0, 0, -s, c, 0, 0, 0, 0, 1]


def _rot_y(deg):
    c, s = math.cos(math.radians(deg)), math.sin(math.radians(deg))
    return [c, 0, -s, 0, 0, 1, 0, 0, s, 0, c, 0, 0, 0, 0, 1]


def _trans(x, y, z):
    return [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, x, y, z, 1]


def _mul(a, b):
    """列主序矩阵乘 a·b（先 b 后 a）。"""
    out = [0.0] * 16
    for c in range(4):
        for r in range(4):
            out[c * 4 + r] = (a[r] * b[c * 4] + a[4 + r] * b[c * 4 + 1]
                              + a[8 + r] * b[c * 4 + 2] + a[12 + r] * b[c * 4 + 3])
    return out


def _uv_scale(md, su, sv):
    """整体缩放 UV（贴图密度控制）。"""
    uvs = md.uvs
    for i in range(0, len(uvs), 2):
        uvs[i] *= su
        uvs[i + 1] *= sv
    return md


def _place(md, x, y, z, rz=0.0, rx=0.0):
    """旋转（可选 rz/rx）后平移到世界位置（原地）。"""
    if rx:
        md.transform(_rot_x(rx))
    if rz:
        md.transform(_rot_z(rz))
    md.transform(_trans(x, y, z))
    return md


def snow_height(x, z):
    """雪原起伏：三组正弦叠加（幅度收敛，近景不形成大片背光黑脊），
    核心区（遗迹/通道）压平到 22%。"""
    b = (math.sin(x * 0.135 + 0.7) * math.sin(z * 0.105 - 0.4) * 0.30
         + math.sin(x * 0.31 + 1.7) * math.sin(z * 0.235 + 0.6) * 0.15
         + math.sin(x * 0.058 - 0.8) * math.sin(z * 0.047 + 2.1) * 0.48)
    dx_in = 1.0 - min(1.0, max(0.0, (abs(x) - 12.0) / 10.0))
    dz_out = max(0.0, -52.0 - z, z - 8.0)
    dz_in = 1.0 - min(1.0, dz_out / 10.0)
    core = min(dx_in, dz_in)
    return b * (0.22 + 0.78 * (1.0 - core))


# ---------------------------------------------------------------- 材质 --
def build_materials():
    """全部材质（含发光体：纯色发光靠 1x1 白贴图 × emissive uniform）。"""
    W = WHITE_TEX()

    def glow(name, color, base=(0.015, 0.016, 0.022)):
        return Material(name, base_color=base, metallic=0.0, roughness=1.0,
                        emissive=color, emissive_tex=W)

    mats = {
        'snow': material_from('snow', size=512, name='mat_snow'),
        'rock': material_from('rock', size=256, name='mat_rock',
                              pattern_kwargs={'seed': 21}),
        'wood': material_from('wood_planks', size=256, name='mat_wood'),
        'thatch': material_from('thatch', size=256, name='mat_thatch'),
        'pine': material_from('pine', size=256, name='mat_pine'),
        'rune': material_from('rune_stone', size=512, name='mat_rune'),
        'dragon': material_from('dragon_scale', size=512, name='mat_dragon',
                                pattern_kwargs={'seed': 31}),
        'moon': glow('mat_moon', (2.05, 2.15, 2.40)),
        'stars': glow('mat_stars', (1.45, 1.55, 1.80)),
        'fire': glow('mat_fire', (2.60, 1.05, 0.26), base=(0.35, 0.10, 0.02)),
        'eyes': glow('mat_eyes', (2.80, 1.35, 0.30)),
        'win': glow('mat_win', (1.70, 1.12, 0.50), base=(0.10, 0.06, 0.03)),
        'beam': glow('mat_beam', (0.55, 1.60, 2.30)),
    }
    # 调色：石材转冷青（夜里花岗岩不泛"沙黄"）、雪提亮；龙鳞灰化
    #（纯黑炭色在夜景里是黑洞，巨龙要能看出"月色轮廓"）
    mats['rock'].base_color = (0.86, 0.90, 1.00)
    mats['snow'].base_color = (1.04, 1.05, 1.08)
    mats['dragon'].base_color = (1.30, 1.36, 1.42)
    # 龙语墙：深色石（夜里不该白晃晃——点亮时靠符文与冷光形成对比）
    mats['rune'].base_color = (0.52, 0.55, 0.62)
    # 雪峰冠单独一份（淡灰蓝，夜里被月光罩住的雪——纯白会读成"月亮"）
    mats['snowcap'] = material_from('snow', size=256, name='mat_snowcap')
    mats['snowcap'].base_color = (0.78, 0.84, 0.96)
    # 龙语墙初始未点亮：material_from 见到发光贴图会默认 (1,1,1) 全亮，
    # 必须手动压回 0（"刻痕"只以 base 里的压暗体现，点灯由 logic 控制）
    mats['rune'].emissive = (0.0, 0.0, 0.0)
    mats['beam'].emissive = (0.0, 0.0, 0.0)
    # 极光：4 条独立材质（各自脉动/色调），走 aurora 渐变贴图的 emissive；
    # base 压黑 ⇒ 只发自身的光，不受太阳/IBL 影响。系数保持克制：
    # emissive 过高 + Bloom 会让极光变成一堵发光的墙
    for k, tint in enumerate(((1.15, 1.25, 1.15), (0.92, 1.08, 1.42),
                              (1.24, 1.02, 1.22), (0.85, 1.26, 1.12))):
        a = material_from('aurora', size=64, name=f'mat_aurora_{k}')
        a.base_color = (0.0, 0.0, 0.0)
        a.emissive = tint
        mats[f'aurora_{k}'] = a
    return mats


# ------------------------------------------------------------- 各元素 --
def _add_ground(acc):
    geo = plane(GROUND_W, GROUND_D, segs=80, y=0.0)
    for i in range(geo.vertex_count):
        x, z = geo.positions[i * 3], geo.positions[i * 3 + 2] + GROUND_CZ
        geo.positions[i * 3 + 2] = z
        geo.positions[i * 3 + 1] = snow_height(x, z)
    geo.compute_normals(force=True)          # 位移后必须重算法线
    _uv_scale(geo, 36.0, 36.0)
    acc.setdefault('snow', []).append(geo)


def _add_mountains(acc):
    for x, z, r, h in MOUNTAIN_DEFS:
        body = lathe([(0.0, 0.0), (r, 0.0), (r * 0.55, h * 0.55),
                      (r * 0.22, h * 0.85), (0.001, h)],
                     segments=10, closed_bottom=True)
        _uv_scale(body, 4.0, 3.0)
        _place(body, x, -0.5, z)
        acc.setdefault('rock', []).append(body)
        cap = lathe([(r * 0.40, h * 0.58), (r * 0.22, h * 0.78),
                     (r * 0.08, h * 0.93), (0.001, h + 0.3)],
                    segments=10, closed_bottom=False)   # 更尖的雪峰（钝锥
        _uv_scale(cap, 3.0, 2.0)                        # 从远处看像"白球"）
        _place(cap, x, -0.5, z)
        acc.setdefault('snowcap', []).append(cap)


def _tree_spots():
    """确定性散布 26 棵树，避开通道/建筑/遗迹广场。

    近区（z > -40）的树再推开到 |x| ≥ 14：贴地掠行镜头从 x≈4 通过时，
    |x|=8~10 的树会在 10m 内形成劈面而来的黑色剪影（"黑柱"撞脸）。
    """
    spots = []
    i = 0
    while len(spots) < 26 and i < 500:
        i += 1
        x = (_hash01(i, 11, 3) - 0.5) * 92.0
        z = 6.0 - _hash01(i, 23, 7) * 100.0
        if abs(x) < 8.0 and z > -60.0:                       # 相机通道
            continue
        if z > -40.0 and abs(x) < 14.0:                      # 近区树推开
            continue
        if abs(x) < 14.0 and -52.0 < z < -16.0:              # 遗迹广场
            continue
        if abs(x - LONGHOUSE[0]) < 9.5 and abs(z - LONGHOUSE[1]) < 9.5:
            continue
        if abs(x - HUT[0]) < 6.5 and abs(z - HUT[1]) < 6.5:
            continue
        if abs(x - CAMPFIRE[0]) < 4.5 and abs(z - CAMPFIRE[1]) < 4.5:
            continue
        spots.append((x, z, 5.4 + 4.6 * _hash01(i, 31, 5)))
    return spots


def _add_forest(acc):
    for k, (x, z, h) in enumerate(_tree_spots()):
        y = snow_height(x, z) - 0.25
        trunk_h = h * 0.34
        trunk = cylinder(0.13 + h * 0.012, trunk_h, 7)
        _uv_scale(trunk, 1.0, 1.6)
        _place(trunk, x, y, z)
        acc.setdefault('wood', []).append(trunk)
        layers = 3
        for l in range(layers):
            t = l / (layers - 1)
            r = h * (0.24 - 0.10 * t)
            base_y = y + trunk_h * 0.62 + (h * 0.72 - trunk_h * 0.62) * t * 0.9
            ch = h * 0.46 * (1.0 - 0.22 * t)
            cone = lathe([(0.0, 0.0), (r, 0.0), (r * 0.55, ch * 0.5),
                          (0.001, ch)], segments=8, closed_bottom=True)
            _uv_scale(cone, 2.0, 2.0)
            _place(cone, x, base_y, z)
            acc.setdefault('pine', []).append(cone)


def _gable(w, h, x, y, z, facing):
    """山墙三角（端面朝向 ±z）：墙顶到屋脊的封山片。"""
    from world3d.engine.geometry import MeshData
    md = MeshData([], [], [])
    for sx, sy in ((-w / 2, 0.0), (w / 2, 0.0), (0.0, h)):
        md.positions += [x + sx, y + sy, z]
        md.normals += [0.0, 0.0, float(facing)]
        md.uvs += [0.5 + sx / w, sy / h]
    return md


def _sloped_roof(acc, x, z, span, length, eave_y, ridge_h, mat_key='thatch'):
    """双坡屋顶：两块旋转斜板 + 屋脊。x 向跨度 span（半宽 span/2）。"""
    half = span / 2.0
    slope = math.degrees(math.atan2(ridge_h, half))
    plate_len = math.sqrt(half * half + ridge_h * ridge_h) + 0.35
    for sgn in (-1.0, 1.0):
        plate = box(plate_len, 0.22, length)
        cx = x + sgn * half / 2.0
        cy = eave_y + ridge_h / 2.0
        _uv_scale(plate, 2.2, 5.0)
        _place(plate, cx, cy, z, rz=-sgn * slope)
        acc.setdefault(mat_key, []).append(plate)
    ridge = box(0.55, 0.30, length + 0.4)
    _place(ridge, x, eave_y + ridge_h + 0.05, z)
    acc.setdefault(mat_key, []).append(ridge)


def _add_longhouse(acc):
    x0, z0 = LONGHOUSE
    # 石基 + 木墙
    base = box(7.4, 0.7, 13.0)
    _place(base, x0, 0.10, z0)
    acc.setdefault('rock', []).append(base)
    wall = box(6.4, 2.8, 11.8)
    _uv_scale(wall, 3.2, 1.4)
    _place(wall, x0, 0.45 + 1.4, z0)
    acc.setdefault('wood', []).append(wall)
    for zz in (z0 - 5.9, z0 + 5.9):
        g = _gable(6.4, 2.9, x0, 3.25, zz, 1.0 if zz > z0 else -1.0)
        _uv_scale(g, 2.5, 1.5)
        acc.setdefault('wood', []).append(g)
    _sloped_roof(acc, x0, z0, 8.6, 12.6, 3.25, 2.9)
    # 门（面向 +x 广场）与暖窗
    door = box(0.18, 2.3, 1.5)
    _place(door, x0 + 3.25, 0.45 + 1.15, z0 + 1.2)
    acc.setdefault('wood', []).append(door)
    for wz in (z0 - 2.6, z0 + 3.8):
        win = box(0.16, 0.95, 0.95)
        _place(win, x0 + 3.22, 0.45 + 1.55, wz)
        acc.setdefault('win', []).append(win)
    win2 = box(0.9, 0.9, 0.16)
    _place(win2, x0 + 1.1, 0.45 + 1.55, z0 + 5.92)
    acc.setdefault('win', []).append(win2)
    # 烟囱（露出屋脊 1m 以上，否则整根埋在坡面里）
    chim = box(0.75, 2.6, 0.75)
    _place(chim, x0 - 1.4, 5.25, z0 - 3.6)
    acc.setdefault('rock', []).append(chim)


def _add_hut(acc):
    x0, z0 = HUT
    base = box(4.0, 0.5, 4.8)
    _place(base, x0, 0.05, z0)
    acc.setdefault('rock', []).append(base)
    wall = box(3.5, 2.1, 4.2)
    _uv_scale(wall, 2.0, 1.0)
    _place(wall, x0, 0.3 + 1.05, z0)
    acc.setdefault('wood', []).append(wall)
    for zz in (z0 - 2.1, z0 + 2.1):
        g = _gable(3.5, 1.9, x0, 2.4, zz, 1.0 if zz > z0 else -1.0)
        acc.setdefault('wood', []).append(g)
    _sloped_roof(acc, x0, z0, 5.0, 4.6, 2.4, 1.9)
    door = box(0.16, 1.8, 1.1)
    _place(door, x0 - 1.77, 0.3 + 0.9, z0)
    acc.setdefault('wood', []).append(door)


def _add_ruins(acc):
    # 台基三级台阶
    for i, (w, yy) in enumerate(((15.6, 0.22), (14.4, 0.62), (13.2, 1.02))):
        step = box(w, 0.42, 1.35)
        _uv_scale(step, 4.0, 0.6)
        _place(step, 0.0, yy, -36.2 - i * 1.15)
        acc.setdefault('rock', []).append(step)
    # 两排石柱：完好 5 根 + 断柱 3 根（含斜置残块）
    broken = {2: 2.6, 5: 4.3, 6: 3.2}
    idx = 0
    for z, hgood in COL_ROWS:
        for sx in (-1.0, 1.0):
            x = sx * COL_X
            h = broken.get(idx, hgood)
            shaft = cylinder(0.60, h, 12)
            _uv_scale(shaft, 2.0, 2.4)
            _place(shaft, x, 1.0, z)
            acc.setdefault('rock', []).append(shaft)
            base = box(1.5, 0.5, 1.5)
            _place(base, x, 1.05, z)
            acc.setdefault('rock', []).append(base)
            if idx in broken:
                chunk = box(1.15, 0.55, 1.15)
                _place(chunk, x + sx * 0.5, 1.0 + h + 0.3, z, rz=sx * 21.0)
                acc.setdefault('rock', []).append(chunk)
            else:
                cap = box(1.45, 0.5, 1.45)
                _place(cap, x, 1.0 + h + 0.25, z)
                acc.setdefault('rock', []).append(cap)
            idx += 1
    # 左拱完好
    for dx in (-2.5, 2.5):
        col = cylinder(0.78, 7.0, 12)
        _uv_scale(col, 2.0, 2.6)
        _place(col, -ARCH_X + dx, 0.0, ARCH_Z)
        acc.setdefault('rock', []).append(col)
    lintel = box(6.4, 0.9, 1.1)
    _place(lintel, -ARCH_X, 7.35, ARCH_Z)
    acc.setdefault('rock', []).append(lintel)
    # 右拱坍塌：一柱半截 + 横梁落地斜倚
    for dx, hh in ((-2.5, 4.6), (2.5, 2.1)):
        col = cylinder(0.78, hh, 12)
        _uv_scale(col, 2.0, 2.6)
        _place(col, ARCH_X + dx, 0.0, ARCH_Z)
        acc.setdefault('rock', []).append(col)
    fallen = box(6.6, 0.85, 1.1)
    _place(fallen, ARCH_X + 1.2, 1.15, ARCH_Z + 0.4, rz=-13.0)
    acc.setdefault('rock', []).append(fallen)
    # 龙语墙主体 + 檐口 + 侧翼 + 前阶 + 守护石 + 背后残墙
    wall = box(WORDWALL_W, WORDWALL_H, WORDWALL_D)
    _uv_scale(wall, 2.2, 1.0)
    _place(wall, WORDWALL[0], 0.55 + WORDWALL_H / 2.0, WORDWALL[1])
    acc.setdefault('rune', []).append(wall)
    cornice = box(WORDWALL_W + 0.9, 0.55, WORDWALL_D + 0.6)
    _place(cornice, WORDWALL[0], 0.55 + WORDWALL_H + 0.27, WORDWALL[1])
    acc.setdefault('rock', []).append(cornice)
    for sx in (-1.0, 1.0):
        wing = box(1.5, 3.9, 1.25)
        _place(wing, sx * 5.4, 1.95, WORDWALL[1] - 0.5)
        acc.setdefault('rock', []).append(wing)
        guard = box(0.95, 2.3, 0.95)
        _place(guard, sx * 3.9, 1.15, WORDWALL[1] + 2.4)
        acc.setdefault('rock', []).append(guard)
    front = box(11.2, 0.42, 1.9)
    _uv_scale(front, 5.0, 0.7)
    _place(front, 0.0, 0.21, WORDWALL[1] + 1.8)
    acc.setdefault('rock', []).append(front)
    behind = box(6.6, 2.3, 0.9)
    _place(behind, 2.6, 1.15, WORDWALL[1] - 3.6)
    acc.setdefault('rock', []).append(behind)


def _blocked(x, z):
    """细节散布禁区：相机通道 / 建筑 / 篝火 / 遗迹广场 / 龙语墙前广场。"""
    if abs(x) < 5.5 and z > -40.0:                       # 中轴通道
        return True
    if abs(x - LONGHOUSE[0]) < 8.0 and abs(z - LONGHOUSE[1]) < 8.0:
        return True
    if abs(x - HUT[0]) < 5.0 and abs(z - HUT[1]) < 5.0:
        return True
    if abs(x - CAMPFIRE[0]) < 3.0 and abs(z - CAMPFIRE[1]) < 3.0:
        return True
    if abs(x) < 15.0 and -54.0 < z < -14.0:              # 遗迹广场
        return True
    if abs(x) < 26.0 and z < -52.0 and z > -74.0:        # 龙语墙后景
        return True
    return False


def _add_details(acc):
    """贴地掠行段的前景素材：枯草簇 + 半埋雪岩（散布时确定性避让）。"""
    n_grass = 0
    i = 0
    while n_grass < 22 and i < 400:
        i += 1
        x = (_hash01(i, 61, 9) - 0.5) * 66.0
        z = -2.0 - _hash01(i, 67, 9) * 56.0
        if _blocked(x, z):
            continue
        n_grass += 1
        y = snow_height(x, z) - 0.04
        for k in range(5):
            gx = x + (_hash01(i, k, 71) - 0.5) * 1.2
            gz = z + (_hash01(i, k, 73) - 0.5) * 1.2
            blade = lathe([(0.0, 0.0), (0.058, 0.03), (0.024, 0.40),
                           (0.001, 0.62)], segments=5)
            _place(blade, gx, y, gz, rx=(_hash01(i, k, 79) - 0.5) * 32.0)
            acc.setdefault('thatch', []).append(blade)
    n_rock = 0
    i = 0
    while n_rock < 12 and i < 400:
        i += 1
        x = (_hash01(i + 900, 61, 13) - 0.5) * 70.0
        z = -6.0 - _hash01(i + 900, 67, 13) * 60.0
        if _blocked(x, z):
            continue
        n_rock += 1
        s = 0.5 + _hash01(i + 900, 71, 13) * 1.1
        rock = lathe([(0.0, -0.35), (s * 0.8, -0.18), (s, 0.16),
                      (s * 0.55, 0.52), (0.001, 0.62)], segments=7)
        _place(rock, x, snow_height(x, z) - 0.16, z,
               rz=(_hash01(i + 900, 79, 13) - 0.5) * 40.0)
        acc.setdefault('rock', []).append(rock)


def _add_campfire(acc):
    x0, z0 = CAMPFIRE
    y = snow_height(x0, z0) - 0.1
    for k in range(9):                                   # 石圈
        a = _TAU * k / 9.0
        st = box(0.42, 0.36, 0.42)
        _place(st, x0 + math.cos(a) * 1.15, y + 0.16,
               z0 + math.sin(a) * 1.15, rz=_hash01(k, 5, 9) * 40.0)
        acc.setdefault('rock', []).append(st)
    for k in range(4):                                   # 交叉圆木（绕圈倒向中心）
        a = _TAU * k / 4.0 + 0.4
        log = cylinder(0.10, 1.5, 7)
        _uv_scale(log, 1.0, 2.0)
        log.transform(_mul(_rot_y(math.degrees(a)), _rot_x(-54.0)))
        _place(log, x0 + math.cos(a) * 0.30, y + 0.06, z0 + math.sin(a) * 0.30)
        acc.setdefault('wood', []).append(log)


def _aurora_ribbon(width, height, segs=44, bend=0.0, bow=0.0):
    """一条极光帘幕：沿 x 的起伏飘带（v=0 下沿 / v=1 上沿）。

    上沿与下沿**都**做波浪——只给上沿起伏、下沿拉直的话，会读成
    "挂在空中的平底布条"而不是极光。
    """
    from world3d.engine.geometry import MeshData
    md = MeshData([], [], [])
    for i in range(segs + 1):
        u = i / segs
        x = (u - 0.5) * width
        # 高度波动收窄 + 波频提高：波谷太深会读成"一列尖塔"而不是帷幕
        h = height * (0.72 + 0.28 * math.sin(u * _TAU * 2.5 + bend))
        y0 = height * (-0.10 + 0.09 * math.sin(u * _TAU * 3.1 + bend * 1.3))
        z = math.sin(u * math.pi + bend * 0.7) * bow
        md.positions += [x, y0, z, x, y0 + h, z]
        md.normals += [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]
        md.uvs += [u * 2.0, 0.0, u * 2.0, 1.0]
    for i in range(segs):
        a = i * 2
        md.indices += [a, a + 2, a + 1, a + 1, a + 2, a + 3]
    return md


def _add_sky(scene, pbr, mats, parts):
    # 星空：确定性散点半球（y>25）
    stars = []
    for i in range(64):
        az = _hash01(i, 1, 17) * _TAU
        el = math.radians(14.0 + 62.0 * _hash01(i, 2, 19))
        r = 215.0
        x = math.cos(el) * math.cos(az) * r
        y = math.sin(el) * r
        z = math.cos(el) * math.sin(az) * r - 20.0
        if y < 35.0:
            continue
        s = 0.42 + 1.05 * _hash01(i, 3, 23)
        star = sphere(s, 5, 3)
        _place(star, x, y, z)
        stars.append(star)
    star_mesh = Mesh(merge_meshes(stars), pbr, mats['stars'],
                     name='nordic_stars')
    scene.add_mesh(star_mesh, name='stars')
    parts['stars_mesh'] = star_mesh

    moon = Mesh(sphere(7.5, 18, 12), pbr, mats['moon'], name='nordic_moon')
    scene.add_mesh(moon, name='moon', translation=(-98.0, 66.0, -196.0))
    parts['moon'] = moon

    # 极光帘幕 ×4：位次错开，各自色调（材质 aurora_0..3）。
    # 位置高悬（y 66–92）、横向收窄、亮度克制——否则贴地视角下会糊成
    # 一堵横贯画面的青绿"墙"（第一版实测）
    aurora_nodes, aurora_base = [], []
    for k, (wx, wz, wy, w_, h_, bend, bow) in enumerate((
            (-64.0, -138.0, 78.0, 118.0, 32.0, 0.4, 16.0),
            (12.0, -170.0, 94.0, 140.0, 44.0, 2.1, 20.0),
            (90.0, -132.0, 82.0, 104.0, 28.0, 3.9, 12.0),
            (-150.0, -106.0, 70.0, 92.0, 24.0, 5.2, 10.0))):
        md = _aurora_ribbon(w_, h_, bend=bend, bow=bow)
        m = Mesh(md, pbr, mats[f'aurora_{k}'], name=f'nordic_aurora_{k}')
        node = scene.add_mesh(m, name=f'aurora_{k}',
                              translation=(wx, wy, wz))
        aurora_nodes.append(node)
        aurora_base.append((wx, wy, wz))
    parts['aurora'] = aurora_nodes
    parts['aurora_base'] = aurora_base


# --------------------------------------------------------------- 飞龙 --
_DRAGON_SPINE = (
    (0.00, 0.06, 2.78), (0.00, 0.30, 2.40), (0.03, 0.66, 1.82),
    (0.05, 0.84, 1.14), (0.04, 0.78, 0.42), (0.00, 0.66, -0.36),
    (-0.04, 0.74, -1.10), (-0.02, 0.98, -1.86), (0.03, 1.30, -2.60),
    (0.06, 1.62, -3.30), (0.04, 1.88, -3.96), (0.00, 2.02, -4.55),
)
_DRAGON_RADII = (0.085, 0.145, 0.20, 0.25, 0.33, 0.39, 0.37, 0.31,
                 0.245, 0.175, 0.105, 0.035)


def _dragon_body_mesh():
    """低多边形飞龙躯体（局部 +z 朝前）：管扫掠躯干 + 箱式头颚 + 后掠双角。"""
    body = tube(list(_DRAGON_SPINE), list(_DRAGON_RADII), radial=10,
                caps=True, name='dragon_body')
    _uv_scale(body, 3.0, 2.0)
    parts = [body]
    skull = box(0.46, 0.40, 0.56)
    _place(skull, 0.0, 0.30, 2.78)
    parts.append(skull)
    snout = box(0.30, 0.235, 0.68)
    _place(snout, 0.0, 0.235, 3.22)
    parts.append(snout)
    jaw = box(0.28, 0.115, 0.56)
    _place(jaw, 0.0, 0.075, 3.10)
    parts.append(jaw)
    for sx in (-1.0, 1.0):
        horn = lathe([(0.052, 0.0), (0.070, 0.14), (0.028, 0.62),
                      (0.001, 0.86)], segments=6)
        horn.transform(_rot_x(118.0))               # 向 -z 后掠
        _place(horn, sx * 0.155, 0.50, 2.62)
        parts.append(horn)
    return merge_meshes(parts)


def _dragon_wing_mesh():
    """右翼（+x，枢轴在原点）：膜面双面三角扇 + 前缘骨管。"""
    from world3d.engine.geometry import MeshData
    p = ((0.00, 0.00, 0.00), (2.55, 1.30, 0.60), (2.95, 0.80, -0.12),
         (2.25, 0.30, -1.00), (1.18, 0.04, -1.30), (0.25, -0.03, -0.74))
    tris = ((0, 1, 2), (0, 2, 3), (0, 3, 4), (0, 4, 5))
    md = MeshData([], [], [])
    for t in tris:
        for q in (t, tuple(reversed(t))):            # 双面
            for idx in q:
                vx, vy, vz = p[idx]
                md.positions += [vx, vy, vz]
                md.uvs += [vx * 0.33, vz * 0.33]
    md.compute_normals()
    bone = tube([(0.0, 0.0, 0.0), (1.25, 0.62, 0.30), (2.55, 1.30, 0.60)],
                0.075, 6)
    return merge_meshes([md, bone])


def _dragon_fire_mesh():
    """龙焰：沿 +z 的锥（lathe 绕 y 建，再绕 x 转到 +z）。"""
    md = lathe([(0.0, 0.0), (0.34, 0.22), (0.27, 1.15), (0.12, 2.45),
                (0.001, 3.30)], segments=10)
    md.transform(_rot_x(-90.0))                      # +y → +z
    return md


def build_dragon(scene, pbr, mats, parts):
    """龙：根节点（身/头，位姿驱动）+ 眼/双翼/龙焰子节点。"""
    root = scene.add_mesh(Mesh(_dragon_body_mesh(), pbr, mats['dragon'],
                               name='dragon_body'),
                          name='dragon', translation=(40.0, 22.0, -80.0))
    # 双眼（同节点局部坐标：x=±0.24 在头颅两侧）
    eye_l = sphere(0.062, 8, 6)
    _place(eye_l, -0.24, 0.40, 2.98)
    eye_r = sphere(0.062, 8, 6)
    _place(eye_r, 0.24, 0.40, 2.98)
    eye_mesh = Mesh(merge_meshes([eye_l, eye_r]), pbr, mats['eyes'],
                    name='dragon_eyes')
    scene.add_mesh(eye_mesh, name='dragon_eye', parent=root)
    wing_r = Mesh(_dragon_wing_mesh(), pbr, mats['dragon'], name='wing_r')
    node_r = scene.add_mesh(wing_r, name='wing_r', parent=root,
                            translation=(0.30, 0.82, 0.35))
    md2 = _dragon_wing_mesh()
    for i in range(0, len(md2.positions), 3):
        md2.positions[i] = -md2.positions[i]
    md2.compute_normals(force=True)
    wing_l = Mesh(md2, pbr, mats['dragon'], name='wing_l')
    node_l = scene.add_mesh(wing_l, name='wing_l', parent=root,
                            translation=(-0.30, 0.82, 0.35))
    fire = Mesh(_dragon_fire_mesh(), pbr, mats['fire'], name='dragon_fire')
    fire_node = scene.add_mesh(fire, name='dragon_fire', parent=root,
                               translation=(0.0, 0.16, 3.30),
                               scale=(0.02, 0.02, 0.02))
    parts['dragon'] = root
    parts['wing_r'] = node_r
    parts['wing_l'] = node_l
    parts['dragon_fire'] = fire_node
    return root


# ------------------------------------------------------------- 飘雪 --
class Snowfall:
    """CPU 更新的飘雪（billboard 菱形片，position[:] 直写 GPU 缓冲）。

    不做每片单独面向相机：整片雪场共用相机的 right/up（雪都分布在
    前方 30~70m 内，方向差极小），270 片每帧一次批量写入。
    """

    def __init__(self, program, material, count, area, size, speed, seed):
        from world3d.engine.geometry import MeshData
        from world3d.engine.frustum import AABB
        self.area = area
        self.size = size
        self.t = 0.0
        x0, x1, y0, y1, z0, z1 = area
        self.flakes = []
        for i in range(count):
            self.flakes.append([
                x0 + (x1 - x0) * _hash01(i, 1, seed),
                y0 + (y1 - y0) * _hash01(i, 2, seed + 5),
                z0 + (z1 - z0) * _hash01(i, 3, seed + 9),
                speed * (0.72 + 0.56 * _hash01(i, 4, seed + 13)),
                _TAU * _hash01(i, 5, seed + 17)])
        n = count * 6
        self._flat = [0.0] * (n * 3)
        md = MeshData([0.0] * (n * 3), [0.0, 1.0, 0.0] * n, [0.0, 0.0] * n,
                      name='snowfall')
        self.mesh = Mesh(md, program, material, name='snowfall')
        # 剔除包围盒：手动给整场雪的大盒子（初始顶点全 0 会让 AABB 退化）
        corners = [(x, y, z) for x in (x0 - 3, x1 + 3) for y in (y0 - 1, y1 + 1)
                   for z in (z0 - 3, z1 + 3)]
        self.mesh.local_aabb = AABB.from_points(corners)
        self.mesh.world_aabb = self.mesh.local_aabb

    def update(self, dt, cam_pos, cam_fwd):
        self.t += dt
        t = self.t
        fx, fy, fz = cam_fwd
        ln = math.sqrt(fx * fx + fy * fy + fz * fz) or 1.0
        fx, fy, fz = fx / ln, fy / ln, fz / ln
        if abs(fy) > 0.94:                     # 竖直视线退化：换世界轴
            fx, fy, fz = 0.0, 0.0, -1.0
        # right = f × (0,1,0) = (-fz, 0, fx)
        rx, ry, rz = -fz, 0.0, fx
        rl = math.sqrt(rx * rx + rz * rz) or 1.0
        rx, rz = rx / rl, rz / rl
        # up = right × f
        ux = ry * fz - rz * fy
        uy = rz * fx - rx * fz
        uz = rx * fy - ry * fx
        ul = math.sqrt(ux * ux + uy * uy + uz * uz) or 1.0
        ux, uy, uz = ux / ul, uy / ul, uz / ul
        x0, x1, y0, y1, z0, z1 = self.area
        span_y = y1 - y0
        s = self.size
        flat = self._flat
        o = 0
        for fl in self.flakes:
            cx = fl[0] + math.sin(_TAU * t / 15.0 + fl[4]) * 1.35
            cy = y0 + (fl[1] - y0 - fl[3] * t) % span_y
            cz = fl[2] + math.sin(_TAU * t / 20.0 + fl[4] * 1.7) * 0.9
            # 菱形 4 角（billboard）
            ax, ay, az = cx + (rx + ux) * s, cy + uy * s, cz + (rz + uz) * s
            bx, by, bz = cx + (ux - rx) * s, cy + uy * s, cz + (uz - rz) * s
            ex, ey, ez = cx - (rx + ux) * s, cy - uy * s, cz - (rz + uz) * s
            dx, dy, dz = cx + (rx - ux) * s, cy - uy * s, cz + (rz - uz) * s
            for vx, vy, vz in ((ax, ay, az), (bx, by, bz), (ex, ey, ez),
                               (ax, ay, az), (ex, ey, ez), (dx, dy, dz)):
                flat[o] = vx
                flat[o + 1] = vy
                flat[o + 2] = vz
                o += 3
        self.mesh.vlist.position[:] = flat


# ------------------------------------------------------------- 总装 --
def build_nordic_scene(scene, pbr):
    """搭起整场。返回 parts 字典（动态对象句柄 + 材质引用）。"""
    mats = build_materials()
    acc = {}
    _add_ground(acc)
    _add_mountains(acc)
    _add_forest(acc)
    _add_longhouse(acc)
    _add_hut(acc)
    _add_ruins(acc)
    _add_campfire(acc)
    _add_details(acc)

    # 静态几何按材质合并
    for key, mds in acc.items():
        md = merge_meshes(mds)
        scene.add_mesh(Mesh(md, pbr, mats[key], name=f'nordic_{key}'),
                       name=f'nordic_{key}')

    parts = {'mats': mats}
    _add_sky(scene, pbr, mats, parts)

    # 篝火火焰（独立：跳动缩放）
    x0, z0 = CAMPFIRE
    y = snow_height(x0, z0)
    flame = Mesh(lathe([(0.0, 0.0), (0.50, 0.26), (0.36, 1.02),
                        (0.16, 1.62), (0.001, 2.05)], segments=9),
                 pbr, mats['fire'], name='campfire_flame')
    parts['campfire'] = scene.add_mesh(flame, name='campfire',
                                       translation=(x0, y + 0.24, z0))

    # 符文光柱（点亮时从墙顶升起）
    beam_geo = box(0.62, 9.0, 0.62)
    beam_geo.transform(_trans(0.0, 4.5, 0.0))
    beam = Mesh(beam_geo, pbr, mats['beam'], name='rune_beam')
    parts['beam'] = scene.add_mesh(beam, name='rune_beam',
                                   translation=BEAM_POS,
                                   scale=(1.0, 0.02, 1.0))

    build_dragon(scene, pbr, mats, parts)

    # 飘雪三组（近/中/远）：雪花自发光一点，夜里也看得见
    snow_c = material_from('snow', size=64, name='mat_flake')
    snow_c.base_color = (0.85, 0.90, 1.00)
    snow_c.emissive = (0.40, 0.44, 0.54)
    snow_c.emissive_tex = WHITE_TEX()
    snow_c.roughness = 1.0
    fields = [
        Snowfall(pbr, snow_c, 60, (-16.0, 16.0, 0.0, 9.0, 14.0, -30.0),
                 0.095, 1.75, 101),
        Snowfall(pbr, snow_c, 90, (-30.0, 30.0, 0.0, 12.0, 0.0, -46.0),
                 0.072, 1.45, 202),
        Snowfall(pbr, snow_c, 110, (-48.0, 48.0, 2.0, 16.0, -10.0, -74.0),
                 0.052, 1.25, 303),
    ]
    for i, f in enumerate(fields):
        scene.add_mesh(f.mesh, name=f'snowfall_{i}')
    parts['snowfall'] = fields
    return parts

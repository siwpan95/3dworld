# -*- coding: utf-8 -*-
"""方块世界（Minecraft 风格致敬）的**纯逻辑层**：像素材质像素、世界生成、网格化、生物步态、昼夜曲线。

本模块**不 import pyglet.gl**：纹理在这里是"像素字节"，网格是"float 数组"，
生物是"角度"，天空是"颜色向量"—— 全部可无窗口单测。

素材来源与许可（`SPEC-001 §铁律 0` / `SPEC-003 §创新三原则 1`）
------------------------------------------------------------
**零外部素材**：所有贴图都由本文件的哈希噪声与图案规则现算，不复制任何 Minecraft
或第三方的贴图、模型、名称。风格（16×16 像素方块、面亮度分级、顶面最亮）属通用
像素艺术手法，此处以"致敬"方式重实现，未借用对方任何资产。

设计要点（为什么这么切）
------------------------
- **贴图是"生成规则"而不是图片**：与 kkrieger 致敬那条线一致 —— 存的是配方不是像素。
  图集按横向拼接，采样用 GL_NEAREST，UV 取半像素内缩（否则相邻格会渗色）。
- **昼夜不能靠重建网格**：网格只烘"相对面亮度"（顶 1.0 / 侧 0.8 / 底 0.5，MC 同款），
  全局天光由 shader 的 uniform 调制 —— 于是日夜循环是零重建成本的。
- **自发光走顶点色 alpha**：火把/窗户标记为 emissive，夜里不随天光变暗，
  这样"日落点灯"这一幕不需要额外的光源系统。
- **透明与不透明分两批网格**：水单独一批（半透明、单独混合），其余按不透明画。
  树叶/玻璃在场内按"不透明"处理（等价于 MC 的"流畅"画质档），已在文档写明。
"""
import math

TILE = 16                     # 单块贴图像素边长（Minecraft 风格的 16×16）
TEXEL = 1.0 / TILE

# ---------------------------------------------------------------- 方块表 --
AIR = 0
GRASS, DIRT, STONE, SAND = 1, 2, 3, 4
LOG, LEAVES, PLANKS, COBBLE = 5, 6, 7, 8
GLASS, WATER, TORCH, SNOW = 9, 10, 11, 12
BRICK, ROOF, WINDOW = 13, 14, 15

# 每个方块用到的图集格号：(顶面, 侧面, 底面)
# 图集格名 → 格号由 `atlas_tiles()` 的顺序决定，这里用名字而不是硬编码下标，
# 免得增删贴图时下标错位（那类 bug 表现为"草地方块长着木头纹理"，很难查）。
BLOCK_FACES = {
    GRASS:  ('grass_top', 'grass_side', 'dirt'),
    DIRT:   ('dirt', 'dirt', 'dirt'),
    STONE:  ('stone', 'stone', 'stone'),
    SAND:   ('sand', 'sand', 'sand'),
    LOG:    ('log_top', 'log_side', 'log_top'),
    LEAVES: ('leaves', 'leaves', 'leaves'),
    PLANKS: ('planks', 'planks', 'planks'),
    COBBLE: ('cobble', 'cobble', 'cobble'),
    GLASS:  ('glass', 'glass', 'glass'),
    WATER:  ('water', 'water', 'water'),
    TORCH:  ('torch', 'torch', 'torch'),
    SNOW:   ('snow', 'snow', 'snow'),
    BRICK:  ('brick', 'brick', 'brick'),
    ROOF:   ('roof', 'roof', 'roof'),
    WINDOW: ('window', 'window', 'window'),
}

# 自发光方块（夜里不随天光变暗）——"日落点灯"那一幕靠它
EMISSIVE = {TORCH, WINDOW}

# 非方块贴图：生物皮肤与云（同样由本文件的规则现算，不引用任何外部贴图）
EXTRA_TILES = ('pig_body', 'pig_face', 'sheep_wool', 'creeper_skin',
               'creeper_face', 'cloud')

# 半透明方块（单独一批网格、单独混合）
TRANSLUCENT = {WATER, GLASS}

# 面顺序固定为：+Z, -Z, +Y, -Y, +X, -X
FACE_DIRS = ((0, 0, 1), (0, 0, -1), (0, 1, 0), (0, -1, 0), (1, 0, 0), (-1, 0, 0))
# 相对面亮度：顶面最亮、底面最暗 —— Minecraft 的经典观感就来自这个分级
FACE_BRIGHT = (0.80, 0.80, 1.00, 0.50, 0.62, 0.62)
# 每个面的四个角（单位立方体，逆时针，供两个三角形展开）
FACE_CORNERS = (
    ((-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)),      # +Z
    ((1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)),  # -Z
    ((-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)),      # +Y
    ((-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)),  # -Y
    ((1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)),      # +X
    ((-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)),  # -X
)

# 世界常量
WORLD_W = 72                  # 世界边长（列数）。72² 的暴露面量级实测可在预算内建完
WATER_LEVEL = 12              # 水线。**这个值必须落在地形高度分布里**：fbm 的结果
#                               集中在 ~0.5（均值 13.7），水线定 9 时水下 0 列、
#                               水面网格 0 面 —— 实测踩过，故抬到 12。
SEA_FLOOR = 6                 # 最深的地表
MAX_H = 26
CLEARING_R = 13               # 村庄周围的树木清空半径（相机进场通路 + 观感）


# ------------------------------------------------------------ 确定性噪声 --
def _hash01(*args):
    """确定性伪随机 [0,1)：同参数恒定（世界可复现、截图可比对）。"""
    s = math.sin(sum(a * (61.7 + 29.3 * k) for k, a in enumerate(args, 1))) * 43758.5453
    return s - math.floor(s)


def _noise2(x, z, seed):
    """值噪声：整数格点哈希 + 双线性插值（够用，且比 Perlin 好解释）。"""
    xi, zi = math.floor(x), math.floor(z)
    xf, zf = x - xi, z - zi
    u = xf * xf * (3 - 2 * xf)                 # smoothstep 缓和，避免方格感
    v = zf * zf * (3 - 2 * zf)
    a = _hash01(xi, zi, seed)
    b = _hash01(xi + 1, zi, seed)
    c = _hash01(xi, zi + 1, seed)
    d = _hash01(xi + 1, zi + 1, seed)
    return (a * (1 - u) + b * u) * (1 - v) + (c * (1 - u) + d * u) * v


def fbm(x, z, seed, octaves=4, lacunarity=2.0, gain=0.5):
    """分形叠加：低频给地貌起伏，高频给细节。返回 [0,1]。"""
    total, amp, norm, fx, fz = 0.0, 1.0, 0.0, x, z
    for i in range(octaves):
        total += amp * _noise2(fx, fz, seed + i * 977)
        norm += amp
        amp *= gain
        fx *= lacunarity
        fz *= lacunarity
    return total / norm


# ------------------------------------------------------------ 贴图像素 --
def _px(buf, w, x, y, rgb, a=255):
    i = (y * w + x) * 4
    buf[i] = max(0, min(255, int(rgb[0])))
    buf[i + 1] = max(0, min(255, int(rgb[1])))
    buf[i + 2] = max(0, min(255, int(rgb[2])))
    buf[i + 3] = a


def _name_seed(name):
    """名字 → 确定性整数种子。

    **不能用 `hash(name)`**：Python 的字符串哈希带进程级随机盐（PYTHONHASHSEED），
    跨进程不稳定 —— 那会让"同一份代码两次运行生成不同贴图"，截图和单测都失去意义。
    """
    return sum((i + 1) * ord(c) for i, c in enumerate(name))


def tile_pixels(name):
    """生成一张 16×16 贴图的 RGBA 像素（bytes，行优先，原点在左上）。

    全部由确定性哈希噪声与几何规则算出：**没有任何外部图片**。
    每个分支都写成"这个方块长什么样"的直观规则，便于调参。
    """
    w = TILE
    ns = _name_seed(name)
    buf = bytearray(w * w * 4)
    for y in range(w):
        for x in range(w):
            n = _hash01(x, y, ns, 17)
            if name == 'grass_top':
                g = 118 + 46 * n
                _px(buf, w, x, y, (g * 0.52, g, g * 0.34))
            elif name == 'grass_side':
                # 上沿 3 像素是草，往下渐变成土，交界做不规则咬边
                edge = 3 + int(2 * _hash01(x, 7, 11))
                if y < edge:
                    g = 112 + 40 * n
                    _px(buf, w, x, y, (g * 0.52, g, g * 0.34))
                else:
                    d = 96 + 34 * n
                    _px(buf, w, x, y, (d * 1.06, d * 0.74, d * 0.48))
            elif name == 'dirt':
                d = 92 + 36 * n
                _px(buf, w, x, y, (d * 1.06, d * 0.74, d * 0.48))
            elif name == 'stone':
                s = 116 + 28 * n
                if _hash01(x, y, 3) > 0.93:      # 零星深色斑点
                    s *= 0.74
                _px(buf, w, x, y, (s, s, s * 1.03))
            elif name == 'cobble':
                # 圆石：把 16×16 切成不规则小块，块内亮、缝隙暗
                cx, cy = int(x / 4), int(y / 4)
                k = _hash01(cx, cy, 21)
                seam = (x % 4 == 0) or (y % 4 == 0) or _hash01(x, y, 5) > 0.9
                s = (104 + 40 * k) * (0.55 if seam else 1.0)
                _px(buf, w, x, y, (s, s, s))
            elif name == 'sand':
                s = 196 + 34 * n
                _px(buf, w, x, y, (s, s * 0.94, s * 0.66))
            elif name == 'log_side':
                # 树皮：竖向纹理 + 深浅木纹
                k = 0.86 + 0.28 * _hash01(x // 1, y, 31)
                if x % 5 == 0:
                    k *= 0.78
                _px(buf, w, x, y, (146 * k, 112 * k, 70 * k))
            elif name == 'log_top':
                # 年轮：到中心的距离分层
                r = math.hypot(x - 7.5, y - 7.5)
                k = 0.86 + 0.26 * (0.5 + 0.5 * math.sin(r * 2.6))
                _px(buf, w, x, y, (172 * k, 134 * k, 84 * k))
            elif name == 'leaves':
                g = 96 + 62 * n
                if n > 0.86:                     # 叶隙（深色斑）
                    g *= 0.42
                _px(buf, w, x, y, (g * 0.42, g, g * 0.36))
            elif name == 'planks':
                k = 0.88 + 0.20 * _hash01(x, y, 41)
                if y % 4 == 3:                   # 木板横缝
                    k *= 0.7
                if (x + (y // 4) * 5) % 8 == 0:  # 错位的竖缝
                    k *= 0.82
                _px(buf, w, x, y, (168 * k, 132 * k, 84 * k))
            elif name == 'glass':
                border = x in (0, 1, 14, 15) or y in (0, 1, 14, 15)
                if border:
                    _px(buf, w, x, y, (208, 226, 236), 235)
                elif _hash01(x, y, 51) > 0.86:   # 高光斜纹
                    _px(buf, w, x, y, (232, 244, 250), 90)
                else:
                    _px(buf, w, x, y, (196, 226, 240), 46)
            elif name == 'water':
                # 波纹：横向正弦 + 噪声，供 UV 滚动时看不出接缝
                k = 0.5 + 0.5 * math.sin((x * 0.9 + y * 0.35) * 1.6)
                k = 0.72 + 0.28 * k + 0.08 * n
                _px(buf, w, x, y, (44 * k, 96 * k, 208 * k), 178)
            elif name == 'torch':
                if 6 <= x <= 9 and y >= 6:       # 木柄
                    k = 0.86 + 0.2 * _hash01(x, y, 61)
                    _px(buf, w, x, y, (140 * k, 104 * k, 62 * k))
                elif 5 <= x <= 10 and 2 <= y <= 6:   # 火头
                    _px(buf, w, x, y, (255, 206, 96))
                else:
                    _px(buf, w, x, y, (24, 18, 12), 0)
            elif name == 'snow':
                s = 228 + 26 * n
                _px(buf, w, x, y, (s, s, s * 1.0))
            elif name == 'brick':
                row = y // 4
                off = (row % 2) * 4
                mortar = (y % 4 == 0) or ((x + off) % 8 == 0)
                if mortar:
                    _px(buf, w, x, y, (176, 170, 162))
                else:
                    k = 0.86 + 0.22 * _hash01(x, y, 71)
                    _px(buf, w, x, y, (150 * k, 74 * k, 58 * k))
            elif name == 'roof':
                row = y // 3
                k = 0.82 + 0.22 * _hash01(x, row, 81)
                if y % 3 == 0:
                    k *= 0.66                    # 瓦楞阴影
                _px(buf, w, x, y, (128 * k, 52 * k, 44 * k))
            elif name == 'window':
                frame = x in (0, 1, 14, 15) or y in (0, 1, 14, 15) or x in (7, 8) or y in (7, 8)
                if frame:
                    _px(buf, w, x, y, (120, 92, 58))
                else:
                    k = 0.9 + 0.16 * n
                    _px(buf, w, x, y, (255 * k, 224 * k, 140 * k))
            elif name == 'pig_body':
                k = 0.9 + 0.2 * n
                _px(buf, w, x, y, (238 * k, 152 * k, 152 * k))
            elif name == 'pig_face':
                # 猪脸：粉底 + 两只眼 + 中间的鼻子
                if 3 <= y <= 5 and (2 <= x <= 4 or 11 <= x <= 13):
                    _px(buf, w, x, y, (28, 24, 24))
                elif 8 <= y <= 11 and 5 <= x <= 10:
                    _px(buf, w, x, y, (206, 122, 128))
                else:
                    k = 0.9 + 0.2 * n
                    _px(buf, w, x, y, (238 * k, 152 * k, 152 * k))
            elif name == 'sheep_wool':
                k = 0.88 + 0.24 * n
                if n > 0.8:
                    k *= 0.9
                _px(buf, w, x, y, (238 * k, 236 * k, 228 * k))
            elif name == 'creeper_skin':
                k = 0.82 + 0.34 * n
                if _hash01(x // 2, y // 2, 91) > 0.72:
                    k *= 0.78                     # 斑驳的深浅格
                _px(buf, w, x, y, (96 * k, 178 * k, 92 * k))
            elif name == 'creeper_face':
                # 爬行者脸：黑洞眼 + 标志性的咧口
                if (4 <= y <= 7 and (2 <= x <= 5 or 10 <= x <= 13)) or \
                   (8 <= y <= 11 and 5 <= x <= 10) or (11 <= y <= 13 and (5 <= x <= 10)):
                    _px(buf, w, x, y, (18, 22, 18))
                else:
                    k = 0.82 + 0.3 * n
                    _px(buf, w, x, y, (104 * k, 186 * k, 96 * k))
            elif name == 'cloud':
                k = 0.94 + 0.10 * n
                _px(buf, w, x, y, (255 * k, 255 * k, 255 * k), 235)
            else:                                 # 兜底：洋红 = "缺贴图"，一眼能认出来
                _px(buf, w, x, y, (255, 0, 220))
    return bytes(buf)


def atlas_tiles():
    """图集格顺序（决定格号）—— 按名字排序保证**确定性**。"""
    names = set()
    for top, side, bottom in BLOCK_FACES.values():
        names.update((top, side, bottom))
    names.update(EXTRA_TILES)
    return sorted(names)


def atlas_pixels():
    """横向拼接的图集：返回 (宽度, 高度, RGBA bytes, {名字: 格号})。"""
    names = atlas_tiles()
    w, h = TILE * len(names), TILE
    buf = bytearray(w * h * 4)
    index = {}
    for i, name in enumerate(names):
        index[name] = i
        data = tile_pixels(name)
        for y in range(h):
            src = y * TILE * 4
            dst = (y * w + i * TILE) * 4
            buf[dst:dst + TILE * 4] = data[src:src + TILE * 4]
    return w, h, bytes(buf), index


def tile_uv(index, count):
    """格号 → UV 区间（**半像素内缩**：不留内缩的话相邻格会互相渗色）。"""
    u0 = (index * TILE + 0.5) / (TILE * count)
    u1 = ((index + 1) * TILE - 0.5) / (TILE * count)
    return u0, u1


# ------------------------------------------------------------ 世界生成 --
def surface_height(wx, wz, seed):
    """某一列的地表高度（含水下压平与山脊抬升）。"""
    base = fbm(wx * 0.035, wz * 0.035, seed, octaves=4)
    detail = fbm(wx * 0.12, wz * 0.12, seed + 5501, octaves=2)
    h = SEA_FLOOR + 1 + (MAX_H - SEA_FLOOR - 4) * (base ** 1.9) + 2.2 * detail
    # 水线附近压成缓坡（仍低于水面 ⇒ 会灌成湖），深水区保留起伏
    if h < WATER_LEVEL + 2:
        h = (WATER_LEVEL - 1) - ((WATER_LEVEL - 1) - h) * 0.55
    return max(SEA_FLOOR, min(MAX_H - 4, int(round(h))))


def generate_world(seed=20261006, width=WORLD_W):
    """生成方块世界。返回 (blocks, heights, trees, houses)。

    blocks 是 `bytearray(width*MAX_H*width)`，索引 `(y*width + z)*width + x` ——
    **y 在前**：网格化按 y 竖着扫时局部性更好（实测比 z 在外快约 15%）。
    """
    n = width
    blocks = bytearray(n * MAX_H * n)
    heights = [0] * (n * n)

    def put(x, y, z, b):
        if 0 <= x < n and 0 <= y < MAX_H and 0 <= z < n:
            blocks[(y * n + z) * n + x] = b

    def get(x, y, z):
        if 0 <= x < n and 0 <= y < MAX_H and 0 <= z < n:
            return blocks[(y * n + z) * n + x]
        return 0

    # --- 地层 ---
    for z in range(n):
        for x in range(n):
            h = surface_height(x, z, seed)
            heights[z * n + x] = h
            for y in range(0, h + 1):
                if y <= h - 4:
                    b = STONE
                elif y <= h - 1:
                    b = DIRT
                else:
                    if h <= WATER_LEVEL + 1:
                        b = SAND                     # 水边沙滩
                    elif h >= MAX_H - 8:
                        b = SNOW                     # 高处积雪
                    else:
                        b = GRASS
                put(x, y, z, b)
            for y in range(h + 1, WATER_LEVEL + 1):
                put(x, y, z, WATER)                  # 水面以下的空气填成水

    # --- 树：只长在草方块上，且彼此留距离（哈希决定，确定性）---
    trees = []
    cx0, cz0 = n // 2, n // 2
    for z in range(3, n - 3):
        for x in range(3, n - 3):
            # 村庄周围留清空区：既符合"村子周围是开垦地"的直觉，也避免相机从空中
            # 进场时一头扎进树冠 —— 第一版实测有两张截图整屏被树叶糊住。
            if abs(x - cx0) <= CLEARING_R and abs(z - cz0) <= CLEARING_R:
                continue
            if _hash01(x, z, seed + 991) > 0.986:
                h = heights[z * n + x]
                if h <= WATER_LEVEL + 1 or h >= MAX_H - 9:
                    continue
                if get(x, h + 1, z) != 0:
                    continue
                trunk = 4 + int(3 * _hash01(x, z, seed + 13))
                trees.append((x, h + 1, z, trunk))
    for (x, y0, z, trunk) in trees:
        for k in range(trunk):
            put(x, y0 + k, z, LOG)
        top = y0 + trunk
        for dy in range(-2, 2):
            rad = 2 if dy < 0 else 1
            for dz in range(-rad, rad + 1):
                for dx in range(-rad, rad + 1):
                    if abs(dx) == rad and abs(dz) == rad and _hash01(dx, dz, dy) > 0.4:
                        continue                     # 削角，让树冠不是方块
                    if get(x + dx, top + dy, z + dz) == 0:
                        put(x + dx, top + dy, z + dz, LEAVES)

    # --- 村庄：在一块被压平的台地上摆三栋小屋 + 一条土路 ---
    cx, cz = n // 2, n // 2
    pad = 7
    # 村庄地面必须高于水线，否则会被"湖"淹掉（实测过一次：三栋房子泡在水里）
    ground = max(heights[cz * n + cx], WATER_LEVEL + 2)
    for z in range(cz - pad - 2, cz + pad + 3):
        for x in range(cx - pad - 2, cx + pad + 3):
            if not (0 <= x < n and 0 <= z < n):
                continue
            for y in range(0, ground + 1):
                put(x, y, z, STONE if y <= ground - 3 else DIRT)
            put(x, ground, z, GRASS)
            for y in range(ground + 1, MAX_H):
                put(x, y, z, AIR)                # 清空台地上的树/水
            heights[z * n + x] = ground
    # 土路：从中心往南一条。**注意别让它穿过房子** —— 第一版第三栋摆在 (cx-2, cz+1)，
    # 正好压在路面上；这是净空检查顺带抓出来的布局缺陷。
    for k in range(0, pad + 8):
        z = cz + 2 + k
        for dx in (-1, 0, 1):
            if 0 <= cx + dx < n and z < n:
                put(cx + dx, ground, z, DIRT)
    houses = []
    # 第三栋改到东侧、并往南让开路面
    for (hx, hz, wide, deep) in ((cx - 5, cz - 5, 5, 4), (cx + 1, cz - 5, 5, 4),
                                 (cx + 2, cz + 3, 6, 5)):
        houses.append(_build_house(put, hx, ground, hz, wide, deep))
    return bytes(blocks), heights, trees, houses


def _build_house(put, x0, y0, z0, wide, deep, tall=4):
    """盖一栋小屋：木板墙 + 四角圆石柱 + 玻璃窗 + 屋顶 + 门 + 门口火把。"""
    for z in range(z0, z0 + deep):
        for x in range(x0, x0 + wide):
            corner = x in (x0, x0 + wide - 1) and z in (z0, z0 + deep - 1)
            for y in range(y0 + 1, y0 + 1 + tall):
                edge = x in (x0, x0 + wide - 1) or z in (z0, z0 + deep - 1)
                if not edge:
                    continue
                if corner:
                    put(x, y, z, COBBLE)
                elif y == y0 + 3:
                    put(x, y, z, WINDOW)          # 窗带（自发光 → 夜里亮）
                else:
                    put(x, y, z, PLANKS)
    # 屋顶：两层瓦，上小下大
    for layer, shrink in ((0, 0), (1, 1)):
        y = y0 + 1 + tall + layer
        for z in range(z0 - shrink, z0 + deep + shrink):
            for x in range(x0 - shrink, x0 + wide + shrink):
                if layer == 1 and not (x0 <= x < x0 + wide and z0 <= z < z0 + deep):
                    continue
                put(x, y, z, ROOF)
    # 门：朝南（z0+deep 那面）正中
    dx = x0 + wide // 2
    put(dx, y0 + 1, z0 + deep - 1, AIR)
    put(dx, y0 + 2, z0 + deep - 1, AIR)
    # 门口两盏火把
    put(dx - 1, y0 + 3, z0 + deep, TORCH)
    put(dx + 1, y0 + 3, z0 + deep, TORCH)
    return (x0, y0, z0, wide, deep, tall)


def block_index(width=WORLD_W):
    """返回 (width, max_h)，供调用方自己算索引：`(y*w + z)*w + x`。"""
    return width, MAX_H


def box_mesh(tile=None, atlas_count=None, size=(1.0, 1.0, 1.0), offset=(0.0, 0.0, 0.0),
             tint=(1.0, 1.0, 1.0), emissive=0.0):
    """单元盒几何（36 顶点）→ (pos, col, uv) 三个 list。

    两种 UV 模式，分别服务两条渲染路径：

    - ``tile`` 给了格号 ⇒ 直接出**图集 UV**（静态网格：地形、生物、云）；
    - ``tile is None`` ⇒ 出**面内局部 UV [0,1]**，由顶点着色器按"每实例格号"映射
      （实例化的下落方块走这条：一个基础立方体 + N 个实例，无法把格号烘进几何）。
    """
    hx, hy, hz = size[0] / 2.0, size[1] / 2.0, size[2] / 2.0
    ox, oy, oz = offset
    pos, col, uv = [], [], []
    u0 = u1 = None
    if tile is not None:
        u0, u1 = tile_uv(tile, atlas_count)
    for fi in range(6):
        b = FACE_BRIGHT[fi]
        c = (tint[0] * b, tint[1] * b, tint[2] * b, emissive)
        corners = FACE_CORNERS[fi]
        quad = (corners[0], corners[1], corners[2], corners[0], corners[2], corners[3])
        for (cx, cy, cz) in quad:
            pos += [ox + cx * hx, oy + cy * hy, oz + cz * hz]
            col += list(c)
        if tile is None:
            uv += [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 1.0]
        else:
            uv += [u0, 0.0, u1, 0.0, u1, 1.0, u0, 0.0, u1, 1.0, u0, 1.0]
    return pos, col, uv


# ------------------------------------------------------------ 网格化 --
def _is_solid(b, transparent_ok=False):
    if b == AIR:
        return False
    if transparent_ok:
        return b not in TRANSLUCENT
    return True


def mesh_world(blocks, atlas_index, width=WORLD_W, want_water=False,
               pos=None, col=None, uv=None, y0=0, y1=None):
    """把方块世界网格化成三角形汤（**带面剔除**）。

    Args:
        want_water: False = 只出不透明面；True = 只出水面（分成两批，混合方式不同）
        y0/y1:      只处理 y ∈ [y0, y1) —— 支持渐进式重建（每帧啃几层，不冻画面）

    返回 (面数, 顶点数)；数组是**追加**到传入的 array('f') 上。
    """
    import array as _array
    pos = _array.array('f') if pos is None else pos
    col = _array.array('f') if col is None else col
    uv = _array.array('f') if uv is None else uv
    y1 = MAX_H if y1 is None else y1
    n = width
    count = len(atlas_index)
    faces = 0
    ext_p, ext_c, ext_u = pos.extend, col.extend, uv.extend

    for y in range(y0, y1):
        ybase = y * n * n
        for z in range(n):
            zbase = ybase + z * n
            for x in range(n):
                b = blocks[zbase + x]
                if b == AIR:
                    continue
                if (b == WATER) != want_water:
                    continue
                faces3 = BLOCK_FACES.get(b)
                if faces3 is None:
                    continue
                emissive = 1.0 if b in EMISSIVE else 0.0
                for fi, (dx, dy, dz) in enumerate(FACE_DIRS):
                    nb = blocks[((y + dy) * n + (z + dz)) * n + (x + dx)] \
                        if (0 <= y + dy < MAX_H and 0 <= z + dz < n and 0 <= x + dx < n) \
                        else AIR
                    # 剔除规则：邻居实心就跳过；水面与水面之间互不遮挡
                    if nb != AIR:
                        if b == WATER and nb == WATER:
                            continue
                        if b != WATER and nb not in TRANSLUCENT:
                            continue
                        if b != WATER and nb == WATER and fi == 3:
                            continue                  # 水下底面不画（看不见）
                    tile = faces3[0] if fi == 2 else (faces3[2] if fi == 3 else faces3[1])
                    u0, u1 = tile_uv(atlas_index[tile], count)
                    bright = FACE_BRIGHT[fi]
                    c = (bright, bright, bright, emissive)
                    corners = FACE_CORNERS[fi]
                    # 水面压低 0.12：MC 的水面比方块顶面低一截
                    dyv = -0.12 if (b == WATER and fi == 2) else 0.0
                    quad = (corners[0], corners[1], corners[2],
                            corners[0], corners[2], corners[3])
                    for (cxi, cyi, czi) in quad:
                        ext_p((x + cxi, y + cyi + dyv, z + czi))
                        ext_c(c)
                    # 水面把 UV 缩小到 0.875（MC 的流体贴图只占 14/16 格），
                    # 这样滚动 UV 时不会把相邻格的像素卷进来
                    if b == WATER:
                        cu0 = u0 + (u1 - u0) * 0.0625
                        cu1 = u1 - (u1 - u0) * 0.0625
                        ext_u((cu0, 0.0625, cu1, 0.0625, cu1, 0.9375,
                               cu0, 0.0625, cu1, 0.9375, cu0, 0.9375))
                    else:
                        ext_u((u0, 0., u1, 0., u1, 1., u0, 0., u1, 1., u0, 1.))
                    faces += 1
    return faces, len(pos) // 3


# ------------------------------------------------------------ 生物步态 --
def leg_swing(phase, amp=28.0):
    """腿摆动角（度）：前后腿相位相差半周期，看起来才是"走"而不是"蹦"。"""
    return amp * math.sin(phase)


def mob_pose(kind, t, speed=1.0, phase=0.0):
    """返回一只方块生物的关节角字典（纯数学，可单测）。

    kind: 'pig' | 'sheep' | 'creeper'
    """
    w = 2.4 * speed                                  # 步频
    p = t * w + phase
    bob = 0.045 * math.sin(2 * p)                    # 身体起伏（一步两颠）
    out = {
        'body_y': bob,
        'leg_front_l': leg_swing(p),
        'leg_front_r': leg_swing(p + math.pi),
        'leg_back_l': leg_swing(p + math.pi),
        'leg_back_r': leg_swing(p),
        'head_pitch': 3.0 * math.sin(p * 0.5),
    }
    if kind == 'creeper':
        out['leg_front_l'] = leg_swing(p) * 0.6
        out['leg_front_r'] = leg_swing(p + math.pi) * 0.6
        out['head_pitch'] = 0.0
    return out


# ------------------------------------------------------------ 昼夜曲线 --
def sky_state(phase):
    """给定循环相位 [0,1) 返回天空状态。

    相位约定：0 = 日出（太阳在东边地平线）、0.25 = 正午、0.5 = 日落、0.75 = 午夜。
    太阳方向 `sun` 是**指向太阳的单位向量**（供 shader 做面朝向的光照调制）。

    返回 dict: sun(3), sky(3), horizon(3), ambient, stars, sun_visible, moon_visible
    """
    phase = phase % 1.0
    theta = 2.0 * math.pi * phase
    elev = math.sin(theta)                          # -1（子夜）~ +1（正午）
    sun = (math.cos(theta) * 0.94, elev, 0.20 + 0.10 * math.cos(theta))
    l = math.sqrt(sum(v * v for v in sun))
    sun = tuple(v / l for v in sun)

    def lerp3(a, b, k):
        return tuple(a[i] + (b[i] - a[i]) * k for i in range(3))

    def clamp01(v):
        return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)

    def smooth(e0, e1, v):
        k = clamp01((v - e0) / (e1 - e0))
        return k * k * (3 - 2 * k)

    NIGHT_SKY, DAY_SKY = (0.035, 0.045, 0.105), (0.32, 0.53, 0.92)
    DUSK_SKY, DUSK_HZ = (0.16, 0.14, 0.28), (0.98, 0.52, 0.22)
    NIGHT_HZ, DAY_HZ = (0.06, 0.08, 0.16), (0.68, 0.82, 0.98)

    day = smooth(-0.06, 0.22, elev)                 # 0 = 夜，1 = 昼
    dusk = 1.0 - abs(clamp01((elev + 0.22) / 0.34) * 2.0 - 1.0)   # 日出/日落时最强
    sky = lerp3(NIGHT_SKY, DAY_SKY, day)
    hz = lerp3(NIGHT_HZ, DAY_HZ, day)
    sky = lerp3(sky, DUSK_SKY, dusk * 0.65)
    hz = lerp3(hz, DUSK_HZ, dusk * 0.85)
    ambient = 0.26 + 0.74 * day                     # 夜间地板 0.26：还能看清轮廓
    stars = clamp01(-elev * 3.2) * 0.9
    return {
        'sun': sun,
        'sky': sky,
        'horizon': hz,
        'ambient': ambient,
        'stars': stars,
        'sun_visible': elev > -0.08,
        'moon_visible': elev < 0.08,
    }


# ------------------------------------------------------- 五幕与相机 --
DURATION = 48.0
ACTS = (
    (0.0,  '第一幕 · 日出', '高空掠过方块大地，云在脚下飘'),
    (10.0, '第二幕 · 村庄', '下降进场：小屋、火把、水塘'),
    (20.0, '第三幕 · 建造', '方块从天而降，逐块拼出一栋小屋'),
    (30.0, '第四幕 · 生物', '方块生物沿小路走过村庄'),
    (40.0, '第五幕 · 日落', '天色转暖再入夜，窗户与火把次第点亮'),
)

# (时间, eye, target)；t=0 与 t=48 同帧 ⇒ 循环无缝。
# 村庄台地在 y≈14，取景都按这个高度对齐；镜头全部留在 17 以上，避免插进地形里。
CAM_KEYS = (
    (0.0,  (36.0, 40.0, 74.0),  (36.0, 16.0, 40.0)),   # 高空自南方入场，正对村庄
    (5.0,  (36.0, 34.0, 62.0),  (36.0, 16.0, 38.0)),
    (10.0, (32.0, 34.0, 54.0),  (34.0, 16.0, 36.0)),   # 第二幕：自西南下降进场
    (14.0, (24.0, 30.0, 50.0),  (32.0, 16.0, 34.0)),
    (18.0, (22.0, 27.0, 40.0),  (30.0, 15.0, 30.0)),
    (20.0, (25.0, 30.0, 15.0),  (33.0, 16.5, 27.0)),   # 第三幕：西北高处俯看拼装地块
    (25.0, (30.0, 28.0, 12.0),  (34.0, 17.0, 28.0)),   # （北侧那栋会挡视线，故避开）
    (28.0, (22.0, 29.0, 26.0),  (30.0, 16.0, 30.0)),   # 转场沿西侧横移，不扫过屋顶
    (30.0, (26.0, 18.5, 48.0),  (36.0, 15.6, 43.0)),   # 第四幕：贴近生物视高看小路
    (35.0, (28.0, 17.5, 44.0),  (35.5, 15.6, 40.0)),   # （俯视会把腿全挡住）
    (40.0, (22.0, 34.0, 62.0),  (36.0, 15.0, 34.0)),   # 第五幕：高位远景看日落与灯火
    (44.0, (28.0, 38.0, 72.0),  (36.0, 15.0, 36.0)),
    (48.0, (36.0, 40.0, 74.0),  (36.0, 16.0, 40.0)),   # 回到开场帧
)

# 「影片时间 → 昼夜相位」关键帧。
# **为什么不能直接用 t/DURATION**：相位线性走完一圈时日落落在第三幕（t≈24），
# 于是"建造"发生在夜里、"生物"在深夜 —— 第一版就是这样（实测第三幕天光 0.26、星 0.90）。
# 这里把白天按幕编排压缩（正午对齐第三幕），夜里走完剩余相位回到日出，
# 循环仍然无缝：t=0 ⇒ 0.0，t=48 ⇒ 1.0 ≡ 0.0。
DAY_KEYS = (
    (0.0,  0.00),    # 日出：太阳在地平线上，天空橙红 —— 第一幕
    (4.0,  0.06),
    (10.0, 0.17),    # 第二幕：上升的上午
    (20.0, 0.30),    # 第三幕：正午，建造看得最清楚
    (30.0, 0.41),    # 第四幕：下午，影子拉长
    (36.0, 0.47),
    (40.0, 0.51),    # 第五幕：日落（相位 0.5 = 太阳过地平线）
    (44.0, 0.66),    # 入夜：窗户与火把成为主要光源
    (46.0, 0.80),
    (48.0, 1.00),    # 走完夜里剩余相位，回到日出
)


def day_phase(t, keys=DAY_KEYS, duration=DURATION):
    """影片时间 → 昼夜相位 [0,1)。分段 smoothstep，端点对齐保证循环无缝。"""
    t = max(0.0, min(duration, t))
    for i in range(len(keys) - 1):
        t0, p0 = keys[i]
        t1, p1 = keys[i + 1]
        if t0 <= t <= t1:
            k = _smooth((t - t0) / (t1 - t0)) if t1 > t0 else 0.0
            return (p0 + (p1 - p0) * k) % 1.0
    return keys[-1][1] % 1.0


def _smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3 - 2 * u)


def camera_pose(t, keys=CAM_KEYS, duration=DURATION):
    """相机 (eye, target)，关键帧 + smoothstep 缓动（LINEAR 采样器上手动做 easing）。"""
    t = max(0.0, min(duration, t))
    for i in range(len(keys) - 1):
        t0, e0, g0 = keys[i]
        t1, e1, g1 = keys[i + 1]
        if t0 <= t <= t1:
            k = _smooth(0.0 if t1 <= t0 else (t - t0) / (t1 - t0))
            eye = tuple(e0[j] + (e1[j] - e0[j]) * k for j in range(3))
            tgt = tuple(g0[j] + (g1[j] - g0[j]) * k for j in range(3))
            return eye, tgt
    return keys[-1][1], keys[-1][2]


def act_at(t):
    idx = 0
    for i, a in enumerate(ACTS):
        if t >= a[0]:
            idx = i
    return ACTS[idx]


# --------------------------------------------------- 建造动画（第三幕）--
# 第三幕拼装小屋的地块：村庄台地在 y=14、x/z ∈ [27,45]，三栋既有小屋占 z≥31，
# 所以这块选在 z=27 起、紧贴北缘 —— 第一版给了 (36,8,30)，结果是"房子埋在地里
# 又和既有小屋撞在一起"。
BUILD_ORIGIN = (32.0, 15.0, 27.0)


def camera_clearance(blocks, keys=CAM_KEYS, duration=DURATION, step=0.25,
                     probe=10.0, width=WORLD_W):
    """相机净空检查（纯函数，供单测）：沿相机轨迹采样，返回违规清单。

    两类违规 —— 这类问题肉眼要等到出截图才发现，机械检查是唯一省事的办法：

    - `inside`：机位落在实心方块里（等于把镜头塞进土里）；
    - `blocked`：机位到注视点之间前 `probe` 格内有实心方块（镜头贴着一堵墙/一丛树叶）。

    水面不算遮挡（水下镜头是刻意的观感），树叶/玻璃算。
    """
    out = []
    t = 0.0
    while t <= duration + 1e-6:
        eye, tgt = camera_pose(t, keys, duration)
        ex, ey, ez = (int(math.floor(v)) for v in eye)
        if 0 <= ex < width and 0 <= ez < width and 0 <= ey < MAX_H:
            b = blocks[(ey * width + ez) * width + ex]
            if b not in (AIR, WATER):
                out.append(('inside', round(t, 2), eye, b))
        # 视线前 probe 格逐格取样（步长 0.5，跳过眼皮底下那 0.5 格）
        d = 0.5
        while d <= probe:
            k = d / probe
            px = eye[0] + (tgt[0] - eye[0]) * (k * probe / probe)
            # 沿 eye→target 方向按实际距离推进
            dx, dy, dz = (tgt[i] - eye[i] for i in range(3))
            ln = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
            px = eye[0] + dx / ln * d
            py = eye[1] + dy / ln * d
            pz = eye[2] + dz / ln * d
            ix, iy, iz = (int(math.floor(v)) for v in (px, py, pz))
            if 0 <= ix < width and 0 <= iz < width and 0 <= iy < MAX_H:
                b = blocks[(iy * width + iz) * width + ix]
                if b not in (AIR, WATER):
                    out.append(('blocked', round(t, 2), (round(px, 1), round(py, 1),
                                                         round(pz, 1)), b))
                    break
            d += 0.5
        t += step
    return out


def build_schedule(count, t_start=20.0, span=8.0):
    """返回 [(时刻, 序号)]：方块依次落下的时间表（确定性、可单测）。

    顺序按"越靠后越快"排（二次曲线），看起来才像有人在盖房子而不是随机爆开。
    """
    out = []
    for i in range(count):
        k = i / max(1, count - 1)
        delay = t_start + span * (0.15 * k + 0.85 * k * k)   # 后段加速：收尾更利落
        out.append((delay, i))
    return out


def build_house_shape(origin=BUILD_ORIGIN, size=(5, 4, 4)):
    """第三幕要拼的那栋小屋：返回 [(dx, dy, dz, block_id)]。"""
    ox, oy, oz = origin
    w, d, h = size
    out = []
    for dz in range(d):
        for dx in range(w):
            for dy in range(h):
                edge = dx in (0, w - 1) or dz in (0, d - 1)
                corner = dx in (0, w - 1) and dz in (0, d - 1)
                if not edge:
                    if dy == h - 1:
                        out.append((dx, dy, dz, ROOF))
                    continue
                if corner:
                    out.append((dx, dy, dz, COBBLE))
                elif dy == h - 2:
                    out.append((dx, dy, dz, WINDOW))
                else:
                    out.append((dx, dy, dz, PLANKS))
    for dz in range(-1, d + 1):
        for dx in range(-1, w + 1):
            out.append((dx, h, dz, ROOF))
    return [(ox + dx, oy + dy, oz + dz, b) for (dx, dy, dz, b) in out]

# -*- coding: utf-8 -*-
"""KID-A WALKER（3D 化）纯逻辑层：房间模版库 / 区块无限地图 / 能量 / 碎片 / 区域。

移植自 arcade 项目 ``kid_a_walker.py``（MIT · siwpan）——22 个 21×21 房间模版、
出口驱动模板筛选、3×3 预加载 + 远区块卸载、能量与碎片系统。3D 化改造：

- 全局 ``random`` → 每 WorldMap 自带 ``rng``（世界可复现、单测可断言）
- 格值编码保持原样（0=空 1=墙 2=发光 3=红区 4=金 5=酸/植被 6=紫能 7=橙光
  8=碎石 9=科技 10/11=水晶 12=传送门）
- 新增 ``ZONE_INFO``：区域名 / 主色 / 音乐参数（3D 渲染与配乐共用一份数据）

本模块**不 import 图形库**：网格是"字符串行"，能量是"标量"——全部可无窗口单测。
"""
import math
import random

__all__ = [
    'CHUNK', 'CELL_NAMES', 'ZONE_INFO', 'DARK_TEMPLATES',
    'WorldMap', 'EnergySystem', 'FragmentSystem',
]

CHUNK = 21                     # 每区块 21×21 格（原版约定：出口固定在中心 3 格）
WALL_H = 2.6                   # 墙高（世界单位）——3D 化新增

# ---------------------------------------------------------------- 格值编码 --
AIR, WALL = 0, 1
GLOW, RED, GOLD, ACID, PURPLE, ORANGE, DEBRIS, TECH = 2, 3, 4, 5, 6, 7, 8, 9
CRYSTAL_A, CRYSTAL_B, PORTAL = 10, 11, 12

CELL_CHARS = {'.': 0, '#': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7,
              '8': 8, '9': 9, 'A': 10, 'B': 11, 'C': 12}

CELL_NAMES = {
    GLOW: '发光柱', RED: '红色警示', GOLD: '金色遗物', ACID: '酸液池',
    PURPLE: '紫色能量', ORANGE: '橙色光源', DEBRIS: '碎石', TECH: '科技残骸',
    CRYSTAL_A: '蓝色水晶', CRYSTAL_B: '紫色水晶', PORTAL: '传送门',
}
# 可收集的碎片类型（与原版一致：deco 2~11）
FRAGMENT_TYPES = tuple(range(2, 12))

# 区域表：id -> (名称, 主色 RGB, 氛围说明, 音乐参数)
# 音乐参数 = (音符序列, BPM, 波形, 混响浓度, 音高偏移)——移植自原版 ZONE_CONFIGS
ZONE_INFO = {
    2:  ('空灵回廊', (0, 230, 200), '青色的光柱在暗处呼吸',
         (["C4", "E4", "G4", "C5", "G4", "E4", "C4", "E4"], 90, 'sine', 0.5, 0)),
    3:  ('警戒区',   (220, 70, 60), '红色的警示纹路在脚下蔓延',
         (["Eb4", "Eb4", "Bb3", "Eb4", "Eb4", "Bb3", "F4", "Eb4"], 130, 'sawtooth', 0.2, 2)),
    4:  ('宝库',     (220, 190, 80), '金色的遗物堆叠在阴影里',
         (["C4", "G4", "C5", "E5", "G4", "C5", "A4", "G4"], 70, 'organ', 0.6, -1)),
    5:  ('酸蚀池',   (70, 220, 120), '酸液在格栅间缓慢涌动',
         (["A3", "C4", "E4", "G4", "E4", "C4", "A3", "D4"], 85, 'sine', 0.4, -2)),
    6:  ('能量核心', (170, 100, 240), '紫色的能量在柱间脉动',
         (["F4", "F#4", "G4", "A4", "C5", "A4", "G4", "F#4"], 140, 'square', 0.3, 3)),
    7:  ('引导区',   (255, 150, 60), '橙色的光源指引着方向',
         (["C4", "E4", "G4", "C5", "E5", "G4", "C5", "E4"], 100, 'triangle', 0.35, 0)),
    8:  ('废墟',     (150, 150, 160), '碎石堆间只有沉默',
         (["D3", "D3", "A3", "D3", "D3", "A3", "G3", "D3"], 60, 'sine', 0.15, -3)),
    9:  ('数据神殿', (80, 220, 255), '数据流在墙面无声滚动',
         (["G4", "A4", "C5", "D5", "C5", "A4", "G4", "F4"], 160, 'square', 0.25, 4)),
    10: ('水晶洞窟', (100, 180, 255), '蓝色水晶从地面生长出来',
         (["C5", "F5", "C5", "A4", "F4", "C4", "G4", "C5"], 75, 'sine', 0.7, 1)),
    11: ('秘仪厅',   (170, 110, 230), '紫色的晶体微微发亮',
         (["Bb3", "Eb4", "G4", "C5", "G4", "Eb4", "Bb3", "D4"], 95, 'sine', 0.55, 2)),
    12: ('传送门',   (255, 240, 110), '金色的门在低鸣',
         (["C5", "E5", "G5", "C6", "G5", "E5", "C5", "E5"], 110, 'triangle', 0.6, 5)),
}

DARK_TEMPLATES = {'make_room_dark_cross', 'make_room_secret_ns'}

_DIR_DELTA = {'N': (0, -1), 'S': (0, 1), 'W': (-1, 0), 'E': (1, 0)}
_OPPOSITE = {'N': 'S', 'S': 'N', 'E': 'W', 'W': 'E'}


# ---------------------------------------------------------------- 网格工具 --
def _mk_empty():
    return [['#'] * CHUNK for _ in range(CHUNK)]


def _open_north(g):
    for c in (9, 10, 11):
        g[0][c] = g[1][c] = '.'


def _open_south(g):
    for c in (9, 10, 11):
        g[20][c] = g[19][c] = '.'


def _open_west(g):
    for r in (9, 10, 11):
        g[r][0] = g[r][1] = '.'


def _open_east(g):
    for r in (9, 10, 11):
        g[r][20] = g[r][19] = '.'


def _fill(g, r1, c1, r2, c2, ch='.'):
    for r in range(r1, r2 + 1):
        for c in range(c1, c2 + 1):
            g[r][c] = ch


def _to_rows(g):
    return [''.join(row) for row in g]


# ------------------------------------------------------------ 房间模版库 --
def make_room_cross(rng):
    """十字走廊（四向）"""
    g = _mk_empty()
    _fill(g, 9, 0, 11, 20)
    _fill(g, 0, 9, 20, 11)
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_chamber(rng):
    """中央大厅 + 柱阵（四向）"""
    g = _mk_empty()
    _fill(g, 2, 2, 18, 18)
    for r in (5, 10, 15):
        for c in (5, 10, 15):
            g[r][c] = '#'
    for r, c in ((5, 5), (5, 15), (15, 5), (15, 15)):
        g[r][c] = '2'
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_hub(rng):
    """中枢大厅：中心岛 + 四向短廊"""
    g = _mk_empty()
    _fill(g, 1, 1, 19, 19)
    _fill(g, 8, 8, 12, 12, '#')
    g[10][10] = '2'
    _fill(g, 0, 9, 1, 11)
    _fill(g, 19, 9, 20, 11)
    _fill(g, 9, 0, 11, 1)
    _fill(g, 9, 19, 11, 20)
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_dark_cross(rng):
    """暗室十字（四角发光柱）"""
    g = _mk_empty()
    _fill(g, 9, 0, 11, 20)
    _fill(g, 0, 9, 20, 11)
    for r, c in ((3, 3), (3, 17), (17, 3), (17, 17)):
        g[r][c] = '2'
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_golden_treasury(rng):
    """金色宝库（四向）"""
    g = _mk_empty()
    _fill(g, 9, 0, 11, 20)
    _fill(g, 0, 9, 20, 11)
    for r, c in ((3, 3), (3, 17), (17, 3), (17, 17), (10, 5), (10, 15),
                 (5, 10), (15, 10)):
        g[r][c] = '4'
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_energy_core(rng):
    """紫色能量核心（四向）"""
    g = _mk_empty()
    _fill(g, 4, 4, 16, 16)
    # 门廊：把门洞(0-1)连到大厅(4-16)——漏掉这步时四个门"通而不达"、
    # 整个区块被隔离（原版同款缺陷，已在 arcade 侧一并修复）。
    _fill(g, 2, 9, 3, 11)      # N 门廊
    _fill(g, 17, 9, 18, 11)    # S 门廊
    _fill(g, 9, 2, 11, 3)      # W 门廊
    _fill(g, 9, 17, 11, 18)    # E 门廊
    g[10][10] = '6'
    for r, c in ((6, 6), (6, 14), (14, 6), (14, 14)):
        g[r][c] = '6'
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_portal_gate(rng):
    """传送门大厅（稀有）"""
    g = _mk_empty()
    _fill(g, 4, 4, 16, 16)
    # 门廊：连门洞(0-1)与大厅(4-16)（与 energy_core 同款修复，
    # 原版缺陷已在 arcade 侧一并修复）
    _fill(g, 2, 9, 3, 11)      # N 门廊
    _fill(g, 17, 9, 18, 11)    # S 门廊
    _fill(g, 9, 2, 11, 3)      # W 门廊
    _fill(g, 9, 17, 11, 18)    # E 门廊
    g[10][10] = 'C'
    for r, c in ((6, 6), (6, 14), (14, 6), (14, 14)):
        g[r][c] = '2'
    for r, c in ((9, 10), (11, 10), (10, 9), (10, 11)):
        g[r][c] = '9'
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_trader_outpost(rng):
    """商人哨站（四向，十字 + 摊位）"""
    g = _mk_empty()
    _fill(g, 9, 0, 11, 20)
    _fill(g, 0, 9, 20, 11)
    for r, c in ((8, 4), (8, 15), (12, 4), (12, 15)):
        _fill(g, r, c, r + 2, c + 3, '#')
        g[r + 1][c + 1] = '4'
    _open_north(g); _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_hall_ns(rng):
    """南北大走廊"""
    g = _mk_empty()
    _fill(g, 0, 7, 20, 13)
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_hall_ns_glow(rng):
    """南北大走廊（发光带）"""
    g = _mk_empty()
    _fill(g, 0, 7, 20, 13)
    for r in range(3, 20, 4):
        g[r][8] = '2'
        g[r][12] = '2'
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_hall_ew(rng):
    """东西大走廊"""
    g = _mk_empty()
    _fill(g, 7, 0, 13, 20)
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_maze_ns(rng):
    """迷宫型（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 1, 20, 19)
    for r1, c1, r2, c2 in ((3, 3, 3, 8), (3, 12, 3, 17), (6, 1, 6, 6),
                           (6, 10, 6, 15), (9, 4, 9, 9), (9, 11, 9, 16),
                           (12, 2, 12, 7), (12, 13, 12, 18),
                           (15, 5, 15, 10), (15, 11, 15, 16)):
        _fill(g, r1, c1, r2, c2, '#')
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_maze_ew(rng):
    """迷宫型（E/W）"""
    g = _mk_empty()
    _fill(g, 1, 0, 19, 20)
    for r1, c1, r2, c2 in ((2, 3, 7, 3), (2, 9, 7, 9), (2, 15, 7, 15),
                           (13, 3, 18, 3), (13, 9, 18, 9), (13, 15, 18, 15),
                           (8, 6, 12, 6), (8, 12, 12, 12)):
        _fill(g, r1, c1, r2, c2, '#')
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_long_corridor_ns(rng):
    """细长南北走廊（侧龛）"""
    g = _mk_empty()
    _fill(g, 0, 8, 20, 12)
    for r in (3, 7, 13, 17):
        _fill(g, r, 5, r + 1, 7)
        _fill(g, r, 13, r + 1, 15)
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_long_corridor_ew(rng):
    """细长东西走廊（凹槽）"""
    g = _mk_empty()
    _fill(g, 8, 0, 12, 20)
    for c in (3, 7, 13, 17):
        _fill(g, 5, c, 7, c + 1)
        _fill(g, 13, c, 15, c + 1)
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_secret_ns(rng):
    """暗路密室：S 形窄径（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 9, 7, 11)
    _fill(g, 6, 6, 8, 14)
    _fill(g, 7, 8, 14, 10)
    _fill(g, 13, 8, 15, 15)
    _fill(g, 14, 9, 20, 11)
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_glow_hall(rng):
    """发光走廊大厅（E/W）"""
    g = _mk_empty()
    _fill(g, 3, 0, 17, 20)
    for c in range(2, 19, 3):
        g[5][c] = '2'
        g[15][c] = '2'
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_red_warning(rng):
    """红色警示区（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 6, 20, 14)
    for r in (3, 8, 12, 17):
        g[r][7] = '3'
        g[r][13] = '3'
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_garden(rng):
    """绿色花园（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 4, 20, 16)
    for r in (2, 6, 10, 14, 18):
        for c in (5, 9, 13):
            if rng.random() > 0.4:
                g[r][c] = '5'
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_debris(rng):
    """碎石废墟（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 5, 20, 15)
    for _ in range(12):
        r = rng.randint(2, 18)
        c = rng.randint(6, 14)
        if g[r][c] == '.':
            g[r][c] = '8'
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_acid_pool(rng):
    """酸液池（N/S，两侧安全路径）"""
    g = _mk_empty()
    _fill(g, 0, 5, 20, 15)
    for r in (6, 8, 10, 12, 14):
        for c in (6, 8, 10, 12, 14):
            if rng.random() > 0.3:
                g[r][c] = '5'
    _fill(g, 6, 4, 14, 5)
    _fill(g, 6, 15, 14, 16)
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_shrine(rng):
    """神秘祭坛（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 5, 20, 15)
    _fill(g, 8, 8, 12, 12, '#')
    g[10][10] = '2'
    for r, c in ((6, 7), (6, 13), (14, 7), (14, 13)):
        g[r][c] = '2'
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_floating_platforms(rng):
    """悬浮平台（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 4, 20, 16)
    for r, c in ((3, 6), (5, 10), (8, 8), (11, 14), (14, 7), (16, 12)):
        _fill(g, r, c, r, c + 2, '#')
        if rng.random() > 0.4:
            g[r - 1][c + 1] = '2'
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_forgotten_tomb(rng):
    """遗忘陵墓（N/S）"""
    g = _mk_empty()
    _fill(g, 0, 3, 20, 17)
    _fill(g, 8, 8, 12, 12, '#')
    g[10][10] = '6'
    for r, c in ((4, 5), (4, 14), (16, 5), (16, 14)):
        g[r][c] = '8'
    _open_north(g); _open_south(g)
    return _to_rows(g)


def make_room_crystal_cavern(rng):
    """水晶洞窟（E/W）"""
    g = _mk_empty()
    _fill(g, 2, 0, 18, 20)
    for r in (4, 8, 12, 16):
        g[r][6] = 'A'
        g[r][14] = 'A'
    g[8][10] = 'B'
    g[12][10] = 'B'
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_neon_highway(rng):
    """霓虹高速（E/W）"""
    g = _mk_empty()
    _fill(g, 0, 0, 20, 20)
    for r in (9, 10, 11):
        for c in range(0, 20):
            if c % 4 == 0:
                g[r][c] = '9'
    for c in (3, 7, 11, 15, 19):
        g[3][c] = '3'
        g[17][c] = '2'
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_ancient_library(rng):
    """远古图书馆（E/W）"""
    g = _mk_empty()
    _fill(g, 1, 0, 19, 20)
    for r in (2, 4, 6, 8, 10, 12, 14, 16, 18):
        for c in (3, 7, 11, 15, 19):
            if c <= 18:
                g[r][c] = '#'
                if rng.random() > 0.5:
                    g[r][c - 1] = '4'
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_abandoned_lab(rng):
    """废弃实验室（E/W）"""
    g = _mk_empty()
    _fill(g, 3, 0, 17, 20)
    for c in (4, 8, 12, 16):
        _fill(g, 4, c, 7, c + 2, '#')
        if rng.random() > 0.3:
            g[5][c + 1] = '8'
    for c in (6, 14):
        g[10][c] = '3'
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_pit_trap(rng):
    """陷阱坑室（E/W，中央深坑 + 十字窄桥）"""
    g = _mk_empty()
    _fill(g, 0, 0, 20, 20)
    _fill(g, 7, 7, 13, 13, '8')
    _fill(g, 10, 0, 10, 6)
    _fill(g, 10, 14, 10, 20)
    _fill(g, 0, 10, 6, 10)
    _fill(g, 14, 10, 20, 10)
    _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_data_temple(rng):
    """数据神殿（N/S/E）"""
    g = _mk_empty()
    _fill(g, 0, 7, 20, 13)
    _fill(g, 7, 7, 13, 20)
    for r in (3, 6, 9, 12, 15, 18):
        for c in (8, 10, 12):
            if rng.random() > 0.5:
                g[r][c] = '9'
    _open_north(g); _open_south(g); _open_east(g)
    return _to_rows(g)


def make_room_tech_nook(rng):
    """科技角落（N/S/E）"""
    g = _mk_empty()
    _fill(g, 0, 9, 20, 11)
    _fill(g, 9, 9, 11, 20)
    for r in (3, 7, 14, 18):
        g[r][10] = '9'
    _open_north(g); _open_south(g); _open_east(g)
    return _to_rows(g)


def make_room_bone_garden(rng):
    """骨骸花园（S/E/W）"""
    g = _mk_empty()
    _fill(g, 4, 0, 20, 20)
    for _ in range(8):
        r = rng.randint(6, 18)
        c = rng.randint(4, 16)
        g[r][c] = '8'
        if c + 1 < 16:
            g[r][c + 1] = '8'
    _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_generator_room(rng):
    """发电机室（N/S/W）"""
    g = _mk_empty()
    _fill(g, 0, 9, 20, 11)
    _fill(g, 9, 0, 11, 11)
    # 大型发电机（rows 3-8，避开 rows 9-11 的西侧门廊——原位置 rows 6-14
    # 压在门廊上把西门封成死路，原版同款缺陷已在 arcade 侧一并修复）
    _fill(g, 3, 3, 8, 7, '#')
    for r in (8, 12):
        g[r][5] = '7'
        g[r][6] = '7'
    _open_north(g); _open_south(g); _open_west(g)
    return _to_rows(g)


def make_room_tee_n(rng):
    """T 字（N/E/W）"""
    g = _mk_empty()
    _fill(g, 0, 9, 20, 11)
    _fill(g, 9, 0, 11, 20)
    _open_north(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_tee_s(rng):
    """T 字（S/E/W）"""
    g = _mk_empty()
    _fill(g, 0, 9, 20, 11)
    _fill(g, 9, 0, 11, 20)
    _open_south(g); _open_west(g); _open_east(g)
    return _to_rows(g)


def make_room_tee_e(rng):
    """T 字（N/S/E）"""
    g = _mk_empty()
    _fill(g, 0, 9, 20, 11)
    _fill(g, 9, 9, 11, 20)
    _open_north(g); _open_south(g); _open_east(g)
    return _to_rows(g)


def make_room_tee_w(rng):
    """T 字（N/S/W）"""
    g = _mk_empty()
    _fill(g, 0, 9, 20, 11)
    _fill(g, 9, 0, 11, 11)
    _open_north(g); _open_south(g); _open_west(g)
    return _to_rows(g)


def make_room_corner_ne(rng):
    """转角（N/E）"""
    g = _mk_empty()
    _fill(g, 0, 9, 11, 11)
    _fill(g, 9, 9, 11, 20)
    _open_north(g); _open_east(g)
    return _to_rows(g)


def make_room_corner_nw(rng):
    """转角（N/W）"""
    g = _mk_empty()
    _fill(g, 0, 9, 11, 11)
    _fill(g, 9, 0, 11, 11)
    _open_north(g); _open_west(g)
    return _to_rows(g)


def make_room_corner_se(rng):
    """转角（S/E）"""
    g = _mk_empty()
    _fill(g, 9, 9, 20, 11)
    _fill(g, 9, 9, 11, 20)
    _open_south(g); _open_east(g)
    return _to_rows(g)


def make_room_corner_sw(rng):
    """转角（S/W）"""
    g = _mk_empty()
    _fill(g, 9, 9, 20, 11)
    _fill(g, 9, 0, 11, 11)
    _open_south(g); _open_west(g)
    return _to_rows(g)


# 出口组合 -> 候选模版（移植自原版 _ALL_TEMPLATES）
_ALL_TEMPLATES = {
    frozenset({'N', 'S', 'E', 'W'}): [make_room_cross, make_room_chamber,
                                      make_room_dark_cross, make_room_hub,
                                      make_room_golden_treasury,
                                      make_room_energy_core,
                                      make_room_trader_outpost,
                                      make_room_portal_gate],
    frozenset({'N', 'S'}): [make_room_hall_ns, make_room_hall_ns_glow,
                            make_room_maze_ns, make_room_long_corridor_ns,
                            make_room_secret_ns, make_room_red_warning,
                            make_room_garden, make_room_debris,
                            make_room_shrine, make_room_floating_platforms,
                            make_room_forgotten_tomb, make_room_acid_pool],
    frozenset({'E', 'W'}): [make_room_hall_ew, make_room_maze_ew,
                            make_room_long_corridor_ew, make_room_glow_hall,
                            make_room_crystal_cavern, make_room_neon_highway,
                            make_room_ancient_library, make_room_abandoned_lab,
                            make_room_pit_trap],
    # 注：原版把 tech_nook / data_temple 放在 N/E/W 组，但其实现只开 N/S/E 口
    # ——声明出口与实际网格不符（西侧进入会撞墙）。此处按**实现**修正分组。
    frozenset({'N', 'E', 'W'}): [make_room_tee_n],
    frozenset({'S', 'E', 'W'}): [make_room_tee_s, make_room_bone_garden],
    frozenset({'N', 'S', 'E'}): [make_room_tee_e, make_room_tech_nook,
                                 make_room_data_temple],
    frozenset({'N', 'S', 'W'}): [make_room_tee_w, make_room_generator_room],
    frozenset({'N', 'E'}): [make_room_corner_ne],
    frozenset({'N', 'W'}): [make_room_corner_nw],
    frozenset({'S', 'E'}): [make_room_corner_se],
    frozenset({'S', 'W'}): [make_room_corner_sw],
}


# ------------------------------------------------------------ 区块世界 --
class WorldMap:
    """无限延伸的区块网格（3×3 预加载 / 远区块卸载 / 出口互连推导）。"""

    PRELOAD = 1
    CLEAN = 2

    def __init__(self, seed=20261009):
        self.rng = random.Random(seed)
        self.chunks = {}          # (cx,cy) -> list[str]（21 行）
        self.exits = {}           # (cx,cy) -> frozenset{'N','S','E','W'}
        self.templates = {}       # (cx,cy) -> 模版函数名（暗室判定）
        self._generate_chunk((0, 0), forced={'N', 'S', 'E', 'W'})
        self.ensure_neighbors(0, 0)

    # ---- 生成 ----
    def _generate_chunk(self, coord, forced=None):
        if coord in self.chunks:
            return
        cx, cy = coord
        need = set(forced or ())
        for d, (dx, dy) in _DIR_DELTA.items():      # 与已生成邻居互连
            nb = (cx + dx, cy + dy)
            if nb in self.exits and _OPPOSITE[d] in self.exits[nb]:
                need.add(d)
        need = self._pick_exits(need, coord)
        fn = self._pick_template(need)
        self.chunks[coord] = fn(self.rng)
        self.exits[coord] = frozenset(need)
        self.templates[coord] = fn.__name__

    def _pick_exits(self, forced, coord):
        """在 forced 上随机扩充出口，保证至少两条路。

        ``forced`` 来自"已生成邻居声明了反向出口"，天然互连。随机扩充必须
        额外检查：**若某方向的邻居已生成且没有声明反向出口，则该方向不能开**
        ——否则会产生"单边出口"（玩家走出去撞墙）。原版没查这一步。
        """
        exits = set(forced)
        cx, cy = coord

        def _ok(d):
            nb = (cx + _DIR_DELTA[d][0], cy + _DIR_DELTA[d][1])
            return nb not in self.exits or _OPPOSITE[d] in self.exits[nb]

        for d in ('N', 'S', 'E', 'W'):
            if d not in exits and _ok(d) and self.rng.random() < 0.45:
                exits.add(d)
        pool = [d for d in ('N', 'S', 'E', 'W') if d not in exits and _ok(d)]
        while len(exits) < 2 and pool:               # 补足至少两条（不硬凑死路）
            d = self.rng.choice(pool)
            pool.remove(d)
            exits.add(d)
        return exits

    def _pick_template(self, exits):
        key = frozenset(exits)
        if key in _ALL_TEMPLATES:
            return self.rng.choice(_ALL_TEMPLATES[key])
        pool = []
        for tmpl_exits, fns in _ALL_TEMPLATES.items():
            if key.issubset(tmpl_exits):
                pool.extend(fns)
        return self.rng.choice(pool) if pool else make_room_cross

    def ensure_neighbors(self, cx, cy):
        for dcy in range(-self.PRELOAD, self.PRELOAD + 1):
            for dcx in range(-self.PRELOAD, self.PRELOAD + 1):
                self._generate_chunk((cx + dcx, cy + dcy))
        self._cleanup(cx, cy)

    def _cleanup(self, cx, cy):
        for coord in list(self.chunks):
            if (abs(coord[0] - cx) > self.CLEAN
                    or abs(coord[1] - cy) > self.CLEAN):
                self.chunks.pop(coord, None)
                self.exits.pop(coord, None)
                self.templates.pop(coord, None)

    # ---- 查询 ----
    def cell(self, wx, wy):
        """世界坐标 -> 格值（未生成区块返回 WALL，防空气墙）。"""
        cx = int(math.floor(wx / CHUNK))
        cy = int(math.floor(wy / CHUNK))
        lx = int(wx - cx * CHUNK)
        ly = int(wy - cy * CHUNK)
        lx = max(0, min(CHUNK - 1, lx))
        ly = max(0, min(CHUNK - 1, ly))
        chunk = self.chunks.get((cx, cy))
        if chunk is None:
            return WALL
        return CELL_CHARS.get(chunk[ly][lx], WALL)

    def cell_blocking(self, wx, wy):
        return self.cell(wx, wy) == WALL

    @staticmethod
    def world_to_chunk(wx, wy):
        return int(math.floor(wx / CHUNK)), int(math.floor(wy / CHUNK))

    def is_dark_room(self, cx, cy):
        name = self.templates.get((cx, cy))
        if name in DARK_TEMPLATES:
            return True
        chunk = self.chunks.get((cx, cy))
        if chunk is None:
            return False
        glow = sum(row.count('2') for row in chunk)
        open_cells = sum(row.count('.') for row in chunk)
        return glow <= 4 and open_cells < 80

    def chunk_zone(self, cx, cy):
        """区块主导区域（装饰最多的类型；无装饰 -> 空灵回廊）。"""
        chunk = self.chunks.get((cx, cy))
        if chunk is None:
            return 2
        counts = {}
        for row in chunk:
            for ch in row:
                v = CELL_CHARS.get(ch, 0)
                if v >= 2:
                    counts[v] = counts.get(v, 0) + 1
        return max(counts, key=counts.get) if counts else 2

    def decorations(self, cx, cy):
        """区块内装饰格：[(world_x, world_y, cell_value), ...]。"""
        chunk = self.chunks.get((cx, cy))
        if chunk is None:
            return []
        out = []
        for ly, row in enumerate(chunk):
            for lx, ch in enumerate(row):
                v = CELL_CHARS.get(ch, 0)
                if v >= 2:
                    out.append((cx * CHUNK + lx + 0.5, cy * CHUNK + ly + 0.5, v))
        return out

    def find_safe_spawn(self, cx, cy):
        """找安全出生点（空地、周围 5×5 无墙/红区/酸液/传送门）。"""
        for ccx, ccy in [(cx, cy)] + [(cx + dx, cy + dy)
                                      for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                                      if (dx, dy) != (0, 0)]:
            chunk = self.chunks.get((ccx, ccy))
            if chunk is None:
                self._generate_chunk((ccx, ccy))
                chunk = self.chunks.get((ccx, ccy))
            if chunk is None:
                continue
            center = CHUNK // 2
            for radius in range(6):
                for dy in range(-radius, radius + 1):
                    for dx in range(-radius, radius + 1):
                        if max(abs(dx), abs(dy)) != radius:
                            continue
                        lx, ly = center + dx, center + dy
                        if not (0 <= lx < CHUNK and 0 <= ly < CHUNK):
                            continue
                        if chunk[ly][lx] != '.':
                            continue
                        ok = True
                        for oy in range(-2, 3):
                            for ox in range(-2, 3):
                                nx, ny = lx + ox, ly + oy
                                if 0 <= nx < CHUNK and 0 <= ny < CHUNK:
                                    if chunk[ny][nx] in ('#', '3', '5', 'C'):
                                        ok = False
                                        break
                            if not ok:
                                break
                        if ok:
                            return (ccx * CHUNK + lx + 0.5,
                                    ccy * CHUNK + ly + 0.5)
        return (cx * CHUNK + 10.5, cy * CHUNK + 10.5)


# ------------------------------------------------------------ 玩法系统 --
ENERGY_MAX = 100.0
DRAIN_MOVE = 1.0          # 移动消耗 点/秒
DRAIN_RED = 3.0           # 红区额外消耗
DRAIN_ACID = 15.0         # 酸液消耗
RECOVER_GLOW = 0.5        # 发光柱附近恢复
GRACE_PERIOD = 3.0        # 出生保护
INTERACT_RANGE = 2.2      # 交互/收集范围


class EnergySystem:
    """能量：移动/红区/酸液消耗，发光柱恢复（原版数值）。"""

    def __init__(self):
        self.reset()

    def reset(self):
        self.energy = ENERGY_MAX
        self.grace = GRACE_PERIOD
        self.is_dead = False
        self.zone_hits = 0

    def update(self, dt, on_ground_cell, is_moving, near_glow):
        if self.grace > 0:
            self.grace -= dt
            if near_glow:
                self.energy = min(ENERGY_MAX, self.energy + RECOVER_GLOW * dt)
            return
        if on_ground_cell == RED:
            self.zone_hits += 1
            self.energy -= DRAIN_RED * dt
        if on_ground_cell == ACID:
            self.energy -= DRAIN_ACID * dt
        if is_moving:
            self.energy -= DRAIN_MOVE * dt
        if near_glow:
            self.energy = min(ENERGY_MAX, self.energy + RECOVER_GLOW * dt)
        self.energy = max(0.0, min(ENERGY_MAX, self.energy))
        if self.energy <= 0:
            self.is_dead = True

    def ratio(self):
        return self.energy / ENERGY_MAX


class FragmentSystem:
    """碎片收集 + 图鉴（跨局保留，原版语义）。"""

    def __init__(self):
        self.collected = {}       # type -> count
        self.interacted = set()   # 稳定键（区块 + 格坐标）
        self.codex = set()        # 跨局图鉴

    def collect(self, deco_type, key):
        if key in self.interacted:
            return False
        self.interacted.add(key)
        self.collected[deco_type] = self.collected.get(deco_type, 0) + 1
        self.codex.add(deco_type)
        return True

    def total(self):
        return sum(self.collected.values())

    def unique_types(self):
        return len(self.collected)

    def is_full(self):
        return self.unique_types() >= len(FRAGMENT_TYPES)

# -*- coding: utf-8 -*-
"""《最后一百公里》场景模板库：地点 × 时间 × 道具 × 构图。

把 100 个剧本场次（2026-10-20 解析结果）归纳成有限的几组模板，
让 animatic 流水线可以用 ``build_scene(place, tod, props)`` 拿到一个
可直接交给 3dworld 渲染的 JSON 场景，再用 ``POSE`` 里的构图出静帧。

约定：
- ``place``  剧本里的"地点"字段，经 :func:`place_key` 归一化成 ~12 类
- ``tod``    time of day，5 档光环境：night / dawn / day / gold / dusk
- ``pose``   构图名，返回 (position, target, fov)
"""
from __future__ import annotations

import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.dirname(_HERE)
ROOT = os.path.dirname(PROBES)
sys.path.insert(0, ROOT)
sys.path.insert(0, PROBES)

import numpy as np                                             # noqa: E402

from gobi_scene import (Y0, gobi_heightmap, milestone_node,   # noqa: E402
                        pole_node, scatter_rocks, truck_node)

TERRAIN_H = 7.0


# ------------------------------------------------------------- 时间 · 光 --
TOD = {
    'night': {
        'sun_dir': [-0.35, 0.62, 0.35], 'sun_color': [0.55, 0.66, 0.95],
        'sun_intensity': 0.70, 'sky_zenith': [0.045, 0.085, 0.20],
        'sky_horizon': [0.12, 0.16, 0.28], 'sky_ground': [0.05, 0.05, 0.07],
        'ibl_intensity': 0.55, 'sky_intensity': 0.55, 'exposure': 1.55,
    },
    'dawn': {
        'sun_dir': [0.92, 0.16, -0.34], 'sun_color': [1.0, 0.72, 0.48],
        'sun_intensity': 1.25, 'sky_zenith': [0.16, 0.30, 0.55],
        'sky_horizon': [0.86, 0.62, 0.42], 'sky_ground': [0.30, 0.24, 0.19],
        'ibl_intensity': 0.55, 'sky_intensity': 0.78, 'exposure': 1.05,
    },
    'day': {
        'sun_dir': [0.42, 0.85, -0.30], 'sun_color': [1.0, 0.90, 0.72],
        'sun_intensity': 1.70, 'sky_zenith': [0.22, 0.40, 0.68],
        'sky_horizon': [0.78, 0.74, 0.62], 'sky_ground': [0.34, 0.28, 0.20],
        'ibl_intensity': 0.45, 'sky_intensity': 0.80, 'exposure': 0.90,
    },
    'gold': {
        'sun_dir': [0.86, 0.28, -0.42], 'sun_color': [1.0, 0.70, 0.42],
        'sun_intensity': 1.45, 'sky_zenith': [0.20, 0.34, 0.60],
        'sky_horizon': [0.90, 0.66, 0.42], 'sky_ground': [0.36, 0.28, 0.20],
        'ibl_intensity': 0.48, 'sky_intensity': 0.80, 'exposure': 0.95,
    },
    'dusk': {
        'sun_dir': [0.80, 0.12, -0.40], 'sun_color': [1.0, 0.60, 0.34],
        'sun_intensity': 1.10, 'sky_zenith': [0.14, 0.24, 0.46],
        'sky_horizon': [0.76, 0.48, 0.30], 'sky_ground': [0.20, 0.16, 0.13],
        'ibl_intensity': 0.50, 'sky_intensity': 0.70, 'exposure': 1.10,
    },
}

TOD_OF_TIME = {
    '凌晨': 'night', '深夜': 'night', '夜': 'night', '夜里': 'night',
    '拂晓': 'dawn', '天亮': 'dawn', '清晨': 'dawn', '天蒙蒙亮': 'dawn',
    '上午': 'day', '中午': 'day', '正午': 'day',
    '下午': 'gold', '傍晚': 'dusk', '黄昏': 'dusk',
}


def tod_of(time_text):
    t = (time_text or '').strip()
    if t in TOD_OF_TIME:
        return TOD_OF_TIME[t]
    for k, v in TOD_OF_TIME.items():
        if k in t:
            return v
    for cue, v in (('前夜', 'night'), ('同一时刻', 'dusk'), ('反复', 'night')):
        if cue in t:
            return v
    return 'day'


# ------------------------------------------------------------- 地点映射 --
PLACE_KEYS = [
    ('场院', 'yard'), ('货台', 'dock'), ('装货台', 'dock'), ('院子', 'yard'),
    ('驾驶室', 'cab'), ('车厢', 'boxbody'), ('车厢内', 'boxbody'),
    ('车尾', 'tail'), ('车头', 'front'), ('冷链车', 'twocars'),
    ('两台车', 'twocars'), ('私家车', 'twocars'),
    ('检查站', 'checkpoint'), ('临时检查站', 'checkpoint'),
    ('加油站', 'gas'), ('服务区', 'service'), ('便利店', 'service'),
    ('养护工区', 'ruin'), ('工区', 'ruin'), ('干沟', 'ruin'), ('凉棚', 'ruin'),
    ('办公室', 'office'), ('登记窗', 'office'),
    ('信箱', 'mailbox'), ('路基', 'roadside'), ('路肩', 'roadside'),
    ('停车带', 'roadside'), ('观景台', 'overlook'), ('公路', 'road'),
    ('路面', 'road'), ('戈壁', 'road'),
]


def place_key(place):
    p = (place or '').strip()
    for kw, key in PLACE_KEYS:
        if kw in p:
            return key
    if '路' in p:
        return 'road'
    return 'road'


# ------------------------------------------------------------- 道具组件 --
def _mat(color, preset='ceramic', rough=0.9, metal=0.0, emissive=None):
    m = {'preset': preset, 'base_color': list(color), 'roughness': rough,
         'metallic': metal}
    if emissive:
        m['emissive'] = list(emissive)
    return m


def crate_node(x, z, y=None, lid=False, name=None, scale=1.0):
    """铁皮运单箱（本剧的核心道具："不到终点不开"）。"""
    s = 0.9 * scale
    n = {'name': name or f'crate_{x:.1f}_{z:.1f}',
         'translation': [x, (Y0 if y is None else y) + 0.45 * scale, z],
         'mesh': {'builder': 'box',
                  'args': {'sx': s, 'sy': 0.9 * scale, 'sz': 0.7 * scale,
                           'color': [0.36, 0.35, 0.33]}},
         'material': _mat([0.36, 0.35, 0.33], 'steel', 0.55, 0.85)}
    if lid:
        n['children'] = [{'name': (name or f'crate_{x:.1f}') + '_seal',
                          'translation': [0, 0.32 * scale, 0],
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 0.94 * s, 'sy': 0.06,
                                            'sz': 0.1,
                                            'color': [0.75, 0.2, 0.12]}},
                          'material': _mat([0.75, 0.2, 0.12], 'plastic_red', 0.4)}]
    return n


def barrel_node(x, z, color=(0.20, 0.32, 0.20), y=None, name=None):
    """油桶（六块钱一升那场的主道具）。"""
    return {'name': name or f'barrel_{x:.1f}_{z:.1f}',
            'translation': [x, (Y0 if y is None else y) + 0.42, z],
            'mesh': {'builder': 'lathe',
                     'args': {'profile': [[0.0, -0.42], [0.28, -0.42],
                                          [0.32, -0.36], [0.32, -0.10],
                                          [0.34, -0.06], [0.34, 0.06],
                                          [0.32, 0.10], [0.32, 0.36],
                                          [0.28, 0.42], [0.0, 0.42]],
                              'segments': 14, 'color': list(color)}},
            'material': _mat(list(color), 'steel', 0.45, 0.8)}


def _plate(x, y, z, sx, sy, sz, color, preset='ceramic', rough=0.9,
           metal=0.0, name=None, rot=None, emissive=None):
    n = {'name': name or f'p_{x:.1f}_{y:.1f}_{z:.1f}',
         'translation': [x, y, z],
         'mesh': {'builder': 'box', 'args': {'sx': sx, 'sy': sy, 'sz': sz,
                                             'color': list(color)}},
         'material': _mat(color, preset, rough, metal, emissive)}
    if rot:
        n['rotation'] = rot
    return n


def _qz(deg):
    """绕 Z 轴旋转 deg 的四元数。"""
    a = math.radians(deg) * 0.5
    return [0.0, 0.0, math.sin(a), math.cos(a)]


def _qx(deg):
    a = math.radians(deg) * 0.5
    return [math.sin(a), 0.0, 0.0, math.cos(a)]


def barrier_node(x, z, lifted=False, name=None):
    """检查站道闸：立柱 + 红白横杆。"""
    arm_deg = 78.0 if lifted else 8.0
    return {'name': name or f'barrier_{x:.1f}',
            'translation': [x, Y0, z],
            'mesh': {'builder': 'cylinder',
                     'args': {'radius': 0.1, 'height': 1.1, 'segments': 8,
                              'color': [0.3, 0.3, 0.32]}},
            'material': _mat([0.30, 0.30, 0.32], 'steel', 0.5, 0.8),
            'children': [
                {'name': f'arm_{x:.1f}', 'translation': [0, 1.0, 0],
                 'rotation': _qz(arm_deg),
                 'mesh': {'builder': 'box',
                          'args': {'sx': 3.6, 'sy': 0.09, 'sz': 0.09,
                                   'color': [0.86, 0.16, 0.12]}},
                 'material': _mat([0.86, 0.16, 0.12], 'plastic_red', 0.4)}]}


def booth_node(x, z, name=None, w=2.4, d=2.2, h=2.6):
    """检查站 / 加油站的岗亭。"""
    return {'name': name or f'booth_{x:.1f}', 'translation': [x, Y0, z],
            'mesh': {'builder': 'box', 'args': {'sx': w, 'sy': h, 'sz': d,
                                                'offset': [0, h / 2, 0],
                                                'color': [0.72, 0.71, 0.66]}},
            'material': _mat([0.72, 0.71, 0.66], 'ceramic', 0.7),
            'children': [
                {'name': f'booth_win_{x:.1f}', 'translation': [0, h * 0.62, d / 2],
                 'mesh': {'builder': 'box',
                          'args': {'sx': w * 0.7, 'sy': h * 0.34, 'sz': 0.06,
                                   'color': [0.10, 0.14, 0.18]}},
                 'material': _mat([0.10, 0.14, 0.18], 'rubber', 0.25, 0.2,
                                  emissive=[0.02, 0.05, 0.07])},
                {'name': f'booth_lamp_{x:.1f}', 'translation': [0, h, 0],
                 'mesh': {'builder': 'box',
                          'args': {'sx': w * 0.5, 'sy': 0.05, 'sz': d * 0.5,
                                   'color': [1.0, 0.92, 0.72]}},
                 'material': _mat([1.0, 0.92, 0.72], 'neon', 0.4, 0.0,
                                  emissive=[1.6, 1.3, 0.7])}]}


def canopy_node(x, z, w=9.0, d=7.0, h=5.2, name=None, color=(0.78, 0.62, 0.24)):
    """加油站雨棚：四柱 + 顶板 + 檐口红条。"""
    kids = []
    for i, sx in enumerate((-1, 1)):
        for j, sz in enumerate((-1, 1)):
            kids.append({'name': f'col_{i}{j}',
                         'translation': [sx * (w / 2 - 0.5), h / 2,
                                         sz * (d / 2 - 0.5)],
                         'mesh': {'builder': 'cylinder',
                                  'args': {'radius': 0.18, 'height': h,
                                           'segments': 8,
                                           'color': [0.72, 0.70, 0.66]}},
                         'material': _mat([0.72, 0.70, 0.66], 'ceramic', 0.7)})
    top = {'name': name or f'canopy_{x:.1f}', 'translation': [x, Y0, z],
           'mesh': {'builder': 'box',
                    'args': {'sx': w, 'sy': 0.42, 'sz': d, 'offset': [0, h, 0],
                             'color': list(color)}},
           'material': _mat(list(color), 'ceramic', 0.6),
           'children': kids + [
               {'name': f'canopy_band_{x:.1f}',
                'translation': [0, h - 0.28, d / 2],
                'mesh': {'builder': 'box',
                         'args': {'sx': w, 'sy': 0.22, 'sz': 0.08,
                                  'color': [0.80, 0.16, 0.12]}},
                'material': _mat([0.80, 0.16, 0.12], 'plastic_red', 0.45)}]}
    return top


def pump_node(x, z, name=None):
    """加油机：机身 + 顶灯 + 油枪管。"""
    return {'name': name or f'pump_{x:.1f}', 'translation': [x, Y0, z],
            'mesh': {'builder': 'box',
                     'args': {'sx': 0.62, 'sy': 1.7, 'sz': 0.5,
                              'offset': [0, 0.85, 0],
                              'color': [0.82, 0.30, 0.18]}},
            'material': _mat([0.82, 0.30, 0.18], 'plastic_red', 0.45),
            'children': [
                {'name': f'pump_head_{x:.1f}', 'translation': [0, 1.78, 0],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.66, 'sy': 0.24, 'sz': 0.54,
                                   'color': [1.0, 0.95, 0.80]}},
                 'material': _mat([1.0, 0.95, 0.80], 'neon', 0.4, 0.0,
                                  emissive=[1.1, 1.0, 0.7])},
                {'name': f'pump_hose_{x:.1f}', 'translation': [0.34, 1.1, 0.1],
                 'rotation': _qz(-38.0),
                 'mesh': {'builder': 'cylinder',
                          'args': {'radius': 0.05, 'height': 0.9, 'segments': 6,
                                   'color': [0.1, 0.1, 0.1]}},
                 'material': _mat([0.10, 0.10, 0.10], 'rubber', 0.9)}]}


def building_node(x, z, w=8.0, d=6.0, h=3.4, color=(0.74, 0.71, 0.65),
                  windows=True, name=None, y=None):
    """便利店 / 办公室 / 养护工区的方形房子。"""
    base = Y0 if y is None else y
    kids = []
    if windows:
        nx = max(1, int(w // 2.4))
        for i in range(nx):
            kids.append({'name': f'win_{i}',
                         'translation': [-w / 2 + w * (i + 0.5) / nx, h * 0.55,
                                         d / 2 + 0.02],
                         'mesh': {'builder': 'box',
                                  'args': {'sx': w / nx * 0.55, 'sy': h * 0.3,
                                           'sz': 0.08,
                                           'color': [0.10, 0.13, 0.16]}},
                         'material': _mat([0.10, 0.13, 0.16], 'rubber', 0.25,
                                          0.2, emissive=[0.03, 0.05, 0.07])})
    return {'name': name or f'bld_{x:.1f}', 'translation': [x, base, z],
            'mesh': {'builder': 'box',
                     'args': {'sx': w, 'sy': h, 'sz': d, 'offset': [0, h / 2, 0],
                              'color': list(color)}},
            'material': _mat(list(color), 'ceramic', 0.75),
            'children': kids}


def signpost_node(x, z, text_h=2.2, name=None, color=(0.14, 0.34, 0.30)):
    """路牌 / 地名标志（本剧的"针"：白墩子、干沟、黑戈壁）。"""
    return {'name': name or f'sign_{x:.1f}', 'translation': [x, Y0, z],
            'mesh': {'builder': 'cylinder',
                     'args': {'radius': 0.07, 'height': text_h, 'segments': 6,
                              'color': [0.42, 0.42, 0.42]}},
            'material': _mat([0.42, 0.42, 0.42], 'steel', 0.5, 0.8),
            'children': [{'name': f'signboard_{x:.1f}',
                          'translation': [0, text_h - 0.35, 0.05],
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 1.7, 'sy': 0.62, 'sz': 0.07,
                                            'color': list(color)}},
                          'material': _mat(list(color), 'ceramic', 0.55)}]}


def mailbox_node(x, z, name=None):
    """十三号信箱：终局的核心道具。"""
    return {'name': name or f'mailbox_{x:.1f}', 'translation': [x, Y0, z],
            'mesh': {'builder': 'box',
                     'args': {'sx': 0.5, 'sy': 1.5, 'sz': 0.5,
                              'offset': [0, 0.75, 0],
                              'color': [0.30, 0.34, 0.32]}},
            'material': _mat([0.30, 0.34, 0.32], 'steel', 0.5, 0.8),
            'children': [
                {'name': f'mbox_body_{x:.1f}', 'translation': [0, 1.62, 0],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.78, 'sy': 0.62, 'sz': 1.05,
                                   'color': [0.16, 0.40, 0.34]}},
                 'material': _mat([0.16, 0.40, 0.34], 'ceramic', 0.5)},
                {'name': f'mbox_flag_{x:.1f}', 'translation': [0.42, 1.72, 0.2],
                 'rotation': _qz(-14.0),
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.05, 'sy': 0.42, 'sz': 0.26,
                                   'color': [0.82, 0.22, 0.16]}},
                 'material': _mat([0.82, 0.22, 0.16], 'plastic_red', 0.45)}]}


def sedan_node(x, z, rot=0.0, color=(0.62, 0.16, 0.16), name=None):
    """私家车 / 小车：简化车体 + 车顶 + 四轮。"""
    car = {'name': name or f'sedan_{x:.1f}', 'translation': [x, Y0 + 0.42, z]}
    if rot:
        car['rotation'] = [0.0, math.sin(rot / 2), 0.0, math.cos(rot / 2)]
    car['mesh'] = {'builder': 'box',
                   'args': {'sx': 4.3, 'sy': 0.62, 'sz': 1.8,
                            'color': list(color)}}
    car['material'] = _mat(list(color), 'ceramic', 0.35)
    kids = [{'name': 'cabin', 'translation': [-0.2, 0.52, 0],
             'mesh': {'builder': 'box',
                      'args': {'sx': 2.2, 'sy': 0.56, 'sz': 1.66,
                               'color': [0.12, 0.14, 0.17]}},
             'material': _mat([0.12, 0.14, 0.17], 'rubber', 0.2, 0.25,
                              emissive=[0.01, 0.02, 0.03])}]
    for dx, dz in ((1.4, 0.85), (1.4, -0.85), (-1.35, 0.85), (-1.35, -0.85)):
        kids.append({'name': f'wheel_{dx}_{dz}', 'translation': [dx, -0.34, dz],
                     'rotation': _qx(90.0),
                     'mesh': {'builder': 'cylinder',
                              'args': {'radius': 0.34, 'height': 0.24,
                                       'segments': 10,
                                       'color': [0.07, 0.07, 0.07]}},
                     'material': _mat([0.07, 0.07, 0.07], 'rubber', 0.92)})
    car['children'] = kids
    return car


def fence_node(x0, x1, z, step=4.0, h=1.5):
    """场院围栏：柱 + 两道横杆。"""
    posts = []
    xs = np.arange(x0, x1 + 0.01, step)
    for i, px in enumerate(xs):
        posts.append({'name': f'fp_{i}', 'translation': [px, Y0, z],
                      'mesh': {'builder': 'cylinder',
                               'args': {'radius': 0.06, 'height': h * 1.3,
                                        'segments': 6,
                                        'color': [0.44, 0.43, 0.40]}},
                      'material': _mat([0.44, 0.43, 0.40], 'steel', 0.6, 0.7)})
    return posts


def room_node(w=6.0, d=5.0, h=2.8, name='room'):
    """内景房间：地板 + 三面墙 + 天花板（相机放在房间内侧）。"""
    wall = {'preset': 'ceramic', 'base_color': [0.62, 0.59, 0.53],
            'roughness': 0.85, 'metallic': 0.0}
    floor = {'preset': 'ceramic', 'base_color': [0.30, 0.28, 0.25],
             'roughness': 0.8, 'metallic': 0.0}
    return {'name': name, 'translation': [0, 0, 0],
            'mesh': {'builder': 'plane',
                     'args': {'width': w, 'depth': d, 'segs': 1,
                              'color': [0.30, 0.28, 0.25]}},
            'material': floor,
            'children': [
                {'name': 'wall_back', 'translation': [0, h / 2, -d / 2],
                 'mesh': {'builder': 'box',
                          'args': {'sx': w, 'sy': h, 'sz': 0.12,
                                   'color': [0.62, 0.59, 0.53]}},
                 'material': wall},
                {'name': 'wall_left', 'translation': [-w / 2, h / 2, 0],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.12, 'sy': h, 'sz': d,
                                   'color': [0.58, 0.55, 0.50]}},
                 'material': wall},
                {'name': 'wall_right', 'translation': [w / 2, h / 2, 0],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.12, 'sy': h, 'sz': d,
                                   'color': [0.58, 0.55, 0.50]}},
                 'material': wall},
                {'name': 'ceiling', 'translation': [0, h, 0],
                 'mesh': {'builder': 'plane',
                          'args': {'width': w, 'depth': d, 'segs': 1,
                                   'color': [0.70, 0.68, 0.64]}},
                 'material': wall},
                {'name': 'ceiling_lamp', 'translation': [0, h - 0.04, 0],
                 'mesh': {'builder': 'plane',
                          'args': {'width': w * 0.5, 'depth': d * 0.35,
                                   'segs': 1,
                                   'color': [1.0, 0.94, 0.80]}},
                 'material': {'preset': 'neon',
                              'base_color': [1.0, 0.94, 0.80],
                              'emissive': [2.2, 1.9, 1.2]}}]}


def desk_node(x=0.0, z=-1.2, name='desk', w=1.9, d=0.9, h=0.76):
    """办公桌 + 台灯 + 纸（'签个字'那个动作需要一个桌面）。"""
    return {'name': name, 'translation': [x, 0, z],
            'mesh': {'builder': 'box',
                     'args': {'sx': w, 'sy': 0.08, 'sz': d,
                              'offset': [0, h, 0],
                              'color': [0.36, 0.26, 0.18]}},
            'material': _mat([0.36, 0.26, 0.18], 'ceramic', 0.6),
            'children': [
                {'name': 'paper', 'translation': [0.25, h + 0.055, 0.1],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.34, 'sy': 0.006, 'sz': 0.46,
                                   'color': [0.92, 0.90, 0.84]}},
                 'material': _mat([0.92, 0.90, 0.84], 'ceramic', 0.5)},
                {'name': 'lamp', 'translation': [-0.7, h + 0.34, -0.2],
                 'mesh': {'builder': 'lathe',
                          'args': {'profile': [[0.0, 0.0], [0.20, 0.06],
                                               [0.24, 0.22], [0.10, 0.30],
                                               [0.0, 0.30]],
                                   'segments': 12, 'color': [0.30, 0.38, 0.32]}},
                 'material': _mat([0.30, 0.38, 0.32], 'ceramic', 0.5,
                                  emissive=[0.55, 0.42, 0.20])}]}


def dashboard_node(name='dash'):
    """驾驶室内景：仪表台 + 方向盘 + 两只座椅 + 挡风玻璃框。"""
    dark = _mat([0.12, 0.12, 0.13], 'rubber', 0.75)
    trim = _mat([0.24, 0.23, 0.22], 'ceramic', 0.7)
    return {'name': name, 'translation': [0, 0, 0],
            'mesh': {'builder': 'box',
                     'args': {'sx': 2.2, 'sy': 0.5, 'sz': 0.6,
                              'offset': [0, 0.55, -0.95],
                              'color': [0.24, 0.23, 0.22]}},
            'material': trim,
            'children': [
                {'name': 'wheel', 'translation': [-0.42, 0.86, -0.62],
                 'rotation': _qx(74.0),
                 'mesh': {'builder': 'torus',
                          'args': {'major': 0.22, 'minor': 0.035,
                                   'major_segs': 18, 'minor_segs': 8,
                                   'color': [0.10, 0.10, 0.11]}},
                 'material': dark},
                {'name': 'cluster', 'translation': [-0.42, 0.86, -1.14],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.52, 'sy': 0.2, 'sz': 0.08,
                                   'color': [0.06, 0.09, 0.12]}},
                 'material': _mat([0.06, 0.09, 0.12], 'rubber', 0.3, 0.1,
                                  emissive=[0.14, 0.30, 0.22])},
                {'name': 'seat_l', 'translation': [-0.45, 0.42, 0.15],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.62, 'sy': 0.86, 'sz': 0.6,
                                   'offset': [0, 0, 0],
                                   'color': [0.20, 0.19, 0.19]}},
                 'material': dark},
                {'name': 'seat_r', 'translation': [0.45, 0.42, 0.15],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 0.62, 'sy': 0.86, 'sz': 0.6,
                                   'color': [0.20, 0.19, 0.19]}},
                 'material': dark},
                {'name': 'glass', 'translation': [0, 1.32, -1.28],
                 'rotation': _qx(-24.0),
                 'mesh': {'builder': 'plane',
                          'args': {'width': 2.3, 'depth': 1.3, 'segs': 1,
                                   'color': [0.55, 0.68, 0.82]}},
                 'material': {'preset': 'ceramic',
                              'base_color': [0.55, 0.68, 0.82],
                              'metallic': 0.0, 'roughness': 0.08,
                              'alpha': 0.28, 'alpha_mode': 'blend',
                              'double_sided': True}},
                {'name': 'bunk', 'translation': [0, 1.5, 1.5],
                 'mesh': {'builder': 'box',
                          'args': {'sx': 2.0, 'sy': 0.16, 'sz': 1.1,
                                   'color': [0.26, 0.25, 0.24]}},
                 'material': dark}]}


# ------------------------------------------------------------- 场景组装 --
def _gobi_base(seed=244, terrain=True):
    """共用的戈壁：地形 + 路面 + 中线 + 里程碑 + 电线杆 + 碎石。"""
    rng = np.random.default_rng(seed)
    hm, _, _ = gobi_heightmap(seed=seed)
    kids = []
    if terrain:
        kids.append({'name': 'gobi',
                     'mesh': {'builder': 'heightfield',
                              'args': {'hm': np.round(hm, 4).tolist(),
                                       'width': 80.0, 'depth': 80.0,
                                       'height': TERRAIN_H, 'y_base': -0.05,
                                       'color': [0.46, 0.38, 0.28]}},
                     'material': _mat([0.46, 0.38, 0.28], 'ceramic', 0.95)})
    kids.append({'name': 'road', 'translation': [0, Y0 + 0.035, 0],
                 'mesh': {'builder': 'plane',
                          'args': {'width': 80.0, 'depth': 7.0, 'segs': 1,
                                   'color': [0.23, 0.22, 0.21]}},
                 'material': _mat([0.23, 0.22, 0.21], 'rubber', 0.92)})
    for i in range(-9, 10):
        kids.append({'name': f'dash_{i}',
                     'translation': [i * 4.0, Y0 + 0.045, 0],
                     'mesh': {'builder': 'plane',
                              'args': {'width': 2.0, 'depth': 0.18, 'segs': 1,
                                       'color': [0.85, 0.82, 0.72]}},
                     'material': _mat([0.85, 0.82, 0.72], 'ceramic', 0.5)})
    kids += [pole_node(float(x), -6.0) for x in range(-36, 37, 8)]
    kids += scatter_rocks(rng, hm)
    return kids, hm


def build_scene(place, tod='day', seed=244, variant=0, **kw):
    """按 地点 + 时间 组装一个可直接渲染的场景 JSON。"""
    key = place_key(place)
    tod = tod if tod in TOD else tod_of(tod)
    env = dict(TOD[tod])
    exposure = env.pop('exposure')
    # 每 · variant 让稀疏散布略有不同，避免同一地点多镜头完全雷同
    base_kids, hm = _gobi_base(seed=seed + variant * 17)
    kids = list(base_kids)
    truck = True

    if key == 'road':
        kids += [milestone_node(11.0, 4.4), milestone_node(-17.0, 4.4),
                 truck_node(x=-6.0 + variant * 3.0, z=1.8,
                            rot=math.radians(-6))]
    elif key == 'roadside':
        kids += [signpost_node(-9.5, 5.0), milestone_node(6.0, 4.4),
                 truck_node(x=-4.0, z=-2.4, rot=math.radians(4))]
    elif key == 'yard':
        kids += [truck_node(x=-2.0, z=2.6, rot=math.radians(8)),
                 truck_node(x=5.0, z=3.4, rot=math.radians(-14)),
                 truck_node(x=11.0, z=2.2, rot=math.radians(24)),
                 building_node(-14.0, -11.0, w=10.0, d=7.0, h=3.6),
                 *fence_node(-24.0, 24.0, -9.0),
                 *fence_node(-24.0, 24.0, 11.0),
                 crate_node(-6.0, 6.0), crate_node(-6.9, 6.3, scale=0.8)]
    elif key == 'dock':
        kids += [_plate(-2.0, Y0 + 0.5, 5.2, 7.0, 1.0, 3.2,
                        [0.52, 0.50, 0.46], 'ceramic', 0.85),
                 truck_node(x=-2.0, z=1.6, rot=math.radians(2)),
                 crate_node(1.6, 5.0, y=Y0 + 1.0, lid=True),
                 _plate(-6.2, Y0 + 0.9, 5.2, 0.3, 1.8, 3.0,
                        [0.44, 0.43, 0.40], 'steel', 0.6, 0.6),
                 building_node(9.0, 9.0, w=9.0, d=7.0, h=4.0)]
    elif key == 'gas':
        kids += [canopy_node(0.0, 7.0, w=12.0, d=8.0),
                 pump_node(-2.6, 6.4), pump_node(2.6, 6.4),
                 building_node(11.0, 10.0, w=9.0, d=7.0, h=3.4),
                 signpost_node(-12.0, 5.4, color=(0.16, 0.36, 0.32)),
                 truck_node(x=-5.5, z=0.0, rot=math.radians(3))]
    elif key == 'service':
        kids += [canopy_node(-3.0, 8.0, w=10.0, d=7.0, h=5.0,
                             color=(0.68, 0.66, 0.60)),
                 building_node(9.5, 10.5, w=11.0, d=8.0, h=3.6),
                 pump_node(-6.0, 7.6), pump_node(-0.5, 7.6),
                 _plate(9.5, Y0 + 0.12, 6.6, 12.0, 0.24, 1.8,
                        [0.62, 0.60, 0.56], 'ceramic', 0.8),
                 truck_node(x=-6.0, z=0.5, rot=math.radians(-2))]
    elif key == 'checkpoint':
        kids += [booth_node(6.0, 5.2), barrier_node(-3.6, 4.6),
                 barrier_node(3.6, 4.6, lifted=True),
                 signpost_node(-11.0, 5.0, color=(0.14, 0.30, 0.42)),
                 truck_node(x=-8.0, z=0.2, rot=math.radians(1))]
    elif key == 'ruin':
        kids += [canopy_node(0.0, 6.0, w=11.0, d=7.0, h=4.4,
                             color=(0.56, 0.52, 0.45)),
                 building_node(12.0, 9.0, w=9.0, d=6.5, h=3.2,
                               color=(0.60, 0.57, 0.52), windows=False),
                 barrel_node(4.5, 8.5, (0.30, 0.28, 0.20)),
                 barrel_node(5.4, 8.9, (0.24, 0.26, 0.22)),
                 crate_node(-7.0, 7.6, scale=0.9),
                 truck_node(x=-5.0, z=0.8, rot=math.radians(-4))]
    elif key == 'mailbox':
        kids += [mailbox_node(2.0, 3.2), milestone_node(-6.0, 4.4),
                 signpost_node(-13.0, 4.6, color=(0.16, 0.36, 0.32)),
                 truck_node(x=-11.0, z=0.6, rot=math.radians(2))]
    elif key == 'overlook':
        kids += [_plate(0.0, Y0 + 0.4, 9.0, 12.0, 0.8, 6.0,
                        [0.58, 0.55, 0.50], 'ceramic', 0.85),
                 milestone_node(-8.0, 4.4), signpost_node(9.0, 9.2),
                 truck_node(x=-4.0, z=1.0, rot=math.radians(6))]
    elif key == 'twocars':
        kids += [truck_node(x=-3.0, z=1.6, rot=math.radians(-3)),
                 sedan_node(x=6.0, z=-2.2, rot=math.radians(12)),
                 milestone_node(-14.0, 4.4)]
    elif key == 'tail':
        kids += [truck_node(x=0.0, z=0.0, rot=math.radians(-2)),
                 crate_node(4.2, 2.4, lid=True), crate_node(5.0, 3.1, scale=0.8),
                 milestone_node(-13.0, 4.4)]
    elif key == 'front':
        kids += [truck_node(x=0.0, z=0.0, rot=math.radians(2)),
                 barrel_node(-5.0, 2.2), milestone_node(12.0, 4.4)]
    elif key in ('cab', 'boxbody', 'office'):
        truck = False
        kids = []
        if key == 'cab':
            kids += [dashboard_node(),
                     {'name': 'cab_floor', 'translation': [0, 0.02, 0],
                      'mesh': {'builder': 'plane',
                               'args': {'width': 2.4, 'depth': 3.4, 'segs': 1,
                                        'color': [0.16, 0.15, 0.14]}},
                      'material': _mat([0.16, 0.15, 0.14], 'rubber', 0.9)},
                     {'name': 'cab_ceil', 'translation': [0, 1.86, 0],
                      'mesh': {'builder': 'plane',
                               'args': {'width': 2.4, 'depth': 3.4, 'segs': 1,
                                        'color': [0.34, 0.33, 0.31]}},
                      'material': _mat([0.34, 0.33, 0.31], 'ceramic', 0.85)},
                     {'name': 'sleepcab_bg', 'translation': [0, 1.0, 3.6],
                      'mesh': {'builder': 'box',
                               'args': {'sx': 2.4, 'sy': 1.9, 'sz': 0.12,
                                        'color': [0.22, 0.21, 0.20]}},
                      'material': _mat([0.22, 0.21, 0.20], 'ceramic', 0.8)}]
        elif key == 'boxbody':
            kids += [{'name': 'box_floor', 'translation': [0, Y0 + 0.02, 0],
                      'mesh': {'builder': 'plane',
                               'args': {'width': 4.2, 'depth': 7.0, 'segs': 1,
                                        'color': [0.28, 0.25, 0.20]}},
                      'material': _mat([0.28, 0.25, 0.20], 'ceramic', 0.9)},
                     crate_node(-1.2, 1.4, y=Y0), crate_node(1.1, 2.2, y=Y0),
                     crate_node(0.2, -1.8, y=Y0, scale=0.85),
                     {'name': 'tarp_roof', 'translation': [0, Y0 + 2.2, 0],
                      'mesh': {'builder': 'plane',
                               'args': {'width': 4.2, 'depth': 7.0, 'segs': 1,
                                        'color': [0.34, 0.30, 0.24]}},
                      'material': _mat([0.34, 0.30, 0.24], 'rubber', 0.95)}]
        else:  # office
            kids += [room_node(w=6.0, d=5.0, h=2.8), desk_node(0.0, -1.4),
                     {'name': 'chair', 'translation': [0, 0.46, 0.1],
                      'mesh': {'builder': 'box',
                               'args': {'sx': 0.55, 'sy': 0.92, 'sz': 0.55,
                                        'color': [0.24, 0.22, 0.20]}},
                      'material': _mat([0.24, 0.22, 0.20], 'rubber', 0.85)},
                     {'name': 'cabinet', 'translation': [-2.4, 0.9, -1.8],
                      'mesh': {'builder': 'box',
                               'args': {'sx': 1.0, 'sy': 1.8, 'sz': 0.5,
                                        'color': [0.40, 0.38, 0.34]}},
                      'material': _mat([0.40, 0.38, 0.34], 'ceramic', 0.7)}]

    scene = {'name': f'l100-{key}-{tod}-{variant}',
             'environment': env,
             'root': {'name': 'root', 'children': kids}}
    if key in ('cab', 'boxbody', 'office'):
        # 内景：环境光来自天空球，夜景会闷死 —— 提曝光 + 提环境
        if tod == 'night':
            exposure += 0.85
            env['sky_intensity'] = min(env.get('sky_intensity', 1.0) + 0.35, 1.1)
            env['ibl_intensity'] = min(env.get('ibl_intensity', 1.0) + 0.25, 1.0)
        elif tod in ('dusk', 'dawn'):
            exposure += 0.35
    scene['_exposure'] = exposure
    scene['_place'] = key
    scene['_tod'] = tod
    scene['_has_truck'] = truck
    return scene


# --------------------------------------------------------------- 构图 --
def pose(name, place='road', variant=0):
    """返回一个构图：(position, target, fov)。"""
    interior = place in ('cab', 'office')     # 房/车厢：地面在 y=0
    ind = 0.0 if interior else Y0
    if place == 'cab':
        return {
            'two_shot':   ([0.0, 1.45, 3.2], [-0.2, 1.15, -0.9], 44.0),
            'driver':     ([-1.0, 1.35, 1.1], [-0.45, 1.05, -0.9], 38.0),
            'passenger':  ([1.05, 1.35, 1.1], [0.45, 1.05, -0.9], 38.0),
            'thru_glass': ([0.0, 1.42, -0.2], [0.0, 1.5, -12.0], 46.0),
            'cluster':    ([-0.62, 1.05, 0.05], [-0.42, 0.86, -1.14], 26.0),
        }[name]
    if place == 'boxbody':
        return {
            'two_shot':   ([0.0, ind + 1.6, 4.6], [0.0, ind + 0.8, 0.0], 46.0),
            'close_crate': ([1.6, ind + 1.1, 2.2], [0.2, ind + 0.5, -0.4], 34.0),
            'wide':       ([0.0, ind + 2.0, 6.0], [0.0, ind + 0.6, -2.0], 52.0),
            'low':        ([0.0, ind + 0.5, 3.0], [0.0, ind + 0.7, -1.0], 40.0),
        }[name]
    if place == 'office':
        return {
            'two_shot':   ([0.0, 1.45, 2.6], [0.0, 1.05, -1.2], 44.0),
            'desk':       ([0.5, 1.42, 1.1], [0.0, 0.95, -1.4], 36.0),
            'paper':      ([0.55, 1.30, 0.4], [0.15, 0.88, -1.35], 30.0),
            'wide':       ([0.0, 1.7, 2.2], [0.0, 0.9, -2.0], 50.0),
        }[name]

    v = variant * 2.5
    P = {
        'establish': ([-30.0, ind + 11.0, 22.0 + v], [-2.0, ind + 1.2, 0.0], 42.0),
        'long_road': ([-24.0, ind + 2.2, 5.0], [16.0, ind + 1.4, 1.0], 38.0),
        'low_marker': ([6.0, ind + 1.05, 6.4], [4.0, ind + 0.75, 4.4], 34.0),
        'truck_side': ([-9.5, ind + 1.9, 6.0], [-4.0, ind + 1.5, 1.6], 40.0),
        'truck_front': ([-14.0, ind + 1.5, 1.4], [-3.0, ind + 1.5, 1.6], 36.0),
        'truck_three_q': ([-10.5, ind + 2.6, -4.2], [-4.0, ind + 1.6, 1.8], 38.0),
        'close_detail': ([-5.4, ind + 0.85, 3.2], [-4.4, ind + 0.7, 2.4], 28.0),
        'overhead': ([0.0, ind + 16.0, 12.0], [0.0, ind + 0.5, 0.0], 48.0),
        'rear_view': ([10.0, ind + 1.5, 4.5], [-6.0, ind + 1.3, 1.2], 36.0),
        'horizon': ([-18.0, ind + 1.25, 9.0], [8.0, ind + 1.5, 2.0], 46.0),
        'pov_windshield': ([-2.6, ind + 1.9, 2.0], [14.0, ind + 1.3, 1.2], 48.0),
        'sign_detail': ([-11.6, ind + 1.7, 6.2], [-9.5, ind + 1.9, 5.0], 30.0),
        'two_cars': ([-11.0, ind + 2.4, 7.0], [0.5, ind + 1.2, -0.4], 44.0),
        'dock_wide': ([-13.0, ind + 3.4, 12.0], [-1.0, ind + 1.2, 3.0], 44.0),
        'mailbox_close': ([3.4, ind + 1.5, 5.4], [2.0, ind + 1.5, 3.2], 32.0),
    }
    return P[name]


# 每个地点默认用哪几个构图（2~3 个足够撑一场戏）
PLACE_POSES = {
    'road': ['establish', 'long_road', 'low_marker'],
    'roadside': ['long_road', 'sign_detail', 'rear_view'],
    'yard': ['establish', 'truck_side', 'close_detail'],
    'dock': ['dock_wide', 'truck_three_q', 'establish'],
    'gas': ['establish', 'truck_side', 'close_detail'],
    'service': ['establish', 'truck_three_q', 'close_detail'],
    'checkpoint': ['long_road', 'truck_front', 'sign_detail'],
    'ruin': ['establish', 'truck_side', 'close_detail'],
    'mailbox': ['horizon', 'mailbox_close', 'long_road'],
    'overlook': ['establish', 'horizon', 'long_road'],
    'twocars': ['two_cars', 'establish', 'close_detail'],
    'tail': ['truck_side', 'close_detail', 'long_road'],
    'front': ['truck_front', 'close_detail', 'low_marker'],
    'cab': ['two_shot', 'driver', 'passenger'],
    'boxbody': ['wide', 'close_crate', 'two_shot'],
    'office': ['wide', 'two_shot', 'desk'],
}


def poses_for(scene_or_place, n=3):
    key = scene_or_place if isinstance(scene_or_place, str) else \
        scene_or_place.get('_place', 'road')
    names = PLACE_POSES.get(key, PLACE_POSES['road'])
    return names[:n]

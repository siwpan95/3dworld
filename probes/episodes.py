# -*- coding: utf-8 -*-
"""《最后一百公里》002-010 集关键镜头批量出片。

复用 gobi_scene 的戈壁世界（worldbuilder 走廊 + 3dworld heightfield），
每集换一套 props + 运镜 + 环境光，各自出一段 8 秒 mp4。
对应连续性台账的时间线与"每集一个视觉锚点"原则。

用法::

    python probes/episodes.py                 # 全部 002-010
    python probes/episodes.py 006 008         # 只出指定集
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from gobi_scene import (Y0, build_scene as gobi_scene_base, milestone_node,
                        pole_node, truck_node)

ENV_DAY = {}
ENV_DUSK = {'sun_dir': [0.8, 0.12, -0.4], 'sun_color': [1.0, 0.62, 0.35],
            'sun_intensity': 1.1, 'sky_zenith': [0.16, 0.26, 0.48],
            'sky_horizon': [0.75, 0.48, 0.30], 'sky_ground': [0.20, 0.16, 0.13],
            'ibl_intensity': 0.5, 'sky_intensity': 0.7}
ENV_NIGHT = {'sun_dir': [-0.35, 0.65, 0.35], 'sun_color': [0.55, 0.65, 0.95],
             'sun_intensity': 0.75, 'sky_zenith': [0.05, 0.09, 0.22],
             'sky_horizon': [0.13, 0.17, 0.30], 'sky_ground': [0.05, 0.05, 0.07],
             'ibl_intensity': 0.55, 'sky_intensity': 0.55}


# ------------------------------------------------------------- 道具库 --
def crate_node(x, z, lid=False):
    """铁皮运单箱（0.9m 立方 + 铅封条）。"""
    n = {'name': f'crate_{x:.0f}_{z:.0f}', 'translation': [x, Y0 + 0.45, z],
         'mesh': {'builder': 'box', 'args': {'sx': 0.9, 'sy': 0.9, 'sz': 0.7,
                                             'color': [0.36, 0.35, 0.33]}},
         'material': {'preset': 'steel', 'base_color': [0.36, 0.35, 0.33],
                      'roughness': 0.55}}
    if lid:
        n['children'] = [{'name': f'crate_seal_{x:.0f}',
                          'translation': [0, 0.32, 0],
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 0.94, 'sy': 0.06, 'sz': 0.1,
                                            'color': [0.75, 0.2, 0.12]}},
                          'material': {'preset': 'plastic_red',
                                       'base_color': [0.75, 0.2, 0.12],
                                       'roughness': 0.4}}]
    return n


def barrel_node(x, z, color=(0.20, 0.32, 0.20)):
    """油桶：lathe 带箍。"""
    return {'name': f'barrel_{x:.0f}_{z:.0f}', 'translation': [x, Y0 + 0.42, z],
            'mesh': {'builder': 'lathe',
                     'args': {'profile': [[0.0, -0.42], [0.28, -0.42],
                                          [0.32, -0.36], [0.32, -0.10],
                                          [0.34, -0.06], [0.34, 0.06],
                                          [0.32, 0.10], [0.32, 0.36],
                                          [0.28, 0.42], [0.0, 0.42]],
                              'segments': 14, 'color': list(color)}},
            'material': {'preset': 'steel', 'base_color': list(color),
                         'roughness': 0.45}}


def barrier_node(x, z):
    """检查站拦车道闸：立柱 + 红白横杆。"""
    return {'name': f'barrier_{x:.0f}', 'translation': [x, Y0, z],
            'mesh': {'builder': 'cylinder', 'args': {'radius': 0.1, 'height': 1.1,
                                                     'segments': 8,
                                                     'color': [0.3, 0.3, 0.32]}},
            'material': {'preset': 'steel', 'roughness': 0.5},
            'children': [{'name': f'barrier_arm_{x:.0f}',
                          'translation': [0, 1.0, 0], 'rotation': _QZ(8.0),
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 3.6, 'sy': 0.09, 'sz': 0.09,
                                            'color': [0.85, 0.3, 0.2]}},
                          'material': {'preset': 'plastic_red',
                                       'base_color': [0.85, 0.3, 0.2],
                                       'roughness': 0.4}}]}


def booth_node(x, z):
    """检查站登记亭（蓝色小屋 + 窗口）。"""
    return {'name': f'booth_{x:.0f}', 'translation': [x, Y0 + 1.2, z],
            'mesh': {'builder': 'box', 'args': {'sx': 1.8, 'sy': 2.4, 'sz': 1.8,
                                                'color': [0.25, 0.35, 0.5]}},
            'material': {'preset': 'ceramic', 'base_color': [0.25, 0.35, 0.5],
                         'roughness': 0.7},
            'children': [{'name': f'booth_roof_{x:.0f}',
                          'translation': [0, 1.3, 0],
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 2.2, 'sy': 0.12, 'sz': 2.2,
                                            'color': [0.85, 0.85, 0.82]}},
                          'material': {'preset': 'ceramic',
                                       'base_color': [0.85, 0.85, 0.82]}}]}


def canopy_node(x, z, w=10.0):
    """废弃服务区凉棚：四根柱子 + 顶板（干沟）。"""
    posts = []
    for px in (-w / 2, w / 2):
        for pz in (-2.5, 2.5):
            posts.append({'name': f'canopy_post_{px:.0f}_{pz:.0f}',
                          'translation': [x + px, Y0 + 2.2, z + pz],
                          'mesh': {'builder': 'cylinder',
                                   'args': {'radius': 0.15, 'height': 4.4,
                                            'segments': 8,
                                            'color': [0.35, 0.33, 0.30]}},
                          'material': {'preset': 'ceramic',
                                       'base_color': [0.35, 0.33, 0.30]}})
    roof = {'name': 'canopy_roof', 'translation': [x, Y0 + 4.5, z],
            'mesh': {'builder': 'box', 'args': {'sx': w + 0.6, 'sy': 0.25,
                                                'sz': 6.0,
                                                'color': [0.62, 0.60, 0.55]}},
            'material': {'preset': 'ceramic', 'base_color': [0.62, 0.60, 0.55],
                         'roughness': 0.8}}
    return {'name': 'canopy', 'children': posts + [roof]}


def spare_tire_node(x, z):
    """靠在货箱边的备胎（torus 斜靠）。"""
    return {'name': f'spare_{x:.0f}', 'translation': [x, Y0 + 0.5, z],
            'rotation': [0.9659, 0.2588, 0.0, 0.0],   # 后仰 30°
            'mesh': {'builder': 'torus',
                     'args': {'major': 0.30, 'minor': 0.15, 'major_segs': 16,
                              'minor_segs': 8, 'color': [0.08, 0.08, 0.09]}},
            'material': {'preset': 'rubber', 'roughness': 0.95}}


def gas_pump_node(x, z):
    """老式加油机（立柜 + 顶灯箱）。"""
    return {'name': f'pump_{x:.0f}', 'translation': [x, Y0 + 0.7, z],
            'mesh': {'builder': 'box', 'args': {'sx': 0.7, 'sy': 1.4, 'sz': 0.5,
                                                'color': [0.75, 0.72, 0.65]}},
            'material': {'preset': 'ceramic', 'base_color': [0.75, 0.72, 0.65],
                         'roughness': 0.6},
            'children': [{'name': f'pump_top_{x:.0f}',
                          'translation': [0, 0.85, 0],
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 0.8, 'sy': 0.5, 'sz': 0.55,
                                            'color': [0.75, 0.25, 0.15]}},
                          'material': {'preset': 'plastic_red',
                                       'base_color': [0.75, 0.25, 0.15],
                                       'roughness': 0.4}}]}


def _QZ(deg):
    r = math.radians(deg) / 2
    return [math.cos(r), 0.0, 0.0, math.sin(r)]


def _shot(name, duration, positions, targets, fovs=None, acts=None, notes=''):
    """运镜 JSON 快捷构造：positions/targets 是关键帧列表，时间自动均匀分布。"""
    from world3d.engine.shot import Shot, CAMERA_NODE
    n = len(positions)
    times = [duration * i / (n - 1) for i in range(n)]
    s = Shot(name=name, duration=duration, acts=acts or [], notes=notes)
    s.add_channel(CAMERA_NODE, 'position', times, positions)
    s.add_channel(CAMERA_NODE, 'target', times, targets)
    if fovs:
        ftimes = [duration * i / (len(fovs) - 1) for i in range(len(fovs))]
        s.add_channel(CAMERA_NODE, 'fov', ftimes, [f[0] if isinstance(f, (list, tuple)) else f for f in fovs])
    return s


# ------------------------------------------------------------- 各集定义 --
# props(sc) -> 节点列表；shot() -> Shot；env -> 环境光覆盖
EPISODES = {
    '002': dict(
        title='小白地（装错车）',
        env=ENV_DAY,
        props=lambda sc: [crate_node(6.0, 2.2, lid=True),
                          crate_node(7.2, 2.0, lid=True),
                          crate_node(6.6, 3.4),
                          truck_node(x=2.0, z=-1.6, rot=math.radians(-4))],
        shot=lambda: _shot(
            'ep002_crates', 8.0,
            ([10.0, Y0 + 1.4, 6.0], [3.0, Y0 + 1.2, 1.0], [1.0, Y0 + 1.6, 3.0]),
            ([6.5, Y0 + 0.8, 2.5], [5.0, Y0 + 0.9, 2.0], [-4.0, Y0 + 1.4, -1.6]),
            ([36.0], [40.0]),
            acts=[{'t': 0.0, 'label': '两只箱子'}, {'t': 4.0, 'label': '装错的车'}],
            notes='002：箱子并排 + 车在旁。钩子：其中一只上错了车'),
    ),
    '003': dict(
        title='检查站',
        env=ENV_DAY,
        props=lambda sc: [booth_node(4.0, 6.5), barrier_node(4.0, 0.0),
                          truck_node(x=-6.0, z=-1.8, rot=math.radians(2))],
        shot=lambda: _shot(
            'ep003_checkpoint', 8.0,
            ([8.0, Y0 + 2.2, -5.0], [6.0, Y0 + 1.8, 3.5], [0.5, Y0 + 1.5, 6.0]),
            ([3.0, Y0 + 1.6, 2.0], [4.0, Y0 + 1.5, 6.0], [4.0, Y0 + 1.3, 6.5]),
            ([38.0], [34.0]),
            acts=[{'t': 0.0, 'label': '接近道闸'}, {'t': 4.0, 'label': '登记窗口'}],
            notes='003：道闸放横，车停在杆前。登记亭是唯一的"机构面孔"'),
    ),
    '004': dict(
        title='九十八公里（爆胎）',
        env=ENV_DAY,
        props=lambda sc: [spare_tire_node(-8.5, -2.6),
                          truck_node(x=-10.0, z=-1.8, rot=math.radians(6))],
        shot=lambda: _shot(
            'ep004_flat', 8.0,
            ([-2.0, Y0 + 1.0, 5.5], [-6.5, Y0 + 1.3, 4.0], [-13.0, Y0 + 2.0, 6.5]),
            ([-9.5, Y0 + 0.8, -2.0], [-11.0, Y0 + 0.9, -2.4], [-12.0, Y0 + 1.2, -2.0]),
            ([36.0], [42.0]),
            acts=[{'t': 0.0, 'label': '路肩'}, {'t': 4.0, 'label': '备胎'}],
            notes='004：车停路肩，备胎斜靠。代价：花时间'),
    ),
    '005': dict(
        title='干沟（废弃服务区）',
        env=ENV_DUSK,
        props=lambda sc: [canopy_node(0.0, 6.0),
                          truck_node(x=0.0, z=-1.5, rot=math.radians(-2))],
        shot=lambda: _shot(
            'ep005_gangou', 8.0,
            ([14.0, Y0 + 2.4, 14.0], [6.0, Y0 + 2.0, 10.0], [0.0, Y0 + 1.6, 12.0]),
            ([0.0, Y0 + 2.5, 6.0], [0.0, Y0 + 2.2, 6.0], [0.0, Y0 + 1.6, 5.5]),
            ([34.0], [40.0]),
            acts=[{'t': 0.0, 'label': '凉棚剪影'}, {'t': 4.0, 'label': '空场'}],
            notes='005：黄昏，废弃凉棚。看站人的一天才一单'),
    ),
    '006': dict(
        title='夜路',
        env=ENV_NIGHT,
        exposure=1.6,
        props=lambda sc: [truck_node(x=0.0, z=0.0, rot=0.0)],
        shot=lambda: _shot(
            'ep006_night', 8.0,
            ([-16.0, Y0 + 1.4, 5.0], [-8.0, Y0 + 1.5, 4.2], [-2.0, Y0 + 1.6, 5.5]),
            ([20.0, Y0 + 1.1, 0.4], [16.0, Y0 + 1.2, 0.4], [24.0, Y0 + 1.3, 0.5]),
            ([30.0], [36.0]),
            acts=[{'t': 0.0, 'label': '车灯前的路'}, {'t': 4.0, 'label': '夜里只有这台车'}],
            notes='006：夜。车灯是唯一光源方向（v1 用暗环境近似）'),
    ),
    '007': dict(
        title='四个小时（等待）',
        env=ENV_DUSK,
        props=lambda sc: [truck_node(x=0.0, z=-1.2, rot=math.radians(-3)),
                          barrel_node(5.5, 2.4)],
        shot=lambda: _shot(
            'ep007_wait', 8.0,
            ([9.0, Y0 + 3.2, 7.0], [4.0, Y0 + 2.0, 5.0], [-2.0, Y0 + 1.4, 4.0]),
            ([0.0, Y0 + 1.4, -1.2], [0.0, Y0 + 1.5, -1.2], [0.0, Y0 + 1.6, -1.2]),
            ([36.0], [44.0]),
            acts=[{'t': 0.0, 'label': '黄昏等待'}, {'t': 4.0, 'label': '推近驾驶室'}],
            notes='007：等待的四小时。黄昏把车影拉长'),
    ),
    '008': dict(
        title='封条',
        env=ENV_DAY,
        props=lambda sc: [crate_node(-4.0, 3.2, lid=True),
                          truck_node(x=-6.0, z=-1.5, rot=math.radians(4))],
        shot=lambda: _shot(
            'ep008_seal', 8.0,
            ([-0.5, Y0 + 1.1, 6.2], [-2.6, Y0 + 1.0, 4.6], [-4.0, Y0 + 1.2, 4.2]),
            ([-4.0, Y0 + 0.7, 3.2], [-4.0, Y0 + 0.8, 3.2], [-4.0, Y0 + 0.9, 3.2]),
            ([34.0], [30.0]),
            acts=[{'t': 0.0, 'label': '挡泥板上的箱子'}, {'t': 4.0, 'label': '铅封'}],
            notes='008：箱子搁在挡泥板上，红封条可见。规则：到终点前不开'),
    ),
    '009': dict(
        title='六块钱一升（油老头）',
        env=ENV_DAY,
        props=lambda sc: [barrel_node(3.0, 3.0), barrel_node(3.8, 3.4, (0.32, 0.30, 0.12)),
                          barrel_node(2.4, 3.6, (0.28, 0.26, 0.22)),
                          truck_node(x=-6.0, z=-1.6, rot=math.radians(-5))],
        shot=lambda: _shot(
            'ep009_fuel', 8.0,
            ([8.0, Y0 + 1.2, 6.5], [5.0, Y0 + 1.0, 4.6], [2.8, Y0 + 0.9, 4.0]),
            ([2.5, Y0 + 0.6, 3.2], [3.0, Y0 + 0.6, 3.2], [3.2, Y0 + 0.7, 3.2]),
            ([38.0], [32.0]),
            acts=[{'t': 0.0, 'label': '三只桶'}, {'t': 4.0, 'label': '六块钱一升'}],
            notes='009：散装油老头的三只桶。今天它在'),
    ),
    '010': dict(
        title='黑戈壁（加油站）',
        env=ENV_DAY,
        props=lambda sc: [canopy_node(2.0, 7.0, w=12.0),
                          gas_pump_node(0.0, 5.0), gas_pump_node(4.0, 5.0),
                          truck_node(x=-5.0, z=0.0, rot=math.radians(2))],
        shot=lambda: _shot(
            'ep010_gas', 8.0,
            ([12.0, Y0 + 2.6, 12.0], [5.0, Y0 + 2.2, 9.0], [-1.0, Y0 + 1.6, 8.0]),
            ([2.0, Y0 + 2.0, 6.5], [2.0, Y0 + 1.8, 6.0], [0.0, Y0 + 1.5, 5.0]),
            ([36.0], [42.0]),
            acts=[{'t': 0.0, 'label': '黑戈壁加油站'}, {'t': 4.0, 'label': '加油机'}],
            notes='010：终点前最后一个有人处。发票没人帮扫'),
    ),
}


def build_episode_scene(ep):
    base = gobi_scene_base()
    spec = EPISODES[ep]
    base['name'] = f'l100-ep{ep}'
    base['environment'].update(spec['env'])
    base['root']['children'].extend(spec['props'](base))
    return base


def main():
    from gobi_scene import render
    which = [a for a in sys.argv[1:] if a in EPISODES] or list(EPISODES)
    out_root = os.path.join(ROOT, 'probes', 'out', 'episodes')
    os.makedirs(out_root, exist_ok=True)
    for ep in which:
        spec = EPISODES[ep]
        scene = build_episode_scene(ep)
        from world3d.engine.scene_build import validate
        errs = [i for i in validate(scene) if i[0] == 'error']
        if errs:
            print(f'[ep{ep}] SKIP, {len(errs)} errors:', errs[:3])
            continue
        shot = spec['shot']()
        out = os.path.join(out_root, f'ep{ep}')
        print(f'=== ep{ep} {spec["title"]} ===')
        render(scene, shot, out, fps=24, size=(960, 540),
               exposure=spec.get('exposure', 0.90))


if __name__ == '__main__':
    main()

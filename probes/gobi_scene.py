# -*- coding: utf-8 -*-
"""《最后一百公里》场景生成器：S244 戈壁公路（worldbuilder -> 3dworld）。

worldbuilder 负责"世界"（分形噪声戈壁高度图 + 公路走廊压平 + 碎石散布），
3dworld 负责"拍摄"（heightfield 渲染 + Shot 运镜 + 离屏出片）。

生成的场景元素对应第 001 集《白墩子》的标志性元素：
- 一条直得发愣的戈壁公路（路面 + 路肩 + 中线虚段）
- 白墩子：里程碑（白色短柱，公路剧的"针"）
- 电线杆排（唯一的垂直线条，跟着路走）
- 卡车（三主角的"家"：驾驶室 + 货箱 + 油桶）
- 远山剪影 + 荒滩碎石

用法::

    python probes/gobi_scene.py --json out/gobi_scene.json   # 只生成场景 JSON
    python probes/gobi_scene.py --render                     # 生成 + 出一段 mp4
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np                                              # noqa: E402

ROAD_HM = 0.30            # 走廊高度图基准值
TERRAIN_H = 7.0           # heightfield 高度
Y0 = ROAD_HM * TERRAIN_H  # 路面高程（world y）
# 向量化分形噪声：本仓自研（按「复制优于共享」自 worldbuilder.noise 复制，
# 使 3dworld 不 import worldbuilder —— 三仓仅以 worldspec v1 文件契约为集成点）。
from world3d.logic.noise_vec import fractal_noise              # noqa: E402


# --------------------------------------------------------------- 地形 --
def gobi_heightmap(rows=96, cols=96, seed=244, road_half=2.6):
    """戈壁高度图：S244 走廊。世界坐标 x∈[-40,40]（沿路），z∈[-40,40]。

    - 基底：低幅 fbm 起伏（荒滩的"皱"）
    - 远山：|z| 越大越高，叠加一层大尺度噪声做山脊
    - 公路走廊：z 近 0 的条带压平（路基），并微微抬高（路基高于地面）
    """
    xw = np.linspace(-40, 40, cols)
    zw = np.linspace(-40, 40, rows)
    xm, zm = np.meshgrid(xw, zw)                     # zm: 行方向（-z..+z）
    base = fractal_noise(xm * 0.06, zm * 0.06, octaves=4, seed=seed) * 0.18
    detail = fractal_noise(xm * 0.35, zm * 0.35, octaves=3, seed=seed + 1) * 0.05
    far = np.abs(zm) / 40.0
    ridged = 1.0 - np.abs(fractal_noise(xm * 0.045, zm * 0.045, octaves=3,
                                        seed=seed + 2) * 2.0 - 1.0)
    mountains = np.clip(far - 0.45, 0, 1) ** 1.6 * ridged * 1.9
    hm = base + detail + mountains
    # 公路走廊：|z| < road_half 压平到路肩高度，边缘 smooth 过渡
    shoulder = road_half + 3.0
    t = np.clip((np.abs(zm) - road_half) / (shoulder - road_half), 0, 1)
    smooth = t * t * (3 - 2 * t)
    road_level = 0.30
    hm = hm * smooth + road_level * (1 - smooth)
    return (hm.astype(np.float32), xw, zw)


# --------------------------------------------------------------- 场景 --
_QX90 = [0.7071068, 0.7071068, 0.0, 0.0]      # 绕 X 轴 90°（车轴沿 Z）
_QZ90 = [0.7071068, 0.0, 0.0, 0.7071068]      # 绕 Z 轴 90°（轴向沿 X）


def truck_node(x=0.0, z=0.0, rot=0.0, scale=1.0, name='truck'):
    """三主角的卡车：方块 previz → lathe/tube 拼装版。

    保留与 previz 版相同的**外围 bounding**（轮胎外缘 r=0.45、轴距位置一致），
    所以既有运镜 JSON 无需改动仍能框住车。头朝 -x（路的前方）。

    升级处：
    - 轮胎：``torus``（真实圆环形截面，不再是圆柱）
    - 轮毂/油箱/排气管：``lathe`` 旋转体（曲面，带倒角）
    - 保险杠/扶手：``tube`` 沿路径扫掠（真弯管，不是方块条）
    """
    s = scale
    body = [0.55, 0.28, 0.12]                     # 驾驶室漆面
    box_col = [0.45, 0.30, 0.16]                  # 货箱篷布色

    cab = {'name': f'{name}_cab', 'translation': [x - 1.6 * s, Y0 + 1.15 * s, z],
           'mesh': {'builder': 'box', 'args': {'sx': 2.6 * s, 'sy': 1.5 * s,
                                               'sz': 2.3 * s, 'color': body}},
           'material': {'preset': 'plastic_red', 'base_color': body,
                        'roughness': 0.45}}
    box_ = {'name': f'{name}_box', 'translation': [x + 1.8 * s, Y0 + 1.35 * s, z],
            'mesh': {'builder': 'box', 'args': {'sx': 4.2 * s, 'sy': 1.9 * s,
                                                'sz': 2.4 * s, 'color': box_col}},
            'material': {'preset': 'plastic_red', 'base_color': box_col,
                         'roughness': 0.55}}

    # 驾驶室顶导流罩：lathe 半锥（削前风阻的斜面罩）
    deflector = {'name': f'{name}_deflector',
                 'translation': [x - 2.2 * s, Y0 + 1.9 * s, z],
                 'scale': [1.0, 0.55, 1.0],
                 'mesh': {'builder': 'lathe',
                          'args': {'profile': [[0.0, 0.0], [0.9, 0.15],
                                               [1.0, 0.5], [1.0, 0.9],
                                               [0.5, 1.1], [0.0, 1.15]],
                                   'segments': 16,
                                   'color': [0.48, 0.24, 0.11]}},
                 'material': {'preset': 'plastic_red',
                              'base_color': [0.48, 0.24, 0.11],
                              'roughness': 0.5}}

    # 油箱：横向 lathe 旋转体（轴向沿 X），带倒角
    tank = {'name': f'{name}_tank', 'rotation': _QZ90,
            'translation': [x + 0.6 * s, Y0 + 0.62 * s, z + 1.35 * s],
            'mesh': {'builder': 'lathe',
                     'args': {'profile': [[0.0, -0.45], [0.22, -0.45],
                                          [0.28, -0.38], [0.28, 0.38],
                                          [0.22, 0.45], [0.0, 0.45]],
                              'segments': 14, 'color': [0.24, 0.26, 0.24]}},
            'material': {'preset': 'steel', 'base_color': [0.24, 0.26, 0.24],
                         'roughness': 0.42}}

    # 排气管：竖置 lathe，顶端喇叭口
    stack = {'name': f'{name}_stack',
             'translation': [x - 2.65 * s, Y0 + 1.2 * s, z + 1.15 * s],
             'mesh': {'builder': 'lathe',
                      'args': {'profile': [[0.0, 0.0], [0.085, 0.02],
                                           [0.085, 1.5], [0.115, 1.62],
                                           [0.115, 1.72], [0.0, 1.74]],
                               'segments': 12, 'color': [0.30, 0.29, 0.28]}},
             'material': {'preset': 'steel', 'base_color': [0.30, 0.29, 0.28],
                          'roughness': 0.55}}

    # 油桶（备用油）：lathe 带箍的桶身
    drum = {'name': f'{name}_drum',
            'translation': [x + 1.4 * s, Y0 + 2.6 * s, z],
            'mesh': {'builder': 'lathe',
                     'args': {'profile': [[0.0, -0.42], [0.28, -0.42],
                                          [0.32, -0.36], [0.32, -0.10],
                                          [0.34, -0.06], [0.34, 0.06],
                                          [0.32, 0.10], [0.32, 0.36],
                                          [0.28, 0.42], [0.0, 0.42]],
                              'segments': 14, 'color': [0.20, 0.32, 0.20]}},
            'material': {'preset': 'steel', 'base_color': [0.20, 0.32, 0.20],
                         'roughness': 0.45}}

    # 保险杠：tube 沿宽度扫掠（中间略向前凸）
    bumper = {'name': f'{name}_bumper',
              'translation': [x, 0.0, z],
              'mesh': {'builder': 'tube',
                       'args': {'points': [[-4.15 * s, Y0 + 0.45 * s, z - 1.15 * s],
                                           [-4.32 * s, Y0 + 0.45 * s, z],
                                           [-4.15 * s, Y0 + 0.45 * s, z + 1.15 * s]],
                                'radius': 0.10 * s, 'radial': 8,
                                'color': [0.22, 0.22, 0.23]}},
              'material': {'preset': 'steel', 'base_color': [0.22, 0.22, 0.23],
                           'roughness': 0.5}}

    # 车灯玻璃罩（小球 + 低粗糙度，PBR 下自动吃高光）
    lights = []
    for i, lz in enumerate((-0.8, 0.8)):
        lights.append({'name': f'{name}_light{i}',
                       'translation': [x - 2.9 * s, Y0 + 0.85 * s, z + lz * s],
                       'mesh': {'builder': 'sphere',
                                'args': {'radius': 0.16 * s, 'segments': 10,
                                         'rings': 6, 'color': [0.9, 0.88, 0.78]}},
                       'material': {'preset': 'ceramic',
                                    'base_color': [0.9, 0.88, 0.78],
                                    'roughness': 0.15}})

    wheels = []
    for i, (wx, wz) in enumerate([(-1.8, -1.15), (-1.8, 1.15),
                                  (1.2, -1.15), (1.2, 1.15),
                                  (2.6, -1.15), (2.6, 1.15)]):
        w = {'name': f'{name}_w{i}', 'rotation': _QX90,
             'translation': [x + wx * s, Y0 + 0.45 * s, z + wz * s],
             'children': [
                 {'name': f'{name}_w{i}_tire',
                  'mesh': {'builder': 'torus',
                           'args': {'major': 0.30 * s, 'minor': 0.15 * s,
                                    'major_segs': 18, 'minor_segs': 8,
                                    'color': [0.08, 0.08, 0.09]}},
                  'material': {'preset': 'rubber', 'roughness': 0.95}},
                 {'name': f'{name}_w{i}_hub',
                  'mesh': {'builder': 'lathe',
                           'args': {'profile': [[0.0, -0.16], [0.19, -0.16],
                                                [0.19, 0.16], [0.0, 0.16]],
                                    'segments': 12,
                                    'color': [0.30, 0.30, 0.31]}},
                  'material': {'preset': 'steel',
                               'base_color': [0.30, 0.30, 0.31],
                               'roughness': 0.4}}]}
        wheels.append(w)

    qz = [1.0, 0.0, 0.0, 0.0]
    if rot:
        qz = [math.cos(rot / 2), 0.0, math.sin(rot / 2), 0.0]
    return {'name': name, 'rotation': qz,
            'children': [cab, box_, deflector, tank, stack, drum, bumper]
            + lights + wheels}


def milestone_node(x, z, label_h=0.9):
    """白墩子：白色里程碑短柱 + 顶帽。"""
    return {'name': f'milestone_{x:.0f}',
            'translation': [x, Y0 + 0.30, z],
            'mesh': {'builder': 'box', 'args': {'sx': 0.36, 'sy': label_h,
                                                'sz': 0.22,
                                                'color': [0.88, 0.86, 0.80]}},
            'material': {'preset': 'marble', 'base_color': [0.88, 0.86, 0.80],
                         'roughness': 0.85},
            'children': [{'name': f'milestone_cap_{x:.0f}',
                          'translation': [0, label_h / 2 + 0.06, 0],
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 0.44, 'sy': 0.12, 'sz': 0.30,
                                            'color': [0.80, 0.30, 0.16]}},
                          'material': {'preset': 'plastic_red', 'roughness': 0.4}}]}


def pole_node(x, z, h=7.0):
    """电线杆：细柱 + 横担。"""
    return {'name': f'pole_{x:.0f}_{z:.0f}',
            'translation': [x, Y0, z],
            'mesh': {'builder': 'cylinder', 'args': {'radius': 0.11, 'height': h,
                                                     'segments': 8,
                                                     'color': [0.30, 0.24, 0.18]}},
            'material': {'preset': 'ceramic', 'base_color': [0.30, 0.24, 0.18],
                         'roughness': 0.9},
            'children': [{'name': f'pole_arm_{x:.0f}_{z:.0f}',
                          'translation': [0, h * 0.45, 0],
                          'mesh': {'builder': 'box',
                                   'args': {'sx': 0.08, 'sy': 0.08, 'sz': 1.6,
                                            'color': [0.28, 0.22, 0.17]}},
                          'material': {'preset': 'ceramic', 'base_color': [0.28, 0.22, 0.17], 'roughness': 0.9}}]}


def terrain_y(x, z, hm, height=TERRAIN_H):
    """世界坐标 -> 地形高度（双线性）。x/z ∈ [-40,40]。"""
    rows, cols = hm.shape
    c = (x + 40.0) / 80.0 * (cols - 1)
    r = (z + 40.0) / 80.0 * (rows - 1)
    c0, r0 = int(c), int(r)
    c1, r1 = min(c0 + 1, cols - 1), min(r0 + 1, rows - 1)
    fc, fr = c - c0, r - r0
    v = (hm[r0, c0] * (1 - fc) * (1 - fr) + hm[r0, c1] * fc * (1 - fr)
         + hm[r1, c0] * (1 - fc) * fr + hm[r1, c1] * fc * fr)
    return float(v) * height - 0.05


def scatter_rocks(rng, hm, count=60, half=38.0, road_half=5.5):
    """荒滩碎石：远离公路随机散布、贴合地形的小石块。"""
    rocks = []
    for i in range(count):
        x = float(rng.uniform(-half, half))
        z = float(rng.uniform(-half, half))
        if abs(z) < road_half:
            continue
        r = float(rng.uniform(0.15, 0.7))
        rocks.append({'name': f'rock_{i}',
                      'translation': [x, terrain_y(x, z, hm) + r * 0.2, z],
                      'scale': [1.0, float(rng.uniform(0.5, 0.9)), 1.0],
                      'mesh': {'builder': 'sphere',
                               'args': {'radius': r, 'segments': 6, 'rings': 4,
                                        'color': [0.34, 0.30, 0.26]}},
                      'material': {'preset': 'ceramic', 'base_color': [0.34, 0.30, 0.26], 'roughness': 0.95}})
    return rocks


def build_scene(seed=244):
    rng = np.random.default_rng(seed)
    hm, _, _ = gobi_heightmap(seed=seed)

    road_strip = {'name': 'road',
                  'translation': [0, Y0 + 0.035, 0],
                  'mesh': {'builder': 'plane',
                           'args': {'width': 80.0, 'depth': 7.0, 'segs': 1,
                                    'color': [0.23, 0.22, 0.21], 'y': 0.0}},
                  'material': {'preset': 'rubber', 'base_color': [0.23, 0.22, 0.21],
                               'roughness': 0.92}}
    # 中线虚段：白漆短条
    dashes = []
    for i in range(-9, 10):
        dashes.append({'name': f'dash_{i}', 'translation': [i * 4.0, Y0 + 0.045, 0],
                       'mesh': {'builder': 'plane',
                                'args': {'width': 2.0, 'depth': 0.18, 'segs': 1,
                                         'color': [0.85, 0.82, 0.72], 'y': 0.0}},
                       'material': {'preset': 'ceramic',
                                    'base_color': [0.85, 0.82, 0.72]}})
    terrain = {'name': 'gobi',
               'mesh': {'builder': 'heightfield',
                        'args': {'hm': np.round(hm, 4).tolist(),
                                 'width': 80.0, 'depth': 80.0, 'height': TERRAIN_H,
                                 'y_base': -0.05,
                                 'color': [0.46, 0.38, 0.28]}},
               'material': {'preset': 'ceramic', 'base_color': [0.46, 0.38, 0.28],
                            'roughness': 0.95}}

    children = [
        terrain, road_strip, *dashes,
        milestone_node(8.0, 4.4), milestone_node(-14.0, 4.4), milestone_node(26.0, 4.4),
        truck_node(x=-4.0, z=1.8, rot=math.radians(-6)),
        *[pole_node(x, -6.0) for x in range(-36, 37, 8)],
        *scatter_rocks(rng, hm),
    ]
    scene = {
        'name': 'l100-ep001-baidunzi',
        'environment': {
            'sun_dir': [0.42, 0.85, -0.30],        # 戈壁正午偏高角度硬光
            'sun_color': [1.0, 0.90, 0.72],
            'sun_intensity': 1.7,
            'sky_zenith': [0.22, 0.40, 0.68],      # 高原蓝（压暗，少给地面喂蓝光）
            'sky_horizon': [0.78, 0.74, 0.62],     # 地平线暖霾
            'sky_ground': [0.34, 0.28, 0.20],      # 地面反照偏棕
            'ibl_intensity': 0.45,
            'sky_intensity': 0.80,
        },
        'root': {'name': 'root', 'children': children},
    }
    return scene


# --------------------------------------------------------------- 运镜 --
def build_shot(duration=24.0):
    """第 001 集开场 24 秒运镜：从里程碑低机位 -> 跟车 -> 拉升大远景。

    五幕（对应《最后一百公里》开场：白墩子、直路、车、人、地平线）。
    """
    from world3d.engine.shot import Shot, CAMERA_NODE
    s = Shot(name='ep001_opening', duration=duration,
             acts=[{'t': 0.0, 'label': '白墩子'},
                   {'t': 6.0, 'label': '公路纵深'},
                   {'t': 12.0, 'label': '卡车'},
                   {'t': 18.0, 'label': '大远景'}],
             notes='第001集开场：低机位里程碑 -> 沿路推近卡车 -> 拉升戈壁大远景')
    s.add_channel(CAMERA_NODE, 'position',
                  [0.0, 5.0, 11.0, 17.0, 24.0],
                  [[11.5, Y0 + 1.1, 6.0], [8.0, Y0 + 1.7, 4.4], [1.0, Y0 + 2.4, 6.5],
                   [-10.0, 5.0, 9.0], [-26.0, 13.0, 16.0]])
    s.add_channel(CAMERA_NODE, 'target',
                  [0.0, 5.0, 11.0, 17.0, 24.0],
                  [[8.6, Y0 + 0.8, 4.5], [-6.0, Y0 + 1.2, 2.0], [-4.0, Y0 + 1.4, 1.8],
                   [-4.0, 1.6, 1.8], [-4.0, 1.0, 1.8]])
    s.add_channel(CAMERA_NODE, 'fov',
                  [0.0, 11.0, 13.0, 24.0],
                  [34.0, 34.0, 42.0, 48.0])
    return s


# --------------------------------------------------------------- 出片 --
def render(scene, shot, out_dir, fps=24, size=(1280, 720), exposure=0.90):
    import pyglet
    pyglet.options['dpi_scaling'] = 'real'
    pyglet.options['debug_gl'] = False
    from pyglet.window import Window
    from pyglet.gl import GL_RGBA, GL_UNSIGNED_BYTE, glReadPixels
    import ctypes
    import time as _t
    from PIL import Image
    import imageio_ffmpeg
    import subprocess

    from world3d.engine.camera import Camera
    from world3d.engine.env import LightEnvironment
    from world3d.engine.renderer import Renderer
    from world3d.engine.shot import ShotPlayer
    from world3d.engine.scene_build import build_scene as build
    from world3d.engine.shaders import get
    from world3d.engine.postfx import PostSettings

    os.makedirs(out_dir, exist_ok=True)
    frames_dir = os.path.join(out_dir, 'frames')
    os.makedirs(frames_dir, exist_ok=True)
    for f in os.listdir(frames_dir):
        os.remove(os.path.join(frames_dir, f))

    win = Window(size[0], size[1], caption='l100', visible=False)
    env = LightEnvironment(**scene['environment'])
    env.bake()
    prog = get('pbr')
    sc = build(scene, prog)
    post = PostSettings()
    post.exposure = exposure
    post.bloom_threshold = 1.6
    post.vignette = 0.42
    renderer = Renderer(size[0], size[1], environment=env, post_settings=post)
    cam = Camera()
    player = ShotPlayer(shot, scene=sc, camera=cam)

    n = int(round(shot.duration * fps))
    t0all = _t.perf_counter()
    for i in range(n):
        t = i / fps
        player.apply(t)
        win.switch_to()
        win.clear()
        win.projection = cam.projection(size[0] / size[1])
        win.view = cam.view_Mat4()
        renderer.render(cam, sc, aspect=size[0] / size[1], window=win)
        w, h = win.width, win.height
        buf = (ctypes.c_ubyte * (w * 4 * h))()
        glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
        arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 4)
        Image.fromarray(arr[::-1, :, :3]).save(
            os.path.join(frames_dir, f'f{i:05d}.png'))
        if i % 48 == 0:
            print(f'[render] {i + 1}/{n}')
    print(f'[render] {n} frames in {_t.perf_counter() - t0all:.1f}s')

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    mp4 = os.path.join(out_dir, f'{shot.name}.mp4')
    subprocess.run([ffmpeg, '-y', '-framerate', str(fps), '-i',
                    os.path.join(frames_dir, 'f%05d.png'),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '19', mp4],
                   check=True, capture_output=True)
    print(f'[out] {mp4} {os.path.getsize(mp4) / 1024:.0f} KB')
    win.close()
    return mp4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--json', default=os.path.join(ROOT, 'probes', 'out',
                                                   'gobi_scene.json'))
    ap.add_argument('--shot-json', default=os.path.join(ROOT, 'probes', 'out',
                                                        'ep001_opening.shot.json'))
    ap.add_argument('--render', action='store_true')
    ap.add_argument('--out', default=os.path.join(ROOT, 'probes', 'out'))
    args = ap.parse_args()

    scene = build_scene()
    os.makedirs(os.path.dirname(args.json), exist_ok=True)
    with open(args.json, 'w', encoding='utf-8') as f:
        json.dump(scene, f, ensure_ascii=False)
    print(f'[scene] {args.json}  nodes~{len(scene["root"]["children"])}')

    shot = build_shot()
    shot.save(args.shot_json)
    print(f'[shot] {args.shot_json}  duration={shot.duration}s')

    if args.render:
        render(scene, shot, args.out)


if __name__ == '__main__':
    main()

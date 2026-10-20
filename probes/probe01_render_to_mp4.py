# -*- coding: utf-8 -*-
"""冒烟 01：离屏逐帧渲染 -> PNG 序列 -> mp4。

只验证一件事：**引擎能不能不靠人手、确定性地产出视频帧**。
不追求画面好看，只追求"跑通 + 可量化"（每帧耗时、总时长、码率）。

用法::

    python probes/probe01_render_to_mp4.py                # 默认 960x540 / 12fps / 6s
    python probes/probe01_render_to_mp4.py --w 1280 --h 720 --fps 24 --dur 8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

# 让 world3d 可导入（仓库根即本文件的上上级）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pyglet                                                    # noqa: E402

pyglet.options['dpi_scaling'] = 'real'
pyglet.options['debug_gl'] = False
# 离屏渲染时窗口不该抢焦点，也不该真弹出来（但仍然需要一个 GL 上下文）
pyglet.options['shadow_window'] = True

from pyglet.gl import GL_RGBA, GL_UNSIGNED_BYTE, glReadPixels        # noqa: E402
from pyglet.window import Window                                     # noqa: E402
import ctypes                                                        # noqa: E402
import numpy as np                                                   # noqa: E402

from world3d.engine.camera import Camera                             # noqa: E402
from world3d.engine.env import LightEnvironment                      # noqa: E402
from world3d.engine.renderer import Renderer                         # noqa: E402
from world3d.engine.scene_build import build_scene                   # noqa: E402
from world3d.engine.shaders import get                               # noqa: E402
from world3d.engine.postfx import PostSettings                       # noqa: E402


def read_rgb(window):
    """按实际 buffer 尺寸直读像素（DPI 无关，正立翻转为 numpy RGB）。"""
    w, h = window.width, window.height
    row = w * 4
    buf = (ctypes.c_ubyte * (row * h))()
    glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
    arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(h, w, 4)
    return arr[::-1, :, :3]          # OpenGL 原点在左下 -> 翻转成图片坐标


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--w', type=int, default=960)
    ap.add_argument('--h', type=int, default=540)
    ap.add_argument('--fps', type=int, default=12)
    ap.add_argument('--dur', type=float, default=6.0)
    ap.add_argument('--out', default=os.path.join(ROOT, 'probes', 'out'))
    args = ap.parse_args()

    scene_json = os.path.join(ROOT, 'docs', 'llm_scene_demo.json')
    with open(scene_json, 'r', encoding='utf-8') as f:
        data = json.load(f)

    os.makedirs(args.out, exist_ok=True)
    n_frames = int(round(args.dur * args.fps))

    win = Window(args.w, args.h, caption='probe01', visible=False)
    t_setup = time.perf_counter()

    env = LightEnvironment(**{k: v for k, v in data.get('environment', {}).items()
                              if k in ('sun_dir', 'sun_color', 'sun_intensity',
                                       'sky_zenith', 'sky_horizon', 'sky_ground',
                                       'ibl_intensity', 'sky_intensity')})
    env.bake()

    prog = get('pbr')
    scene = build_scene(data, prog)
    renderer = Renderer(args.w, args.h, environment=env,
                        post_settings=PostSettings())
    cam = Camera(position=(16.0, 6.0, 16.0), target=(0.0, 2.0, 0.0), fov=45.0)

    setup_cost = time.perf_counter() - t_setup
    print(f'[setup] {setup_cost:.2f}s  nodes={len(list(scene.root.traverse()))}')

    frames_dir = os.path.join(args.out, 'frames')
    os.makedirs(frames_dir, exist_ok=True)

    times = []
    for i in range(n_frames):
        t = i / args.fps
        # 简易环绕运镜：只验证"逐帧可控"，不做艺术处理
        ang = t * 0.35
        import math
        r = 17.0
        cam.position = (math.cos(ang) * r, 4.5 + math.sin(t * 0.6) * 1.5,
                        math.sin(ang) * r)
        cam.target = (0.0, 2.2, 0.0)

        t0 = time.perf_counter()
        win.switch_to()
        win.clear()
        win.projection = cam.projection(args.w / args.h)
        win.view = cam.view_Mat4()
        renderer.render(cam, scene, aspect=args.w / args.h, window=win)
        arr = read_rgb(win)
        times.append(time.perf_counter() - t0)

        from PIL import Image
        Image.fromarray(arr).save(os.path.join(frames_dir, f'f{i:05d}.png'))

        if i % 10 == 0 or i == n_frames - 1:
            print(f'[frame] {i + 1}/{n_frames}  t={t:.2f}s  {times[-1] * 1000:.1f}ms')

    # 编码
    import imageio_ffmpeg
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    mp4 = os.path.join(args.out, 'probe01.mp4')
    import subprocess
    cmd = [ffmpeg, '-y', '-framerate', str(args.fps), '-i',
           os.path.join(frames_dir, 'f%05d.png'),
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '20',
           '-vf', 'scale=trunc(iw/2)*2:trunc(ih/2)*2', mp4]
    t_enc = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=True)
    enc_cost = time.perf_counter() - t_enc

    mean_ms = sum(times) / len(times) * 1000
    print(f'[done] frames={n_frames} mean_render={mean_ms:.1f}ms '
          f'(≈{1000 / mean_ms:.1f} fps 上限) encode={enc_cost:.2f}s')
    print(f'[out] {mp4}  {os.path.getsize(mp4) / 1024:.0f} KB')
    win.close()
    print('EXIT_OK')


if __name__ == '__main__':
    main()

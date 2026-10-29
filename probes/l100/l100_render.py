# -*- coding: utf-8 -*-
"""静帧渲染 + Ken Burns 运镜 + 片段拼接。

``StillRenderer`` 复用一个离屏 GL 窗口：同一场景只 build 一次 GPU 资源，
换构图只改 Camera，出多张 1920×1080 静帧（放大留白给后续推轨）。

``kenburns`` 用 ffmpeg ``zoompan`` 把静帧变成缓慢运镜的视频段，
``concat_videos`` 把各段用 xfade 交叉溶解接起来。
"""
from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
PROBES = os.path.dirname(_HERE)
ROOT = os.path.dirname(PROBES)
for _p in (ROOT, PROBES):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


class StillRenderer:
    """离屏渲染器：一次 switch_scene，多次 shoot。"""

    def __init__(self, size=(1920, 1080)):
        import pyglet
        pyglet.options['dpi_scaling'] = 'real'
        pyglet.options['debug_gl'] = False
        from pyglet.window import Window
        from world3d.engine.renderer import Renderer
        from world3d.engine.postfx import PostSettings

        self.w, self.h = size
        self.win = Window(self.w, self.h, caption='l100-still', visible=False)
        self.post = PostSettings()
        self.post.bloom_threshold = 1.6
        self.post.vignette = 0.42
        self.renderer = Renderer(self.w, self.h, environment=None,
                                 post_settings=self.post)
        self._prog = None
        self._scene = None
        self.scene_name = None

    # ---------------------------------------------------------- 场景 --
    def switch_scene(self, scene):
        from world3d.engine.env import LightEnvironment
        from world3d.engine.scene_build import build_scene as build
        from world3d.engine.shaders import get

        payload = {k: v for k, v in scene.items() if not k.startswith('_')}
        if self._prog is None:
            self._prog = get('pbr')
        env = LightEnvironment(**payload.get('environment', {}))
        env.bake()
        self.renderer.environment = env
        self.post.exposure = float(scene.get('_exposure', 0.9))
        self._scene = build(payload, self._prog)
        self.scene_name = payload.get('name', '?')
        return self.scene_name

    # ---------------------------------------------------------- 拍摄 --
    def shoot(self, pos, target, fov, out_png, grade=True):
        from pyglet.gl import GL_RGBA, GL_UNSIGNED_BYTE, glReadPixels
        from world3d.engine.camera import Camera

        cam = Camera(position=pos, target=target, fov=fov)
        self.win.switch_to()
        self.win.clear()
        aspect = self.w / self.h
        self.win.projection = cam.projection(aspect)
        self.win.view = cam.view_Mat4()
        self.renderer.render(cam, self._scene, aspect=aspect, window=self.win)
        buf = (ctypes.c_ubyte * (self.w * 4 * self.h))()
        glReadPixels(0, 0, self.w, self.h, GL_RGBA, GL_UNSIGNED_BYTE, buf)
        arr = np.frombuffer(bytes(buf), dtype=np.uint8).reshape(self.h, self.w, 4)
        img = Image.fromarray(arr[::-1, :, :3])
        if grade:
            img = _grade(img)
        d = os.path.dirname(out_png)
        if d:
            os.makedirs(d, exist_ok=True)
        img.save(out_png)
        return out_png

    def close(self):
        try:
            self.win.close()
        except Exception:
            pass


def _grade(img, lift=6, gain=1.06, sat=1.06):
    """轻微提亮/加饱和：抵消 IBL 天光把戈壁洗灰的倾向。"""
    a = np.asarray(img).astype(np.float32)
    a = np.clip((a - lift) * gain, 0, 255)
    grey = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    a = grey[..., None] + (a - grey[..., None]) * sat
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


# ------------------------------------------------------------ Ken Burns --
_ZOOMS = {
    'in':      ("1.0+0.16*on/%d", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    'out':     ("1.16-0.16*on/%d", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    'pan_r':   ("1.08", "(iw-iw/zoom)*(on/%d)", "ih/2-(ih/zoom/2)"),
    'pan_l':   ("1.08", "(iw-iw/zoom)*(1-on/%d)", "ih/2-(ih/zoom/2)"),
    'drift_u': ("1.10", "iw/2-(iw/zoom/2)", "(ih-ih/zoom)*(1-on/%d)"),
    'still':   ("1.02", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
}


def kenburns(png, seconds, out_mp4, size=(1280, 720), move='in', fps=25,
             crf=20):
    """单张静帧 → 一段缓慢运镜视频，时长等于该段的配音长度。"""
    seconds = max(seconds, 0.20)
    n = max(2, int(round(seconds * fps)))
    zx, xx, yx = _ZOOMS.get(move, _ZOOMS['in'])
    z = zx % n if '%d' in zx else zx
    x = xx % n if '%d' in xx else xx
    y = yx % n if '%d' in yx else yx
    w, h = size
    filt = (f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={w}x{h}:fps={fps},"
            f"scale={w}:{h},setsar=1,format=yuv420p")
    d = os.path.dirname(out_mp4)
    if d:
        os.makedirs(d, exist_ok=True)
    r = subprocess.run([ffmpeg_exe(), '-y', '-loop', '1', '-i', png,
                        '-filter_complex', filt, '-frames:v', str(n),
                        '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-crf', str(crf), out_mp4], capture_output=True)
    if not os.path.exists(out_mp4):
        raise RuntimeError('kenburns failed: '
                           + r.stderr.decode('utf-8', 'replace')[-600:])
    return out_mp4


def probe_duration(path, default=0.0):
    ff = ffmpeg_exe()
    cand = os.path.join(os.path.dirname(ff), 'ffprobe.exe')
    if os.path.exists(cand):
        try:
            out = subprocess.run([cand, '-v', 'error', '-show_entries',
                                  'format=duration', '-of', 'json', path],
                                 capture_output=True, check=True).stdout
            return float(json.loads(out)['format']['duration'])
        except Exception:
            pass
    m = re.search(rb'Duration: (\d+):(\d+):(\d+\.\d+)',
                  subprocess.run([ff, '-i', path],
                                 capture_output=True).stderr or b'')
    if m:
        return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    return default


def concat_videos(mp4s, out_mp4, fade=0.45, crf=20):
    """把多段视频首尾相连，镜头之间做交叉溶解。"""
    mp4s = [m for m in mp4s if m and os.path.exists(m)]
    d = os.path.dirname(out_mp4)
    if d:
        os.makedirs(d, exist_ok=True)
    if len(mp4s) == 1:
        shutil.copy(mp4s[0], out_mp4)
        return out_mp4
    if fade <= 0:
        lst = os.path.join(d or '.', '_concat.txt')
        with open(lst, 'w', encoding='utf-8') as fh:
            for m in mp4s:
                fh.write(f"file '{os.path.abspath(m)}'\n")
        subprocess.run([ffmpeg_exe(), '-y', '-f', 'concat', '-safe', '0',
                        '-i', lst, '-c', 'copy', out_mp4],
                       check=True, capture_output=True)
        return out_mp4

    durs = [probe_duration(m, 1.0) for m in mp4s]
    cmd = [ffmpeg_exe(), '-y']
    for m in mp4s:
        cmd += ['-i', m]
    parts, acc, chain = [], durs[0], None
    for i in range(1, len(mp4s)):
        f = min(fade, durs[i] / 2.5, durs[i - 1] / 2.5)
        left = '0:v' if chain is None else chain
        lab = f'x{i}'
        parts.append(f"[{left}][{i}:v]xfade=transition=fade:duration={f:.3f}"
                     f":offset={acc - f:.3f}[{lab}]")
        chain = lab
        acc = acc + durs[i] - f
    cmd += ['-filter_complex', ';'.join(parts), '-map', f'[{chain}]',
            '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', str(crf),
            '-pix_fmt', 'yuv420p', out_mp4]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_mp4


def mux(video_mp4, audio_path, out_mp4, audio_gain=1.0):
    """给视频挂上音轨。apad 补尾 + 显式 -t 封顶，保证必然终止。"""
    d = os.path.dirname(out_mp4)
    if d:
        os.makedirs(d, exist_ok=True)
    vdur = probe_duration(video_mp4, 0.0)
    cmd = [ffmpeg_exe(), '-y', '-i', video_mp4, '-i', audio_path,
           '-filter_complex', f'[1:a]volume={audio_gain:.2f},apad[a]',
           '-map', '0:v', '-map', '[a]',
           '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k']
    if vdur > 0:
        cmd += ['-t', f'{vdur + 1.0:.3f}']
    else:
        cmd += ['-shortest']
    cmd.append(out_mp4)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_mp4

# -*- coding: utf-8 -*-
"""阈廊 / Liminall 音频（kkrieger_audio 同款离线渲染范式，纯合成零素材）。

三件常驻/事件音：

- ``hum``      荧光灯嗡鸣循环（8s）：低频锯齿 + 440Hz 正弦 + 细噪声——
               后室 Level 0 的"max hum-buzz"音床，death 循环的心理压力源；
- ``broadcast`` 杂音广播（7s）：白噪声底 + 间歇低频方块"电文"——
               通讯失灵设定的声音化（叙事文本走屏幕字幕，见 app）；
- ``scare``    jump scare 音爆（0.7s）：噪声冲击 + 骤降音。

全部走 ``world3d.engine.audio`` 的 Track/render_wav 离线渲染（kkrieger_audio 同款），
产物缓存到临时目录——headless 无驱动时 render_wav 仍可跑（纯数组运算）。
"""
from __future__ import annotations

import os
import tempfile

from world3d.engine.audio import Track, render_wav

_DIR = None


def _hum_track():
    tr = Track(8.0, rate=22050)
    # 荧光灯：120Hz 基频（镇流器）+ 2/3 次谐波 + 高频细噪——"max hum-buzz"
    tr.tone(0.0, 8.0, 120, 120, wave='saw', amp=0.16, attack=0.4, release=0.6)
    tr.tone(0.0, 8.0, 240, 240, wave='sine', amp=0.06, attack=0.4, release=0.6)
    tr.tone(0.0, 8.0, 55, 55, wave='sine', amp=0.10, attack=0.5, release=0.5)
    # 两处骤亮-骤灭（灯闪段落的声音化）
    tr.tone(3.1, 0.12, 800, 300, wave='square', amp=0.12, attack=0.005, release=0.1)
    tr.tone(6.4, 0.12, 800, 300, wave='square', amp=0.10, attack=0.005, release=0.1)
    return tr


def _broadcast_track():
    tr = Track(7.0, rate=22050)
    # 电台底噪（持续）
    tr.tone(0.0, 7.0, 90, 95, wave='saw', amp=0.07, attack=0.3, release=0.8)
    # "电文"脉冲：三短一长（摩尔斯感，不可读——后室通讯失灵）
    marks = ((0.6, 0.10), (0.9, 0.10), (1.2, 0.22), (2.4, 0.30),
             (3.6, 0.10), (3.9, 0.10), (4.2, 0.10), (4.5, 0.34),
             (5.8, 0.42))
    for (t0, d) in marks:
        tr.tone(t0, d, 210, 180, wave='square', amp=0.14, attack=0.01, release=0.05)
    return tr


def _scare_track():
    tr = Track(0.7, rate=22050)
    # 噪声冲击（用高频锯齿簇模拟）+ 骤降低音
    tr.tone(0.0, 0.18, 2400, 300, wave='saw', amp=0.5, attack=0.002, release=0.12)
    tr.tone(0.05, 0.55, 160, 38, wave='saw', amp=0.55, attack=0.003, release=0.4)
    tr.tone(0.0, 0.5, 60, 30, wave='square', amp=0.3, attack=0.002, release=0.35)
    return tr


def ensure_sfx(cache_dir=None, force=False):
    """渲染三件音效，返回 ``{'hum': path, 'broadcast': path, 'scare': path}``。

    与 ``kkrieger_audio.ensure_ost`` 同款缓存约定：产物已存在且未 force 则直接复用。
    """
    global _DIR
    d = cache_dir or _DIR or tempfile.mkdtemp(prefix='liminall_sfx_')
    _DIR = d
    out = {}
    specs = dict(hum=_hum_track, broadcast=_broadcast_track, scare=_scare_track)
    for name, build in specs.items():
        path = os.path.join(d, name + '.wav')
        if force or not os.path.isfile(path):
            render_wav(build(), path)
        out[name] = path
    return out

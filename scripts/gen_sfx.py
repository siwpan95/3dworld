# -*- coding: utf-8 -*-
"""渲染 3dworld 交互 SFX（bloodmusic 预设 → WAV，供 demos/scene_walker 使用）。

产物：assets/sfx/{jump,pickup,power_up,land}.wav（生成物，不入库）。
设计：不硬编码预设全名——按「语义 → 关键词」在 ALL_PRESETS 中自适应匹配，
预设库升级/更名时仍可用。

运行（装有 bloodmusic 的环境）：
    <venv python> scripts/gen_sfx.py
"""
import os
import sys

import numpy as np
from scipy.io import wavfile

from bloodmusic.sfx import ALL_PRESETS, get_preset
from bloodmusic.sfx.layered import mix_voices

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'assets', 'sfx')
SR = 44100

# 语义 → 预设关键词（按序遍历，命中第一个）
WANT = {
    'jump':     ('jump', 'blip', 'coin', 'pickup'),
    'pickup':   ('pickup', 'coin', 'blip'),
    'power_up': ('powerup', 'power_up', 'power', 'levelup', 'level_up'),
    'land':     ('land', 'stomp', 'hit', 'impact', 'thud'),
}


def pick(keywords):
    names = sorted(ALL_PRESETS)
    for kw in keywords:
        for n in names:
            if kw in n.lower():
                return n
    return None


def render(name):
    v = get_preset(name)
    a = np.asarray(mix_voices([v]), dtype=np.float64)
    if a.ndim == 2:
        a = a.mean(axis=1)
    peak = float(np.max(np.abs(a))) or 1.0
    return a / peak * 0.85


def main():
    os.makedirs(OUT, exist_ok=True)
    print('预设总量:', len(ALL_PRESETS), flush=True)
    ok = 0
    for want, kws in WANT.items():
        src = pick(kws)
        if src is None:
            print(f'[skip] {want}: 无匹配预设')
            continue
        try:
            a = render(src)
            path = os.path.join(OUT, f'{want}.wav')
            wavfile.write(path, SR, (a * 32767.0).astype(np.int16))
            ok += 1
            print(f'[ok] {want:9s} <- {src:26s} {len(a)/SR:.2f}s', flush=True)
        except Exception as e:
            print(f'[fail] {want}: {type(e).__name__}: {e}', flush=True)
    print(f'GEN_SFX_DONE ok={ok}/{len(WANT)} -> {OUT}')


if __name__ == '__main__':
    sys.exit(main())

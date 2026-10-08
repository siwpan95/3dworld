# -*- coding: utf-8 -*-
"""bloodmusic 试点：kkrieger 第六幕（圣殿 118–150s）重制。

对照目标（logic/kkrieger_audio.py 第六幕，120 BPM / A 小调）：

    木琴旋律  121–136（五声 16 音，每音 1 拍，A4–A5 音区）
    心跳鼓    126–141.4（每 2 拍两粒双击，间隔 0.25s）
    圣殿贝斯  126–141.5（每拍根音，G2/A2/F2/C3 随和弦进行）
    pad 铺底  Am–F–C–G 每小节（2s）
    点火爆发  141.5（低频坠落） · 尾晖 144–147.5（A4 长音渐消）

重制策略（体现分工）：**3dworld 管「何时响」，bloodmusic 管「声音内容」**——
每轨以休止符对齐到 64 拍（32s）总时间线，分轨渲染后相加。
运行（隔离环境，bloodmusic 已 editable 安装）：
    <envs/default python> experiments/bloodmusic_ost/remake_act6.py
"""
import os
import numpy as np
from scipy.io import wavfile

from bloodmusic.core.constants import SR
from bloodmusic.core.oscillator import OscType
from bloodmusic.core.envelope import EnvShape
from bloodmusic.music import (MusicPattern, render_pattern_to_array,
                              DrumPattern, DrumSoundParams, render_drum_pattern)
from bloodmusic.music.instruments import MARIMBA

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'act6_remake_bloodmusic.wav')

BPM = 120
BEAT = 60.0 / BPM                # 0.5s（与原曲一致）
T0 = 118.0                       # 重制段起点（原曲时间戳）
TOTAL = 32.0                     # 118–150
TOTAL_BEATS = int(TOTAL / BEAT)  # 64


def b(n):
    """原曲时间戳 -> 段内秒。"""
    return n - T0


def at(ts):
    """原曲时间戳 -> 段内拍序号。"""
    return (ts - T0) / BEAT


def mono(arr):
    return arr.mean(axis=1) if getattr(arr, 'ndim', 1) == 2 else arr


def pad_to(notes, durs, total=TOTAL_BEATS):
    """尾部补休止到总拍数（分轨渲染对齐用）。"""
    notes = list(notes) + [None] * (total - len(notes))
    durs = list(durs) + [1.0] * (total - len(durs))
    return notes, durs


# ------------------------------------------------------------------ 分轨 --
def track_pad():
    """Am–F–C–G 每小节根音长音（148 后渐寂）。"""
    names = ['A2', 'F2', 'C3', 'G2']
    notes, durs = [], []
    for bar in range(TOTAL_BEATS // 4):
        if T0 + bar * 2.0 >= 148.0:
            notes.append(None)
        else:
            notes.append(names[bar % 4])
        durs.append(4.0)
    p = MusicPattern(name='pad', notes=notes, durations=durs, bpm=BPM,
                     loop=False, osc_type=OscType.SAW, volume=0.055,
                     env_shape=EnvShape.PAD, lp_filter_hz=300.0,
                     reverb_mix=0.35, reverb_decay=2.0, stereo_width=0.35)
    return mono(render_pattern_to_array(p, TOTAL))


def track_melody():
    """木琴旋律（MARIMBA）：121–136 的 16 音 + 同步低八度软垫音。"""
    mel = ['A4', 'D5', 'G5', 'D5', 'A5', 'G5', 'D5', 'A4',
           'C5', 'E5', 'A5', 'E5', 'G5', 'E5', 'C5', 'A4']       # 原曲索引序列
    n = [None] * int(at(121.0)) + mel                            # 121s = 第 6 拍
    d = [1.0] * len(n)
    n, d = pad_to(n, d)
    hi = MARIMBA.make_pattern('melody', n, d, bpm=BPM, loop=False)
    hi.volume = 0.34
    hi.reverb_mix = 0.30
    hi.reverb_decay = 1.6
    a1 = mono(render_pattern_to_array(hi, TOTAL))

    low = [m[:-1] + str(int(m[-1]) - 1) for m in mel]            # 低八度
    n2 = [None] * int(at(121.0)) + low
    n2, d2 = pad_to(n2, [1.0] * len(n2))
    p2 = MusicPattern(name='melody_low', notes=n2, durations=d2, bpm=BPM,
                      loop=False, osc_type=OscType.SINE, volume=0.07,
                      env_shape=EnvShape.PLUCK, reverb_mix=0.25)
    return a1 + mono(render_pattern_to_array(p2, TOTAL))


def track_drums():
    """心跳式双击：每 2 拍两粒（间隔 0.25s），126–141.4。"""
    kick = [True, False, True, False, False, False, False, False,
            True, False, True, False, False, False, False, False]
    dp = DrumPattern(name='heartbeat', steps=16, bpm=BPM,
                     tracks={'kick': kick},
                     sounds={'kick': DrumSoundParams(
                         osc_type=OscType.SINE, pitch_hz=55.0,
                         decay=0.16, volume=0.52)})
    one_bar = mono(render_drum_pattern(dp, BEAT * 4))            # 16 步 = 4 拍
    seg_len = int((141.4 - 126.0) * SR)
    seg = np.tile(one_bar, 16)[:seg_len]
    out = np.zeros(int(TOTAL * SR))
    i0 = int(b(126.0) * SR)
    out[i0:i0 + len(seg)] += seg
    return out


def track_bass():
    """圣殿贝斯：126–141.5 每拍根音（G2/A2/F2/C3 随小节相位）。"""
    names = ['A2', 'F2', 'C3', 'G2']
    seq = []
    for i in range(int(at(141.5) - at(126.0))):                  # 126→141.5 共 31 拍
        bar = int((126.0 + i * BEAT) / 2.0) % 4
        seq.append(names[bar])
    notes = [None] * int(at(126.0)) + seq
    durs = [0.9] * len(notes)
    notes, durs = pad_to(notes, durs)
    p = MusicPattern(name='bass', notes=notes, durations=durs, bpm=BPM,
                     loop=False, osc_type=OscType.SQUARE, volume=0.15,
                     env_shape=EnvShape.PLUCK, lp_filter_hz=700.0,
                     reverb_mix=0.08)
    return mono(render_pattern_to_array(p, TOTAL))


def track_boom():
    """141.5 反应堆点火：低频坠落（2s 长共鸣，对应原版 1.1s 坠落 + 尾音）。"""
    notes = [None] * int(at(141.5)) + ['C1']
    durs = [1.0] * int(at(141.5)) + [4.0]
    notes, durs = pad_to(notes, durs)
    p = MusicPattern(name='boom', notes=notes, durations=durs, bpm=BPM,
                     loop=False, osc_type=OscType.SINE, volume=0.45,
                     env_shape=EnvShape.PAD, lp_filter_hz=140.0)
    return mono(render_pattern_to_array(p, TOTAL))


def track_tail():
    """144–147.5 尾晖：A4 长音渐消（衔接循环）。"""
    notes = [None] * int(at(144.0)) + ['A4']
    durs = [1.0] * int(at(144.0)) + [7.0]
    notes, durs = pad_to(notes, durs)
    p = MusicPattern(name='tail', notes=notes, durations=durs, bpm=BPM,
                     loop=False, osc_type=OscType.SINE, volume=0.11,
                     env_shape=EnvShape.PAD, lp_filter_hz=1200.0,
                     reverb_mix=0.4, reverb_decay=2.5)
    return mono(render_pattern_to_array(p, TOTAL))


def main():
    tracks = {
        'pad': track_pad(),
        'melody': track_melody(),
        'drums': track_drums(),
        'bass': track_bass(),
        'boom': track_boom(),
        'tail': track_tail(),
    }
    n = int(TOTAL * SR)
    mix = np.zeros(n, dtype=np.float64)
    for name, arr in tracks.items():
        m = min(len(arr), n)
        mix[:m] += arr[:m]
        print(f'  [track] {name:8s} len={m / SR:.2f}s '
              f'peak={np.max(np.abs(arr[:m])) if m else 0:.3f}', flush=True)
    peak = float(np.max(np.abs(mix))) or 1.0
    mix = mix / peak * 0.92
    wavfile.write(OUT, SR, (mix * 32767.0).astype(np.int16))
    print(f'[remake] wrote {OUT}  ({TOTAL:.0f}s @ {SR}Hz, '
          f'归一化 peak={peak:.3f})')


if __name__ == '__main__':
    main()

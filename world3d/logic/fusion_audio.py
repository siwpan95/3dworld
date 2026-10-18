# -*- coding: utf-8 -*-
"""融合总集篇配乐（logic 层）：全部动画串成一部片的程序化总配乐。

思路：与 kkrieger 的"音画编译期对齐"相同——段落表（FUSION_SEGMENTS）同时
驱动 ffmpeg 的 xfade 时间轴与这里的乐谱，段落的情绪参数（mood）决定该段
用哪些乐器，段落边界由 xfade 时长统一换算。

kkrieger 段**不**重新作曲：它的原 OST 本来就与画面采样级对齐，直接把
``assets/kkrieger_ost_150s.wav`` 整体植入对应偏移（保留开门气浪/爆发等 SFX）。

内存纪律：645s 全长一次性混音要 600MB+ 的 Python float 列表——改为**逐段
渲染成 16-bit int 数组**再拼接（峰值内存 ≈ 单段大小，约 40MB）。
"""
import math
import os
from array import array

from world3d.engine.audio import Track

RATE = 32000
BEAT = 0.5                          # 120 BPM
XFADE = 0.8                         # 与成片录制的转场时长一致（录制工具已随 demo 迁往 3dworld-demos）

# (录制文件名, 时长s, 情绪)——顺序就是成片顺序
FUSION_SEGMENTS = (
    ('main_48s',            48.0,  'band'),
    ('kkrieger_150s',       150.0, None),        # 植入自带 OST
    ('engine_showcase_24s', 24.0,  'tech'),
    ('material_lab_30s',    30.0,  'minimal'),
    ('minecraft_style_48s', 48.0,  'band'),
    ('voxel_terrain_36s',   36.0,  'chiptune'),
    ('terrain_flyover_48s', 48.0,  'ambient'),
    ('water_waves_40s',     40.0,  'fluid'),
    ('boids_45s',           45.0,  'playful'),
    ('particles_gpu_30s',   30.0,  'sparkle'),
    ('shadow_map_30s',      30.0,  'tense'),
    ('solar_system_60s',    60.0,  'ambient'),
    ('raymarch_sdf_32s',    32.0,  'eerie'),
    ('demoscene_tunnel_24s', 24.0, 'pulse'),
)

# A 小调和弦库（根音频率 + 三和弦半音）
CHORDS = (
    (55.00, (0, 3, 7)),      # Am
    (43.65, (0, 4, 7)),      # F
    (65.41, (0, 4, 7)),      # C
    (49.00, (0, 4, 7)),      # G
)
PENTA = (220.00, 261.63, 293.66, 329.63, 392.00, 440.00)


def segment_offsets():
    """各段在成片中的起点（xfade 每处吃掉 0.8s）。"""
    offs, t = [], 0.0
    for name, dur, _ in FUSION_SEGMENTS:
        offs.append(t)
        t += dur - XFADE
    return offs


def total_duration():
    return sum(d for _, d, _ in FUSION_SEGMENTS) - XFADE * (len(FUSION_SEGMENTS) - 1)


# ---------------------------------------------------------------- 乐器 --
def _kick(tr, t, amp=0.42):
    tr.tone(t, 0.13, 140.0, 44.0, wave='sine', amp=amp, attack=0.001, release=0.05)


def _bass(tr, t, f, dur=0.22, amp=0.13):
    tr.tone(t, dur, f, wave='square', amp=amp, duty=0.3,
            attack=0.004, release=0.05, lp=0.2)


def _pad(tr, t, dur, chord, amp=0.045, lp=0.06):
    root, semi = chord
    for k, s in enumerate(semi):
        tr.tone(t, dur, root * 2 ** (s / 12) * 2.0, wave='saw',
                amp=amp if k else amp * 1.25, attack=0.3, release=0.5, lp=lp)


def _arp(tr, t0, dur, step_dur, notes, wave='square', amp=0.08, lp=0.35,
         duty=0.25):
    t, k = t0, 0
    while t < t0 + dur - 1e-9:
        tr.tone(t, step_dur * 0.9, notes[k % len(notes)], wave=wave, amp=amp,
                attack=0.002, release=0.03, lp=lp, duty=duty)
        t += step_dur
        k += 1


def _hats(tr, t0, dur, step_dur, amp=0.04):
    t = t0
    while t < t0 + dur - 1e-9:
        tr.noise(t, 0.03, amp=amp, decay=28, lp=0.55, seed=int(t * 37))
        t += step_dur


def _bell(tr, t, f, amp=0.09):
    tr.tone(t, 0.5, f, wave='tri', amp=amp, attack=0.003, release=0.38, lp=0.5)
    tr.tone(t + 0.015, 0.3, f * 2.0, wave='sine', amp=amp * 0.35,
            attack=0.003, release=0.25)


def _whoosh(tr, t, amp=0.14):
    """转场气浪（每处 xfade 边界的听觉缝合）。"""
    tr.noise(t - 0.35, 0.7, amp=amp, decay=1.6, lp=0.10, seed=int(t * 11))


# ---------------------------------------------------------------- 情绪段 --
def _section(tr, mood, dur, seed=0):
    """按情绪填充一段。所有段都建立在 Am 调性与 120BPM 网格上，
    换乐器/密度/音区换情绪——这样段间 xfade 才像"同一张专辑"。"""
    nbars = int(dur / (BEAT * 4)) + 1
    bar = BEAT * 4
    if mood == 'band':                       # 主片/方块：全配器
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.3, CHORDS[b % 4])
            if t0 >= 2.0:
                for s in range(4):
                    _kick(tr, t0 + s * BEAT)
                for s in range(8):
                    root, _ = CHORDS[b % 4]
                    _bass(tr, t0 + s * BEAT / 2, root * 2 ** ((0, 0, 12, 0)[s % 4] / 12))
                _hats(tr, t0, bar, BEAT / 2, 0.035)
        _arp(tr, 4.0, dur - 5.0, BEAT / 4, PENTA, amp=0.07)
    elif mood == 'tech':                     # 引擎秀：脉冲贝斯 + 机械镲
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.3, CHORDS[(b + 1) % 4], amp=0.04, lp=0.045)
            for s in range(4):
                root, _ = CHORDS[(b + 1) % 4]
                _bass(tr, t0 + s * BEAT, root, dur=0.3, amp=0.11)
            _hats(tr, t0, bar, BEAT / 4, 0.03)
        _arp(tr, 2.0, dur - 3.0, BEAT / 2, (PENTA[1], PENTA[3], PENTA[5]),
             wave='saw', amp=0.05, lp=0.3)
    elif mood == 'minimal':                  # 材质实验室：稀疏 pad + 单音
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.5, CHORDS[b % 4], amp=0.05, lp=0.04)
            if b % 2 == 0:
                _bell(tr, t0 + bar * 0.75, PENTA[(b * 2) % 6] * 2, 0.06)
    elif mood == 'chiptune':                 # 体素：方波琶音 + 干镲
        for b in range(nbars):
            t0 = b * bar
            root, _ = CHORDS[b % 4]
            _arp(tr, t0, bar, BEAT / 4,
                 (root * 4, root * 6, root * 8, root * 6), amp=0.075, duty=0.5)
            _kick(tr, t0, 0.3)
            _kick(tr, t0 + bar * 0.5, 0.3)
            _hats(tr, t0, bar, BEAT, 0.045)
    elif mood == 'ambient':                  # 行星/地形：长 pad + 稀疏钟
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.8, CHORDS[(b + 2) % 4], amp=0.055, lp=0.035)
            if b % 3 == 1:
                _bell(tr, t0 + BEAT, PENTA[(b + 4) % 6], 0.05)
        # 高频星尘
        t = 0.0
        while t < dur - 1.0:
            if seed % 2 == 0:
                _bell(tr, t + 1.3, PENTA[int(t * 1.7) % 6] * 4, 0.022)
            t += BEAT * 3
    elif mood == 'fluid':                    # 水：流动的正弦琶音
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.6, CHORDS[b % 4], amp=0.05, lp=0.05)
            _arp(tr, t0, bar, BEAT / 4,
                 (PENTA[0], PENTA[2], PENTA[4], PENTA[2]),
                 wave='sine', amp=0.06, lp=0.6)
    elif mood == 'playful':                  # 群鸟：跳跃旋律 + 轻底鼓
        melody = (0, 2, 4, 5, 4, 2, 0, 3, 5, 3, 2, 0)
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.3, CHORDS[b % 4], amp=0.045)
            _kick(tr, t0, 0.22)
            _kick(tr, t0 + bar * 0.5, 0.22)
        for k, deg in enumerate(melody):
            _bell(tr, 2.0 + k * BEAT * 2, PENTA[deg] * 2, 0.07)
            _bell(tr, 2.0 + dur / 2 + k * BEAT * 2, PENTA[(deg + 2) % 6] * 2, 0.07)
    elif mood == 'sparkle':                  # 粒子：高音区碎钻琶音
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.4, CHORDS[(b + 3) % 4], amp=0.04, lp=0.04)
            _arp(tr, t0, bar, BEAT / 4,
                 (PENTA[5] * 2, PENTA[4] * 2, PENTA[2] * 2, PENTA[3] * 2),
                 wave='tri', amp=0.055, lp=0.5)
    elif mood == 'tense':                    # 阴影：低音脉冲 + 长弦
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.6, CHORDS[(b + 1) % 4], amp=0.05, lp=0.03)
            for s in range(8):
                root, _ = CHORDS[(b + 1) % 4]
                _bass(tr, t0 + s * BEAT / 2, root, dur=0.14, amp=0.09)
        tr.tone(dur - 3.0, 2.6, 55.0, 62.0, wave='saw', amp=0.06,
                attack=1.2, release=1.0, lp=0.05)
    elif mood == 'eerie':                    # SDF：失谐双 drone + 远钟
        root, _ = CHORDS[0]
        tr.tone(0.0, dur - 0.5, root * 2, wave='saw', amp=0.05,
                attack=1.5, release=1.5, lp=0.03)
        tr.tone(0.0, dur - 0.5, root * 2 * 2 ** (6 / 12), wave='saw',
                amp=0.04, attack=1.5, release=1.5, lp=0.03)
        t = 2.0
        while t < dur - 2.0:
            _bell(tr, t, PENTA[int(t) % 6] * 2, 0.04)
            t += BEAT * 5
    elif mood == 'pulse':                    # 隧道（终幕）：暗色 techno
        for b in range(nbars):
            t0 = b * bar
            _pad(tr, t0, bar + 0.3, CHORDS[b % 4], amp=0.04, lp=0.04)
            for s in range(4):
                _kick(tr, t0 + s * BEAT, 0.38)
            root, _ = CHORDS[b % 4]
            _arp(tr, t0, bar, BEAT / 4, (root, root, root * 2, root),
                 wave='saw', amp=0.07, lp=0.25)
    else:
        raise ValueError(f'未知情绪: {mood}')


# ---------------------------------------------------------------- 拼装 --
def build_fusion_ost(path, kkrieger_wav='assets/kkrieger_ost_150s.wav',
                     progress=None):
    """渲染融合总配乐 → 16-bit 单声道 WAV。返回 (路径, 实际秒数)。"""
    offs = segment_offsets()
    total = total_duration()
    n_total = int(round(total * RATE))
    pcm = array('h', [0] * n_total)

    def _mix(i0, samples):
        end = min(n_total, i0 + len(samples))
        j = 0
        for i in range(max(0, i0), end):
            pcm[i] = max(-32767, min(32767, pcm[i] + samples[j]))
            j += 1

    for (name, dur, mood), off in zip(FUSION_SEGMENTS, offs):
        if mood is None:                              # kkrieger：植入自带 OST
            import wave as _w
            import struct as _s
            with _w.open(kkrieger_wav, 'rb') as w:
                assert w.getframerate() == RATE and w.getnchannels() == 1
                raw = _s.unpack('<%dh' % w.getnframes(),
                                w.readframes(w.getnframes()))
            _mix(int(off * RATE), raw)
        else:
            tr = Track(dur, RATE)
            _section(tr, mood, dur, seed=len(name))
            # 段首尾 0.4s 渐入渐出（与 xfade 视觉溶解同长，听感不断层）
            ns = tr.n
            fade = int(0.4 * RATE)
            for i in range(min(fade, ns)):
                tr.buf[i] *= i / fade
                tr.buf[ns - 1 - i] *= i / fade
            peak = max(abs(x) for x in tr.buf) or 1.0
            gain = 0.85 / max(1.0, peak)
            _mix(int(off * RATE),
                 [int(x * gain * 32767) for x in tr.buf])
        # 段间转场气浪（跳过首段；kkrieger 段的边界已有自带音效缝合）
        if off > 0:
            tr2 = Track(1.0, RATE)
            _whoosh(tr2, 0.5)
            _mix(int((off - 0.5) * RATE),
                 [int(x * 32767) for x in tr2.buf])
        if progress:
            progress(name)

    # 全曲首尾渐入渐出 + 峰值归一
    fade = int(1.2 * RATE)
    for i in range(min(fade, n_total)):
        k = i / fade
        pcm[i] = int(pcm[i] * k)
        pcm[n_total - 1 - i] = int(pcm[n_total - 1 - i] * k)
    peak = max(max(pcm), -min(pcm)) or 1
    if peak > 30000:
        g = 30000.0 / peak
        for i in range(n_total):
            pcm[i] = int(pcm[i] * g)

    import wave as _w
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with _w.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return path, n_total / RATE

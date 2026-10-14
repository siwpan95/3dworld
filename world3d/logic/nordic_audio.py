# -*- coding: utf-8 -*-
"""诺德之境配乐（logic 层）：60 秒北欧风程序化管弦，纯 Python 离线渲染。

与 .kkrieger 的配乐同管线（engine.audio.Track 合成器），但换一套"北境"的
音乐语言：低音弦 drone 铺底、战鼓推进、铜管号角主题、冰晶铃音、无词合唱
pad 与龙吼/烈焰音效；D 小调（黎明段转大调色彩），60 BPM 的慢呼吸感。

曲式与动画共用同一批事件常量（导入 logic.nordic_saga 的 FIRE/RUNE/
TAKEOFF/ROAR 时刻），音画同步是"编译期"对齐：

==== ========== =========================================================
幕   时间        音乐
==== ========== =========================================================
一   0–9.5      荒原：drone（D1+D2）与风声，零星冰铃
二   9.5–21     进发：加入五度 drone、慢拨弦与战鼓，节奏行进
三   21–40     龙临：24.2 龙吼；号角主题（8 音长句）× 加速战鼓；29.5 二吼
四   40–50     龙语：合唱 pad + 心跳鼓；44.8–48.4 龙焰噪声；46.0/46.95/
               47.9/48.85 四连上行音阶（逐字点亮）；49.8 全亮叮
五   50–60     黎明：全奏高潮（大调色彩）+ 起飞 whoosh；56 起渐弱，
               60 秒处归静（与开头无缝衔接）
==== ========== =========================================================

渲染成本：约 190 万采样、数千音符事件，一次性渲染 ~10-15 秒，之后走
``assets/nordic_saga_ost_60s.wav`` 缓存（文件名带时长防旧缓存）。
"""
import math
import os

from world3d.engine.audio import Track, render_wav

from world3d.logic.nordic_saga import (DURATION, FIRE_T0, FIRE_T1, ROAR_T, RUNE_T0,
                               RUNE_T1, TAKEOFF_T)

# ---- D 小调音高表（Hz）----
D1, D2, A2, D3 = 36.71, 73.42, 110.00, 146.83
F3, G3, A3, Bb2, C3 = 174.61, 196.00, 220.00, 116.54, 130.81
D4, F4, A4, C4 = 293.66, 349.23, 440.00, 261.63
G4 = 392.00
D5, F5, A5, D6 = 587.33, 698.46, 880.00, 1174.66
FS4 = 369.99                                   # 黎明段的大调三音（F#）


def build_ost(path, duration=DURATION, rate=32000):
    """渲染整条配乐。返回 (路径, 实际秒数)。"""
    tr = Track(duration, rate)

    # ---- 低音弦 drone：D1+D2 五秒一段交叉淡化（0–60 的生命周期包络）----
    def drone_env(t0, t1):
        return (_smooth((t0 - 0.0) / 6.0) * (1.0 - _smooth((t0 - 53.0) / 6.5))
                * _smooth((t1 - 0.0) / 6.0) * (1.0 - _smooth((t1 - 53.0) / 6.5)))
    for i in range(12):
        t0 = i * 5.0
        if t0 >= 59.0:
            continue
        env = drone_env(t0, min(60.0, t0 + 5.35))
        if env <= 0.01:
            continue
        tr.tone(t0, 5.35, D1, wave='saw', amp=0.052 * env,
                attack=1.0, release=1.4, lp=0.050)
        tr.tone(t0, 5.35, D2, wave='saw', amp=0.034 * env,
                attack=1.0, release=1.4, lp=0.055)
        if 9.0 <= t0 <= 52.0:                   # 五度 A2：10–52 中段
            tr.tone(t0, 5.35, A2, wave='saw', amp=0.022 * env,
                    attack=1.2, release=1.4, lp=0.06)

    # ---- 风声：4s 一段，幅度缓慢起伏（雪原贯穿全曲）----
    for i in range(15):
        t0 = i * 4.0
        if t0 >= duration - 1.0:
            break
        amp = 0.026 + 0.014 * (0.5 + 0.5 * math.sin(i * 2.3))
        tr.noise(t0, 4.4, amp=amp, lp=0.06, seed=700 + i * 13)

    # ---- 冰铃：稀疏高音（0–50，空灵感）----
    for t0, f in ((3.0, D6), (7.5, A5), (13.0, F5), (17.5, D6), (21.5, A5),
                  (26.5, D6), (31.0, F5), (36.5, D5), (41.0, A5), (43.5, D6),
                  (52.0, D6), (54.5, A5)):
        tr.tone(t0, 1.6, f, wave='tri', amp=0.055,
                attack=0.005, release=1.3, lp=0.5)
        tr.tone(t0 + 0.02, 1.2, f * 0.5, wave='sine', amp=0.030,
                attack=0.005, release=1.0)

    # ---- 拨弦行进（10–22）：慢速 D 小调三音循环 ----
    pluck = (D3, A2, F3, C3)
    t, k = 10.5, 0
    while t < 22.0:
        f = pluck[k % 4]
        tr.tone(t, 0.5, f, wave='tri', amp=0.070,
                attack=0.004, release=0.42, lp=0.30)
        tr.tone(t, 0.25, f * 2.0, wave='sine', amp=0.028,
                attack=0.004, release=0.2)
        t += 1.25
        k += 1

    # ---- 战鼓（分段变速）----
    def kick(t0, amp):
        tr.tone(t0, 0.16, 128.0, 42.0, wave='sine', amp=amp,
                attack=0.001, release=0.07)
        tr.noise(t0, 0.03, amp=0.05, decay=40, lp=0.55, seed=int(t0 * 17) % 9973)

    t = 10.0
    while t < 21.5:                             # 进发：1.15s 匀速
        kick(t, 0.30)
        t += 1.15
    t = 22.0
    while t < 38.0:                             # 龙临：加速 1.0 → 0.5
        kick(t, 0.36)
        if int((t - 22.0) / 0.5) % 2 == 1:
            tr.noise(t + 0.25, 0.05, amp=0.06, decay=25, lp=0.6,
                     seed=int(t * 31) % 9973)
        t += 1.0 - 0.5 * min(1.0, (t - 22.0) / 16.0)
    t = 38.0
    while t < 45.0:                             # 龙语：心跳双击
        kick(t, 0.26)
        kick(t + 0.35, 0.15)
        t += 1.5
    t = 45.0
    while t < 50.0:
        kick(t, 0.30)
        t += 1.2
    t = 50.0
    while t < 56.0:                             # 黎明：全速轰鸣
        kick(t, 0.42)
        if int((t - 50.0) * 2) % 2 == 1:
            tr.noise(t, 0.06, amp=0.07, decay=22, lp=0.62,
                     seed=int(t * 7) % 9973)
        t += 0.5

    # ---- 龙吼（ROAR_T）+ 二声 ----
    for t0, amp in ((ROAR_T, 1.0), (29.5, 0.62)):
        tr.tone(t0, 1.6, 108.0, 30.0, wave='saw', amp=0.42 * amp,
                attack=0.05, release=0.85, lp=0.095)
        tr.noise(t0, 1.9, amp=0.36 * amp, decay=2.1, lp=0.13,
                 seed=int(t0 * 23) % 9973)
        tr.tone(t0 + 0.08, 2.0, 46.0, 27.0, wave='sine', amp=0.30 * amp,
                attack=0.12, release=1.0)

    # ---- 号角主题（22–38，8 音长句 = 16s 正好一轮）----
    horn = (D3, F3, G3, A3, G3, F3, C4, A3)
    for k, f in enumerate(horn):
        t0 = 22.5 + k * 2.0
        tr.tone(t0, 2.3, f, wave='saw', amp=0.095,
                attack=0.28, release=0.55, lp=0.125)
        tr.tone(t0 + 0.02, 2.1, f * 2.0, wave='saw', amp=0.036,
                attack=0.30, release=0.5, lp=0.16)
        tr.tone(t0, 2.3, f * 0.5, wave='sine', amp=0.05,
                attack=0.30, release=0.6)

    # ---- 合唱 pad（38–50）：D 小调长和弦，微失谐声部 ----
    for bi in range(3):
        t0 = 38.5 + bi * 4.0
        for f in (D3, A3, D4):
            tr.tone(t0, 4.6, f, wave='sine', amp=0.034,
                    attack=1.3, release=1.5, lp=0.4)
            tr.tone(t0, 4.6, f * 1.0035, wave='sine', amp=0.022,
                    attack=1.4, release=1.5, lp=0.4)
            tr.tone(t0, 4.6, f * 0.9965, wave='sine', amp=0.020,
                    attack=1.4, release=1.5, lp=0.4)

    # ---- 龙焰（FIRE_T0–FIRE_T1）：持续烈焰 + 低频轰鸣 + 噼啪 ----
    tr.noise(FIRE_T0, FIRE_T1 - FIRE_T0, amp=0.30, lp=0.30, seed=771)
    tr.tone(FIRE_T0, FIRE_T1 - FIRE_T0 - 0.2, 74.0, 44.0, wave='saw',
            amp=0.15, attack=0.18, release=0.5, lp=0.075)
    tr.noise(46.6, 1.6, amp=0.15, decay=2.4, lp=0.5, seed=913)
    tr.noise(47.4, 1.0, amp=0.11, decay=3.2, lp=0.52, seed=917)

    # ---- 符文点亮：四连上行音阶（与 _rune_glow 的脉冲同刻）----
    scale = (D5, F5, A5, D6)
    for k, f in enumerate(scale):
        t0 = 46.0 + k * 0.95
        tr.tone(t0, 1.5, f, wave='tri', amp=0.105,
                attack=0.004, release=1.1, lp=0.55)
        tr.tone(t0 + 0.02, 1.0, f * 0.5, wave='sine', amp=0.055,
                attack=0.004, release=0.8)
    tr.tone(RUNE_T1, 2.4, D6, wave='tri', amp=0.115,
            attack=0.004, release=2.0, lp=0.55)     # 全亮叮
    tr.tone(RUNE_T1, 2.4, A5, wave='tri', amp=0.085,
            attack=0.004, release=2.0, lp=0.55)

    # ---- 起飞 whoosh（TAKEOFF_T 起，上行滑音 + 气流）----
    tr.tone(TAKEOFF_T, 2.6, 170.0, 880.0, wave='saw', amp=0.10,
            attack=0.65, release=0.5, lp=0.26)
    tr.noise(TAKEOFF_T + 0.1, 2.4, amp=0.15, lp=0.38, seed=881)

    # ---- 黎明高潮主题（50–58，每 1s 一音，大调色彩收束）----
    climax = (D4, D4, F4, A4, G4, F4, FS4, D4)
    for k, f in enumerate(climax):
        t0 = 50.0 + k * 1.0
        tr.tone(t0, 1.15, f, wave='saw', amp=0.115,
                attack=0.06, release=0.4, lp=0.14)
        tr.tone(t0, 1.15, f * 2.0, wave='saw', amp=0.042,
                attack=0.08, release=0.4, lp=0.2)
        tr.tone(t0, 1.15, f * 0.5, wave='sine', amp=0.048,
                attack=0.08, release=0.45)
    # 高潮 pad：D 大调色彩（D-A-F#-D）
    for bi in range(2):
        t0 = 50.2 + bi * 4.0
        for f in (D3, A3, FS4):
            tr.tone(t0, 4.4, f, wave='sine', amp=0.036,
                    attack=1.1, release=1.4, lp=0.4)

    # ---- 尾声：D2 长音渐隐（56.5–60，与 t=0 的静默衔接）----
    tr.tone(56.5, 3.4, D2, wave='sine', amp=0.045,
            attack=1.2, release=2.0, lp=0.06)
    tr.tone(57.0, 3.0, D3, wave='sine', amp=0.028,
            attack=1.2, release=1.8, lp=0.12)

    return render_wav(tr, path)


def _smooth(u):
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)


def ensure_ost(path=None, duration=DURATION, force=False):
    """拿到配乐 WAV：有缓存直接用，缺失/强制时现场渲染（打印进度）。"""
    path = path or os.path.join('assets', f'nordic_saga_ost_{int(duration)}s.wav')
    if os.path.exists(path) and not force:
        return path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    print('[audio] rendering nordic saga OST (one-time, ~12s) ...', flush=True)
    t0 = os.times()[4]
    out, dur = build_ost(path, duration=duration)
    print(f'[audio] OST ready: {out} ({dur:.0f}s, '
          f'{os.path.getsize(out) / 1048576:.1f}MB, '
          f'{os.times()[4] - t0:.1f}s)', flush=True)
    return out

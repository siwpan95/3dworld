# -*- coding: utf-8 -*-
"""kkrieger 配乐（logic 层）：程序化生成的电子乐，纯 Python 离线渲染。

与 .kkrieger 的"音乐也是程序生成"对齐：这里不存任何音频素材，只有一份
乐谱规则——首次运行渲染成 WAV 缓存（``assets/kkrieger_ost_150s.wav``），之后
由 :class:`engine.audio.TimelineAudio` 与动画时间轴同步播放。

曲式与动画**共用同一套时间常量**（幕边界/开门时刻/核心脉冲周期/两次爆发），
所以音画同步是"编译期"对齐，不是运行时对时：

==== ========== =========================================================
幕   时间        音乐
==== ========== =========================================================
一   0–24       氛围铺垫：pad 和弦 + 底鼓 4s 进，贝斯/琶音渐入，开门气浪
二   24–48      全套鼓组，升降台低鸣（26–36），开门气浪
三   48–72      强度顶点：琶音升八度、16 分镲、双音警报、核心亚低频脉冲
                （周期 6s，与纪念碑核心的视觉脉冲同相）
四   72–96      拆解：鼓/贝斯/琶音依次退出，灯带熄灭的"哒"声，留 pad
五   96–118     心跳 → 高速突入（99 起全开）→ 114 起噪音上升 → 117.5
                核心爆发（低频坠落 + 噪声爆闪）→ 白闪尾音
六   118–150    圣殿：木琴旋律 + 稀疏鼓点渐入（126 起）→ 136 点火 riser
                → 141.5 反应堆爆发（二次低频坠落）→ 142–147 pad 余晖
                → 148 后渐寂（与开头静默衔接成环）
==== ========== =========================================================

技术约束：纯 Python 无 numpy，单声道 32000Hz；约 2.2 万个音符事件、
两千五百万次采样运算，首帧渲染约 25~35 秒（一次性，之后走缓存）。
"""
import os

from engine.audio import Track, render_wav

# ---- 乐理常量（A 小调，120 BPM：beat=0.5s，bar=2.0s ⇒ 幕边界全落在小节线）----
BEAT = 0.5
BAR = 2.0
# 和弦进行 Am-F-C-G（根音频率 + 三和弦半音），每小节换一个
CHORDS = (
    (55.00, (0, 3, 7)),      # A1 小三
    (43.65, (0, 4, 7)),      # F1 大三
    (65.41, (0, 4, 7)),      # C2 大三
    (49.00, (0, 4, 7)),      # G1 大三
)
# 琶音音池：A 小调五声（A3 C4 D4 E4 G4 A4）
PENTA = (220.00, 261.63, 293.66, 329.63, 392.00, 440.00)

# ---- 与动画共享的同步点（logic.kkrieger 的常量，导入而非复写）----
from logic.kkrieger import A2, A3, A4, A5, A6, DURATION        # noqa: E402
from logic.kkrieger import DOOR_OPEN_T, V_DOOR_OPEN_T          # noqa: E402
from logic.kkrieger import K2_BOOM_T                           # noqa: E402

BOOM_T = 117.5                    # 核心爆发（音频与画面同一时刻）
SPARK_T0 = 114.0                  # 碎片飞散（ riser 与爆发之间）
STRIP_CLUNK_T0, STRIP_CLUNK_STEP = 76.0, 0.85   # 与灯带逐段熄灭同步


def build_ost(path, duration=DURATION, rate=32000):
    """渲染整条配乐。返回 (路径, 实际秒数)。"""
    tr = Track(duration, rate)
    nbars = int(duration / BAR)

    # ---- Pad 和弦铺底：每小节三音，长音包络，低通闷化 ----
    for b in range(nbars):
        t0 = b * BAR
        # 撤离末留白 / 爆发尾寂（118–121 留给白闪与圣殿开门）/ 尾声渐寂
        if 92.0 <= t0 < 96.0 or 117.5 <= t0 < 121.0 or t0 >= 148.0:
            continue
        root, semi = CHORDS[b % 4]
        for k, s in enumerate(semi):
            f = root * 2 ** (s / 12) * 2.0        # 抬一个八度进 pad 音区
            amp = 0.050 if k else 0.065
            if t0 >= A6:                          # 圣殿段：pad 更空更轻
                amp *= 0.8
            tr.tone(t0, BAR + 0.4, f, wave='saw', amp=amp,
                    attack=0.35, release=0.55, lp=0.06)

    # ---- 底鼓：四分音符；第五幕开头是心跳（每两拍一下）----
    t = 4.0
    while t < duration - 0.1:
        in_iv = t < 72.0
        in_v = 96.0 <= t < 117.5
        heart = 96.0 <= t < 99.0
        if (in_iv and t >= 4.0 and not (80.0 <= t < 96.0)) or in_v:
            if not heart or (int(t / BEAT) % 2 == 0):
                tr.tone(t, 0.14, 150.0, 42.0, wave='sine', amp=0.50,
                        attack=0.001, release=0.05)
                tr.noise(t, 0.02, amp=0.05, decay=40, lp=0.6, seed=int(t * 13))
        t += BEAT

    # ---- 贝斯：八分音符，[根,根,高八度,根] 花型 ----
    pat = (0, 0, 12, 0, 0, 7, 0, 12)
    t = 4.0
    step = 0
    while t < duration - 0.1:
        if (4.0 <= t < 72.0) or (96.0 <= t < 117.5):
            root, _ = CHORDS[int(t / BAR) % 4]
            f = root * 2 ** (pat[step % 8] / 12)
            amp = 0.15 if t < 96.0 else 0.17
            tr.tone(t, 0.22, f, wave='square', amp=amp, duty=0.35,
                    attack=0.003, release=0.04, lp=0.22)
        t += BEAT / 2
        step += 1

    # ---- 琶音：十六分，五声音阶；过载/突入段升八度 ----
    t = 12.0
    step = 0
    while t < duration - 0.1:
        active = (12.0 <= t < 24.0) or (24.0 <= t < 80.0) or (96.0 <= t < 117.5)
        octave = 2.0 if (48.0 <= t < 72.0 or 109.0 <= t < 117.5) else 1.0
        if active and (step % 8) != 7:            # 每 8 个十六分留一拍呼吸
            f = PENTA[(step * 3) % 6] * octave
            amp = 0.085 if t < 48.0 else 0.105
            tr.tone(t, 0.11, f, wave='square', amp=amp, duty=0.25,
                    attack=0.002, release=0.03, lp=0.35)
        t += BEAT / 4
        step += 1

    # ---- 镲：反拍八分；过载/突入段加密到十六分 ----
    t = 8.0
    while t < duration - 0.1:
        off = (t / BEAT) % 1.0
        dense = 48.0 <= t < 72.0 or 99.0 <= t < 117.5
        if ((abs(off - 0.5) < 1e-6) and (24.0 <= t < 72.0 or 96.0 <= t < 117.5)) \
                or (dense and t >= 48.0):
            tr.noise(t, 0.035, amp=0.06 if dense else 0.05, decay=30, lp=0.55,
                     seed=int(t * 31))
        t += BEAT / 4 if dense else BEAT / 2

    # ---- 事件音效：与动画逐秒对齐 ----
    # 开门气浪（第一幕三道 + 第五幕三道）：噪声气浪 + 金属闷响
    for t0 in tuple(DOOR_OPEN_T) + tuple(V_DOOR_OPEN_T):
        tr.noise(t0, 0.7, amp=0.20, decay=3.0, lp=0.10, seed=int(t0 * 7))
        tr.tone(t0 + 0.55, 0.09, 72.0, wave='square', amp=0.16, duty=0.5,
                attack=0.002, release=0.03, lp=0.3)
    # 升降台低鸣（第二幕 26–36）：频率/音量随行程爬升
    for k in range(10):
        t0 = 26.0 + k
        tr.tone(t0, 1.05, 44.0 + 0.8 * k, wave='saw', amp=0.035 + 0.006 * k,
                attack=0.2, release=0.25, lp=0.05)
    # 双音警报（第三幕，每 2s）：与能量束增亮同步
    t = 48.5
    k = 0
    while t < 71.5:
        f = 784.0 if k % 2 == 0 else 622.0
        tr.tone(t, 0.16, f, wave='square', amp=0.075, duty=0.5,
                attack=0.004, release=0.04, lp=0.5)
        t += 2.0
        k += 1
    # 核心亚低频脉冲：周期 6s、峰值相位与 _core_pose 的 _pulse(t,6) 对齐
    for t0 in (49.5, 55.5, 61.5, 67.5):
        tr.tone(t0, 0.55, 52.0, 38.0, wave='sine', amp=0.34,
                attack=0.01, release=0.2)
    # 灯带逐段熄灭的轻响（第四幕，与 STRIP_BLACKOUT 同步）
    for i in range(14):
        tr.noise(STRIP_CLUNK_T0 + STRIP_CLUNK_STEP * i, 0.09, amp=0.05,
                 decay=9, lp=0.28, seed=40 + i)
    # 碎片飞散（爆发前兆）：短促金属碎裂
    tr.noise(SPARK_T0, 0.5, amp=0.16, decay=4.0, lp=0.35, seed=77)
    # 爆发前 riser：噪声渐强 + 音高爬升
    tr.noise(114.0, 3.5, amp=0.26, lp=0.45, seed=99)
    tr.tone(114.0, 3.5, 220.0, 1760.0, wave='saw', amp=0.12,
            attack=1.2, release=0.05, lp=0.3)
    # 核心爆发：低频坠落 + 爆闪 + 尾音
    tr.tone(BOOM_T, 1.1, 120.0, 28.0, wave='sine', amp=0.85,
            attack=0.002, release=0.35)
    tr.noise(BOOM_T, 1.6, amp=0.48, decay=2.5, lp=0.16, seed=123)
    tr.tone(BOOM_T + 0.1, 0.45, 880.0, wave='square', amp=0.10, duty=0.3,
            attack=0.001, release=0.2, lp=0.5)
    tr.tone(118.2, 1.8, 30.0, 24.0, wave='sine', amp=0.30,
            attack=0.05, release=0.9)

    # ---- 第六幕：圣殿 ----
    # 木琴旋律：三角波短音，五声音阶漫游（121–136，稀疏，空灵）
    melody = (0, 2, 4, 2, 5, 4, 2, 0, 1, 3, 5, 3, 4, 3, 1, 0)
    for k, deg in enumerate(melody):
        t0 = 121.0 + k * BEAT
        f = PENTA[deg] * 2.0
        tr.tone(t0, 0.42, f, wave='tri', amp=0.085,
                attack=0.004, release=0.30, lp=0.55)
        tr.tone(t0 + 0.02, 0.30, f * 0.5, wave='sine', amp=0.05,
                attack=0.004, release=0.25)
    # 稀疏鼓点渐入（126 起）：心跳式双击，141.4 前停
    t = 126.0
    while t < 141.4:
        tr.tone(t, 0.13, 130.0, 44.0, wave='sine', amp=0.26,
                attack=0.001, release=0.05)
        tr.tone(t + BEAT / 2, 0.11, 110.0, 42.0, wave='sine', amp=0.14,
                attack=0.001, release=0.05)
        t += BEAT * 2
    # 圣殿贝斯：根音长八分（126–141.5）
    t = 126.0
    step = 0
    while t < 141.5:
        if step % 2 == 0:
            root, _ = CHORDS[int(t / BAR) % 4]
            tr.tone(t, 0.45, root * 2.0, wave='square', amp=0.10, duty=0.3,
                    attack=0.01, release=0.08, lp=0.18)
        t += BEAT / 2
        step += 1
    # 点火 riser（136–141.3）：与反应堆 glow 爬升同窗口
    tr.noise(136.0, 5.3, amp=0.24, lp=0.42, seed=77)
    tr.tone(136.0, 5.3, 165.0, 1568.0, wave='saw', amp=0.11,
            attack=1.8, release=0.05, lp=0.3)
    # 种子收拢的"献祭"三连音（与 seed 收敛窗口同步）
    for k in range(3):
        tr.tone(137.5 + k * 0.75, 0.18, 1320.0 - k * 220.0, wave='tri',
                amp=0.09, attack=0.003, release=0.10)
    # 二次爆发：反应堆点火（141.5，与 K2_BOOM_T 同刻）
    tr.tone(K2_BOOM_T, 1.2, 140.0, 26.0, wave='sine', amp=0.90,
            attack=0.002, release=0.40)
    tr.noise(K2_BOOM_T, 1.8, amp=0.50, decay=2.5, lp=0.15, seed=124)
    tr.tone(K2_BOOM_T + 0.1, 0.5, 1108.0, wave='square', amp=0.10, duty=0.3,
            attack=0.001, release=0.25, lp=0.5)
    tr.tone(142.0, 2.2, 32.0, 22.0, wave='sine', amp=0.32,
            attack=0.06, release=1.0)
    # 尾声余晖：传送回开场高空的长音渐消（144–147.5）
    tr.tone(144.0, 3.5, PENTA[0], wave='sine', amp=0.10,
            attack=0.8, release=2.2, lp=0.10)
    tr.tone(144.5, 3.0, PENTA[0] * 1.5, wave='sine', amp=0.06,
            attack=0.8, release=2.0, lp=0.12)

    return render_wav(tr, path)


def ensure_ost(path=None, duration=DURATION, force=False):
    """拿到配乐 WAV：有缓存直接用，缺失/强制时现场渲染（打印进度）。

    文件名带时长后缀——时长从 120s 改到 150s 之后，不带时长的旧缓存
    会被当成现成文件直接播（音画错位），带后缀就不会踩到。
    """
    path = path or os.path.join('assets', f'kkrieger_ost_{int(duration)}s.wav')
    if os.path.exists(path) and not force:
        return path
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    print('[audio] rendering kkrieger OST (one-time, ~20s) ...', flush=True)
    t0 = os.times()[4]
    out, dur = build_ost(path, duration=duration)
    print(f'[audio] OST ready: {out} ({dur:.0f}s, '
          f'{os.path.getsize(out) / 1048576:.1f}MB, '
          f'{os.times()[4] - t0:.1f}s)', flush=True)
    return out

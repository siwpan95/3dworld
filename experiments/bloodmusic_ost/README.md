# experiments/bloodmusic_ost —— bloodmusic 配乐重制试点（P0）

> 目标：用 [bloodmusic](https://pypi.org/project/bloodmusic/)（程序化音频合成引擎）
> 重制 kkrieger 第六幕（圣殿 118–150s），与自研 OST 做 A/B 对照。
> 依据：`docs/生态库引入评估.md`。2026-10-09。

## 分工设计

**3dworld 管「何时响」，bloodmusic 管「声音内容」**——每轨以休止符对齐到
64 拍（32s）总时间线，分轨渲染后相加。时间常量（BPM=120 / A 小调 / 幕边界）
与原曲完全一致。

## 对照表（六要素）

| 要素 | 原曲（logic/kkrieger_audio.py） | 重制（bloodmusic） |
|------|--------------------------------|--------------------|
| 木琴旋律 121–136 | tri 波短音 + 低八度垫音 | **MARIMBA 预设** + sine 低八度 |
| 心跳鼓 126–141.4 | sine 低频双击（55Hz） | **DrumPattern 步进**（DrumSoundParams 55Hz） |
| 圣殿贝斯 126–141.5 | square 根音（G2/A2/F2/C3） | MusicPattern square、lp 700Hz |
| pad 铺底 | saw 三音和弦 | saw 根音长音、lp 300Hz、混响 |
| 点火爆发 141.5 | 低频坠落 + 噪声 | C1 长共鸣（PAD 包络 2s） |
| 尾晖 144–147.5 | A4 sine 渐消 | A4 长音 + 混响 |

## A/B 客观对照（RMS 包络，每秒）

```
sec  :    3    8   12   20   23   24   25   27
原版 : 0.03 0.07 0.09 0.11 0.34 0.35 0.12 0.05
重制 : 0.08 0.15 0.14 0.14 0.17 0.30 0.15 0.08
```

结构对齐 ✓：旋律进入 3s（休止符对齐验证）、鼓/贝斯进入 8s、爆发 23.5–24s、
尾晖渐寂 25–31s。总 RMS：原版 0.121 / 重制 0.107。

## 差异说明（诚实记录）

- **点火 riser**（136–141.3 噪声上升）v1 **未实现**（简化）；
- 爆发形态：原版为「坠落滑音 + 噪声爆闪」，重制为「C1 长共鸣」——受音色体系差异限制；
- 采样率：原版 32kHz 单声道 / 重制 44.1kHz（bloodmusic 默认）。

## 复现

```bash
# 需环境装有 bloodmusic（依赖清单见 docs/生态库引入评估.md；本项目 .venv 不含它）
<envs python> experiments/bloodmusic_ost/remake_act6.py
# 原版对照段（已切好；重切用）：
ffmpeg -y -ss 118 -t 32 -i assets/kkrieger_ost_150s.wav -c:a pcm_s16le \
    experiments/bloodmusic_ost/act6_original.wav
```

产物：`act6_remake_bloodmusic.wav` / `act6_original.wav`（WAV 不入库，脚本可复现）。

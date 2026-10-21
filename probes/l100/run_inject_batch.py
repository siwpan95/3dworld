# -*- coding: utf-8 -*-
"""把新生成的 10 段 AI 镜头回插到对应集，逐集打印进度。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import l100_inject as I  # noqa: E402

CLIPS = os.path.join(I.OUT, 'ai_clips')

# 集号 -> AI 片段文件名（按 scene -> clip 一一对应）
JOBS = [
    (3,  '戈壁公路检查站*'),
    (4,  '俯瞰戈壁公路*'),
    (5,  '傍晚戈壁废弃服务区*'),
    (8,  '清晨戈壁公路*'),
    (9,  '正午废弃公路养护工区*'),
    (12, '深夜戈壁公路路肩*'),
    (17, '下午黑戈壁深处*'),
    (23, '傍晚戈壁公路_一块巨大*'),
    (25, '深夜戈壁公路_一辆深色卡车*'),
    (28, '凌晨戈壁公路临时检查站*'),
]

import glob  # noqa: E402

for ep, pat in JOBS:
    hits = sorted(glob.glob(os.path.join(CLIPS, pat)))
    if not hits:
        print(f'[skip] ep{ep:03d}: no clip for {pat}', flush=True)
        continue
    print(f'[run ] ep{ep:03d} <- {os.path.basename(hits[0])}', flush=True)
    I.inject(ep, [hits[0]])
print('[done] all injected', flush=True)

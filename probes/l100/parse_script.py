# -*- coding: utf-8 -*-
"""《最后一百公里》剧本 → 结构化 JSON。

扫描 ``projects/最后一百公里/05-分集剧本/*.md``，抽出每一集的场次
（内外景 / 地点 / 时间 / 出场）以及该场次下的【动作】与台词行，
供 animatic 渲染流水线（l100_build / l100_tts / l100_assemble）消费。

用法::

    python probes/l100/parse_script.py            # 写到 out/l100/script.json
    python probes/l100/parse_script.py --summary  # 只打统计
"""
from __future__ import annotations

import argparse
import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../3dworld/probes/l100
PROBES = os.path.dirname(_HERE)                              # .../3dworld/probes
ROOT = os.path.dirname(PROBES)                               # .../3dworld
OUT_DIR = os.path.join(PROBES, 'out', 'l100')
SCRIPT_DIR = os.path.join(os.path.dirname(ROOT), 'projects', '最后一百公里',
                          '05-分集剧本')

FILES = ['第001-003集-v1.md', '第004-007集-v1.md', '第008-010集-v1.md',
         '第011-015集-v1.md', '第016-020集-v1.md', '第021-025集-v1.md',
         '第026-030集-v1.md']

# 场次标题：1-1 外景 / 场院 / 凌晨   |   20-0 cold open / 戈壁 / （字幕：三十年前）
SCENE_RE = re.compile(
    r'^\**\s*(?P<ep>\d{1,3})-(?P<sc>\d{1,3})\s+'
    r'(?P<ie>内景|外景|cold open|COLD OPEN|闪回|插叙)\s*/\s*'
    r'(?P<place>[^/]+?)\s*/\s*(?P<time>[^\*]+?)\s*\**\s*$')
EP_RE = re.compile(r'^##\s*第\s*(?P<ep>\d{1,3})\s*集[《](?P<title>[^》]+)[》]')
# 台词：石满仓：（看着纸）签了干什么。 | 石满仓（低声）：还不到你。
DLG_RE = re.compile(
    r'^(?P<who>[^\s：:（()）]{1,10})(?:[（(][^（）()]{0,22}[）)])?\s*[:：]\s*(?P<line>.+)$')
ACT_RE = re.compile(r'^【(?P<kind>动作|字幕|音效|OS|VO|画外音|旁白)】\s*(?P<text>.*)$')
SUB_RE = re.compile(r'^【(?P<text>[^】]*)】\s*$')

# 主要人物 → 固定配音声线（edge-tts）
VOICES = {
    '石满仓': 'zh-CN-YunjianNeural',   # 男·沉稳
    '苗小满': 'zh-CN-XiaoxiaoNeural',  # 女·克制
    '汤宁':   'zh-CN-XiaoyiNeural',    # 女·明快
    '二柱':   'zh-CN-YunxiNeural',     # 男·年轻
    '__旁白__': 'zh-CN-YunyangNeural', # 男·播报
}
DEFAULT_VOICE = 'zh-CN-YunyangNeural'

NOISE = ('**【承诺与衔接】**', '【承诺与衔接】', '**第二幕', '## 第二幕',
         '---', '**集尾钩子】', '```')


def parse_line(raw):
    """把一行原文归到 (kind, payload) 上。"""
    s = raw.rstrip()
    t = s.strip()
    # 去掉可能存在的加粗包裹
    stripped = t.strip('*').strip()
    if not stripped:
        return None
    m = ACT_RE.match(stripped)
    if m:
        return ('act', m.group('kind'), m.group('text').strip())
    if stripped.startswith('**【') or stripped.startswith('【'):
        if stripped.startswith('**【') and stripped.endswith('】**'):
            inner = stripped.strip('*').strip('【】')
            return ('tag', '', inner)
        if t.startswith('【') and t.endswith('】'):
            return ('tag', '', t.strip('【】'))
    m = DLG_RE.match(stripped)
    if m:
        who = m.group('who').strip('* ')
        if who in ('出场', '人物', '场景', '旁白提示'):
            return ('meta', who, m.group('line').strip())
        return ('dlg', who, m.group('line').strip())
    # 整行就是一个括号注记 + 台词（"（低声）还不到你"）跳过
    return None


def clean_speech(text):
    """把台词里不适合念出来的部分处理掉：舞台指示、书名号等。"""
    s = text
    # 去掉（ Medal ）式舞台指示，交给 ACTION 轨
    s = re.sub(r'（[^）]{0,20}）', '', s)
    s = s.replace('——', '，').replace('…', '，')
    s = re.sub(r'\*+', '', s)
    s = s.strip(' 　')
    s = re.sub(r'\s{2,}', ' ', s)
    return s


def parse_file(path, bucket):
    ep_no = None
    scene = None
    with open(path, encoding='utf-8') as fh:
        lines = fh.readlines()
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        m = EP_RE.match(raw.rstrip())
        if m:
            ep_no = int(m.group('ep'))
            bucket[ep_no] = {'ep': ep_no, 'title': m.group('title').strip(),
                             'scenes': []}
            scene = None
            continue
        if ep_no is None:
            continue
        if any(s.startswith(n) for n in NOISE):
            continue
        # 代码块围栏
        if s.startswith('```'):
            continue
        body = s.strip('*').strip()
        m = SCENE_RE.match(raw.rstrip())
        if not m:
            m = SCENE_RE.match(body)
        if m and int(m.group('ep')) == ep_no:
            scene = {
                'scene_id': f"{int(m.group('ep')):03d}-{int(m.group('sc')):02d}",
                'ep': ep_no,
                'idx': int(m.group('sc')),
                'interior': m.group('ie'),
                'place': m.group('place').strip(),
                'time': m.group('time').strip().strip('（）()').strip(),
                'blocks': [],
            }
            bucket[ep_no]['scenes'].append(scene)
            continue
        if scene is None:
            continue
        # 写作说明 / 承诺区：遇到 **xxx** 小节标题就停掉该场次的收集
        if body.startswith('【承诺与衔接】') or body.startswith('第二幕'):
            scene = None
            continue
        parsed = parse_line(raw)
        if parsed:
            scene['blocks'].append(parsed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--summary', action='store_true')
    args = ap.parse_args()

    bucket = {}
    for name in FILES:
        p = os.path.join(SCRIPT_DIR, name)
        if not os.path.exists(p):
            print('[warn] missing', name)
            continue
        parse_file(p, bucket)

    eps = [bucket[k] for k in sorted(bucket)]
    total_scenes = sum(len(e['scenes']) for e in eps)
    total_dlg = 0
    total_act = 0
    chars = {}
    for e in eps:
        for sc in e['scenes']:
            for b in sc['blocks']:
                if b[0] == 'dlg':
                    total_dlg += 1
                    chars[b[1]] = chars.get(b[1], 0) + 1
                elif b[0] == 'act':
                    total_act += 1

    print(f'解析完成：{len(eps)} 集 / {total_scenes} 场次 / '
          f'{total_dlg} 条台词 / {total_act} 条舞台提示')
    top = sorted(chars.items(), key=lambda kv: -kv[1])
    print(f'全部角色 {len(top)} 个，出场最多的前 14：')
    for who, n in top[:14]:
        print(f'  {who:<10s} {n:>4d}')

    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, 'script.json')
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump({'voices': VOICES, 'default_voice': DEFAULT_VOICE,
                   'episodes': eps}, fh, ensure_ascii=False, indent=1)
    print('[out]', out, f'{os.path.getsize(out) / 1024:.0f} KB')

    if args.summary:
        for e in eps:
            print(f"  {e['ep']:03d} 《{e['title']}》 {len(e['scenes'])} 场")


if __name__ == '__main__':
    main()

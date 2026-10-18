# -*- coding: utf-8 -*-
"""`docs/README.md` 的**生成器**（P4：文档站的第一块 —— 一个能被门禁核对新鲜度的索引）。

为什么要生成而不是手写：手写的索引**一定会腐**（加了文档忘了加一行）。
生成 + 门禁核对，就等于"加了文档但没更新索引"这件事会在 `verify.py` 里变红。

三条自我约束（照抄 `repo-consistency-gates` 的踩坑表）：

1. **只含事实，不含时间戳/解释器版本** —— 否则换台机器 `--check` 就误报过期；
2. **不写会随每次编辑抖动的量**（行数、字数）—— 否则索引天天"过期"，
   人会开始无视这条门禁；
3. **纯标准库、确定性**：同一个仓库状态必须生成逐字节相同的输出。

用法::

    python scripts/docs_index.py            # 打印到 stdout
    python scripts/docs_index.py --write    # 写 docs/README.md
    python scripts/docs_index.py --check    # 与磁盘比，不一致则 rc=1
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys

# stdio 编码兜底（必须在任何 print 之前）：见 scripts/_console.py
_spec = importlib.util.spec_from_file_location(
    '_console', os.path.join(os.path.dirname(os.path.abspath(__file__)), '_console.py'))
_console = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_console)
_console.ensure_utf8_stdio()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, 'docs')
INDEX = os.path.join(DOCS, 'README.md')

#: 分类：**按文件名前缀**（而不是靠内容关键词猜），顺序即渲染顺序。
#: 第一个匹配上的分类生效，所以"更具体的放前面"。
TRACKS = (
    ('P0 试点转正', ('P0-',)),
    ('P1 库化', ('P1-',)),
    ('P2 分层归位', ('P2-',)),
    ('P3 帧图管线', ('P3-',)),
    ('P4 收尾（文档/门禁/脚手架）', ('P4-',)),
    ('③ 可视化导演台', ('3_',)),
    ('④ 引擎渲染扩展', ('4-', '4_')),
    ('BQM 机制致敬线（并行轨道）', ('BQM',)),
    ('规划与总览', ('5_', '补完计划', '引擎设计')),
    ('专题笔记与指南', ()),          # 兜底：剩下的都进这里
)

SUMMARY_LIMIT = 58


def _title(text):
    """取第一个 `# ` 标题（去掉行内的 markdown 强调）。"""
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('# '):
            return _plain(s[2:].strip())
    return ''


def _plain(s):
    s = re.sub(r'\*\*(.+?)\*\*', r'\1', s)
    s = re.sub(r'`([^`]+)`', r'\1', s)
    s = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', s)
    return s.strip()


def _summary(text):
    """取标题之后的第一段**散文**。

    必须跳过的东西（第一版全踩了，摘要列因此全是 `---` 或 ASCII 图）：

    * 围栏代码块内部的任何行（要按 ```` ``` ```` 的开关状态跟踪，不能只看单行）；
    * 分隔线（`---` / `===` / `***`）—— 它**不以 `#`/`>`/`|` 开头**，很容易被当成正文；
    * 引用块、标题、表格、列表项；
    * 对不上"像一句话"的行（例如 ASCII 图的坐标轴）。
    """
    started = False
    in_fence = False
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('```'):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not started:
            if s.startswith('# '):
                started = True
            continue
        if not s or s.startswith(('#', '>', '|', '*', '-', '=', '·', '+')):
            continue
        if re.fullmatch(r'[-=_*`\s]+', s):          # 分隔线兜底
            continue
        s = _plain(s)
        if not s:
            continue
        return (s[:SUMMARY_LIMIT] + '…') if len(s) > SUMMARY_LIMIT else s
    return ''


def _track(name):
    for label, prefixes in TRACKS:
        for p in prefixes:
            if name.startswith(p):
                return label
        if not prefixes:
            return label
    return TRACKS[-1][0]


def collect():
    """→ [(track, name, title, summary)]，按 (track 顺序, 文件名) 排。"""
    order = {label: i for i, (label, _p) in enumerate(TRACKS)}
    rows = []
    for fn in sorted(os.listdir(DOCS)):
        if not fn.endswith('.md') or fn == 'README.md':
            continue
        text = open(os.path.join(DOCS, fn), encoding='utf-8').read()
        rows.append((_track(fn), fn, _title(text) or fn[:-3], _summary(text)))
    rows.sort(key=lambda r: (order.get(r[0], 99), r[1]))
    return rows


def build_index() -> str:
    rows = collect()
    by_track = {}
    for track, fn, title, summary in rows:
        by_track.setdefault(track, []).append((fn, title, summary))

    out = [
        '# 文档索引',
        '',
        '> **本文件由 `scripts/docs_index.py` 生成，不要手改** —— ',
        '> 加了文档就重跑一次：`python scripts/docs_index.py --write`。',
        '> 门禁 `docs-index-fresh`（`python scripts/verify.py`）会核对它是否最新，',
        '> 所以"新加了文档但忘了登记"会在验收里变红，而不是悄悄腐烂。',
        '',
        f'共 **{len(rows)} 篇**，按轨道分组：',
        '',
    ]
    for label, _prefixes in TRACKS:
        items = by_track.get(label)
        if not items:
            continue
        out.append(f'## {label}')
        out.append('')
        out.append('| 文档 | 讲什么 |')
        out.append('|------|--------|')
        for fn, title, summary in items:
            out.append(f'| [`{title}`]({fn}) | {summary} |')
        out.append('')
    out.append('---')
    out.append('')
    out.append('> 图片产物（剧照 / 深度图小窗等）已随 demo 迁往独立仓 '
               '`siwpan/3dworld-demos`（其 `docs/*.png`），本仓不再生成。')
    return '\n'.join(out) + '\n'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='生成 / 核对 docs/README.md 索引')
    ap.add_argument('--write', action='store_true', help='写入 docs/README.md')
    ap.add_argument('--check', action='store_true', help='与磁盘比对，不一致 rc=1')
    args = ap.parse_args(argv)
    text = build_index()
    if args.write:
        open(INDEX, 'w', encoding='utf-8', newline='\n').write(text)
        print(f'[docs_index] 已写入 {os.path.relpath(INDEX, ROOT)}'
              f'（{len(collect())} 篇）')
        return 0
    if args.check:
        if not os.path.exists(INDEX):
            print('[docs_index] docs/README.md 不存在 —— 跑 --write 生成')
            return 1
        cur = open(INDEX, encoding='utf-8').read()
        if cur != text:
            print('[docs_index] docs/README.md **不是最新的** —— 跑 --write 重新生成')
            return 1
        print(f'[docs_index] 最新（{len(collect())} 篇）')
        return 0
    sys.stdout.write(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())

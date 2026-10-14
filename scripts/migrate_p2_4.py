# -*- coding: utf-8 -*-
"""P2-4 迁移脚本：把 `core/logic/engine/presentation/games/host/cli` 收进 `world3d.*`。

用法（从仓库根运行；**先 git mv 包、再跑本脚本**）::

    python scripts/migrate_p2_4.py --dry-run     # 只报告
    python scripts/migrate_p2_4.py               # 真改

改三类东西（**三类都必须覆盖，只 grep 一种就会漏**）：

1. **import 语句**：
   `from world3d.core.x import Y` / **`from core import x as y`** / `import world3d.core.x`
   —— 三种形态都要改（P2-2 漏了第二种，被测试抓出来）；相对导入（`from . import x`）**不动**。
2. **字符串里的模块路径**：`'world3d.games.foo.kernel:Bar'`、`'world3d.logic.audio:ensure_ost'`
   —— 这类**正则改 import 永远碰不到**，而 `host/registry.py` 的 `SERVICES` 表、
   `core/contracts.py` 的契约清单、`world3d/_info.py` 的公共面清单都靠它，
   漏一处就是"配置树挂不起来 / 契约检查找不到模块"。
3. **反引号里的散文引用**：`` `world3d.engine.greedy_mesh` `` → `` `world3d.engine.greedy_mesh` ``
   —— 只改**反引号包起来**的，且**排除文件后缀**（`` `engine.py` `` 是文件名，不是模块路径）。

本脚本幂等：已经有 `world3d.` 前缀的不再重复加。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKGS = ('core', 'logic', 'engine', 'presentation', 'games', 'host', 'cli')
#: 反引号里不该被当成模块路径的"像扩展名"的段
EXTS = {'py', 'md', 'yml', 'yaml', 'json', 'txt', 'png', 'jpg', 'mp4', 'wav', 'sh',
        'exe', 'cfg', 'ini', 'toml', 'html', 'css', 'js', 'ts', 'csv', 'zip', 'obj'}
PKG_ALT = '|'.join(PKGS)

#: `from <pkg>[.mod] import ...`（含缩进；相对导入 `from .x` 不匹配）
RE_FROM = re.compile(rf'^(?P<indent>\s*)from (?P<pkg>{PKG_ALT})(?P<tail>\.[\w.]+)? import ')
#: `import <pkg>[.mod][ as alias]`（可能出现在逗号列表里）
RE_IMPORT_ITEM = re.compile(rf'(?<![\w.])(?P<pkg>{PKG_ALT})(?P<tail>\.[\w.]*)?')
#: 字符串里的模块路径（可带 `:Attr`）
RE_STR = re.compile(rf'(["\'])(?P<path>(?:{PKG_ALT})\.[a-z_][\w.]*(?::[A-Za-z_]\w*)?)\1')
#: 反引号里的散文引用
RE_TICK = re.compile(rf'`(?P<path>(?:{PKG_ALT})(?:\.[a-z_][\w.]*)+)`')


def _dunder_all_lines(text):
    """`__all__ = [...]` 这类通常不含模块路径；此处只用于统计。"""
    return text.count('__all__')


def rewrite_python(text, stats):
    out, changed = [], 0
    for line in text.split('\n'):
        new = line
        # ① from <pkg>[.mod] import
        m = RE_FROM.match(new)
        if m:
            new = RE_FROM.sub(
                lambda mm: f'{mm.group("indent")}from world3d.{mm.group("pkg")}'
                           f'{mm.group("tail") or ""} import ', new, count=1)
        # ② import <pkg>[.mod] [, ...] —— 逐个 token 处理
        mi = re.match(r'^(?P<indent>\s*)import (?P<body>.+)$', new)
        if mi:
            body = mi.group('body')
            parts = []
            for tok in body.split(','):
                stripped = tok.strip()
                mm = re.match(rf'^(?P<pkg>{PKG_ALT})(?P<rest>[\w.]*)(?P<as>\s+as\s+\w+)?$',
                              stripped)
                if mm:
                    stripped = (f'world3d.{mm.group("pkg")}{mm.group("rest")}'
                                f'{mm.group("as") or ""}')
                parts.append((' ' if tok.startswith(' ') else '') + stripped)
            new = f'{mi.group("indent")}import ' + ','.join(parts)
        # ③ 字符串里的模块路径
        def _str_sub(mm):
            path = mm.group('path')
            return (f'{mm.group(1)}world3d.{path}{mm.group(1)}'
                    if not path.startswith('world3d.') else mm.group(0))
        new = RE_STR.sub(_str_sub, new)
        if new != line:
            changed += 1
        out.append(new)
    # ④ 反引号散文
    def _tick(mm):
        path = mm.group('path')
        seg = path.split('.')[-1]
        if seg in EXTS or path.startswith('world3d.'):
            return mm.group(0)
        return f'`world3d.{path}`'
    joined = '\n'.join(out)
    before = joined
    joined = RE_TICK.sub(_tick, joined)
    if joined != before:
        stats['prose'] += joined.count('world3d.')
    stats['py'] += changed
    return joined


def rewrite_markdown(text, stats):
    def _tick(mm):
        path = mm.group('path')
        seg = path.split('.')[-1]
        if seg in EXTS or path.startswith('world3d.'):
            return mm.group(0)
        stats['md'] += 1
        return f'`world3d.{path}`'
    out = RE_TICK.sub(_tick, text)

    def _cmd(mm):
        if mm.group('path').startswith('world3d.'):
            return mm.group(0)
        stats['md'] += 1
        return f'python -m world3d.{mm.group("path")}'
    out = re.sub(r'python -m (?P<path>(?:cli)\.[\w.]+)', _cmd, out)
    for pat in (r'(?<![\w.])from (?P<p>core|logic|engine|presentation|games|host|cli)\.',
                r'(?<![\w.])import (?P<p>core|logic|engine|presentation|games|host|cli)\.'):
        def _imp(mm):
            if mm.group(0).startswith('world3d.'):
                return mm.group(0)
            stats['md'] += 1
            return mm.group(0).replace(f' {mm.group("p")}.', f' world3d.{mm.group("p")}.')
        out = re.sub(pat, _imp, out)
    return out


def fix_root_depth(stats, dry_run=False):
    """**第 4 步：包深了一层 ⇒ 包内所有"仓库根推导"都要多一次 `dirname`。**

    这是规划里点名的迁移坑（P0-3 / P2-1 各踩过一次）：`core/x.py` 找仓库根是
    `dirname(dirname(__file__))`，搬到 `world3d/core/x.py` 之后必须变成三次。
    少一层的症状是"路径指向 world3d/ 而不是仓库根" —— 找不到 `examples/`、`configs/`、
    或把**输出写到错的目录**（后者最阴：测试可能还是绿的）。

    `world3d/platform/app.py` **本来就在最终位置**（P2-1 直接落到那里），所以它是
    三次、**不能动** —— 迁移脚本必须把它排除，否则会被加成四次。
    """
    skip = os.path.join('world3d', 'platform')
    hits = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, 'world3d')):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        if skip.replace('/', os.sep) in dirpath:
            continue
        for fn in filenames:
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            text = open(path, encoding='utf-8').read()
            lines = text.split('\n')
            n = 0
            for i, line in enumerate(lines):
                if 'dirname(os.path.dirname(os.path.abspath(__file__)))' in line:
                    lines[i] = line.replace(
                        'dirname(os.path.dirname(os.path.abspath(__file__)))',
                        'dirname(os.path.dirname(os.path.dirname('
                        'os.path.abspath(__file__))))')
                    n += 1
                elif ('dirname(os.path.dirname(os.path.dirname('
                      'os.path.abspath(__file__))))') in line and 'brickward' in path:
                    lines[i] = line.replace(
                        'dirname(os.path.dirname(os.path.dirname('
                        'os.path.abspath(__file__))))',
                        'dirname(os.path.dirname(os.path.dirname(os.path.dirname('
                        'os.path.abspath(__file__)))))')
                    n += 1
            if n:
                hits.append((os.path.relpath(path, ROOT), n))
                if not dry_run:
                    open(path, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines))
    stats['depth'] = sum(n for _f, n in hits)
    return hits


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='P2-4 迁移：包收进 world3d.*')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    stats = {'py': 0, 'md': 0, 'prose': 0, 'files': 0}
    changed_files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in ('.git', '.venv', 'build', '__pycache__', 'vendor',
                                    'recordings', 'shots', 'node_modules')
                       and not d.endswith('.egg-info')]
        for fn in filenames:
            if not fn.endswith(('.py', '.md')):
                continue
            path = os.path.join(dirpath, fn)
            try:
                text = open(path, encoding='utf-8').read()
            except (OSError, UnicodeDecodeError):
                continue
            new = (rewrite_python(text, stats) if fn.endswith('.py')
                   else rewrite_markdown(text, stats))
            if new != text:
                changed_files.append(os.path.relpath(path, ROOT))
                if not args.dry_run:
                    open(path, 'w', encoding='utf-8', newline='\n').write(new)
    hits = fix_root_depth(stats, args.dry_run)
    print(f'{"[dry-run] " if args.dry_run else ""}改动文件 {len(changed_files)} 个：'
          f'py 行 {stats["py"]}、md 处 {stats["md"]}、散文 {stats["prose"]}'
          f'；根推导补层 {stats["depth"]} 处 / {len(hits)} 个文件')
    for f, n in hits:
        print(f'   depth: {f} ({n})')
    for f in changed_files[:60]:
        print('  ', f)
    if len(changed_files) > 60:
        print(f'   … 另有 {len(changed_files) - 60} 个')
    return 0


if __name__ == '__main__':
    sys.exit(main())

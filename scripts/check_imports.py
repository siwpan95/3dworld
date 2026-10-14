# -*- coding: utf-8 -*-
"""P2-4 迁移验证（1/3）：**逐个 import 全仓模块**，把"改漏/改坏"当场抓出来。

为什么要有这一步：迁移是机械改写（147 个文件、600 行），单测只覆盖到"被测试引用的那些模块"
—— 没被任何测试 import 的模块（例如某些只在 demo 里用的）改坏了**单测不会红**。
本脚本把 `world3d/` 下每个 `.py` 都 import 一遍，因此它是"全仓可达性"的门禁。

用法::

    python scripts/check_imports.py            # 全量
    python scripts/check_imports.py --list-bad # 只打印失败的
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SKIP_MODULES = {
    # 这些模块 import 时就会开窗口/连设备（演示入口），不适合"纯 import"体检
    'world3d.platform.app',
}
SKIP_PREFIX = ('world3d.games.',)     # 游戏 kernel 由下面单独按"可 import"处理
ALLOW_FAIL_PREFIX = (
    'main', 'kkrieger_demo',           # 顶层 demo 入口按需再测
)


def iter_modules():
    for dirpath, dirnames, filenames in os.walk(os.path.join(ROOT, 'world3d')):
        dirnames[:] = [d for d in dirnames if d != '__pycache__']
        for fn in sorted(filenames):
            if not fn.endswith('.py'):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, ROOT)[:-3].replace(os.sep, '.')
            if rel.endswith('.__init__'):
                rel = rel[:-len('.__init__')]
            yield rel, path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='逐个 import 全仓模块（P2-4 门禁 1/3）')
    ap.add_argument('--list-bad', action='store_true')
    args = ap.parse_args(argv)

    total, bad, skipped = 0, [], []
    for name, path in iter_modules():
        if name in SKIP_MODULES:
            skipped.append((name, 'import 时会开窗口/连设备'))
            continue
        total += 1
        try:
            importlib.import_module(name)
        except Exception as exc:                    # noqa: BLE001
            bad.append((name, f'{type(exc).__name__}: {exc}'))
            if not args.list_bad:
                print(f'  !! {name}\n     {type(exc).__name__}: {exc}')
                if os.environ.get('CHECK_IMPORTS_TRACE'):
                    traceback.print_exc()

    print('-' * 62)
    print(f'[check_imports] world3d/ 下 {total} 个模块，失败 {len(bad)}，'
          f'跳过 {len(skipped)}')
    for n, why in skipped:
        print(f'   skip: {n}（{why}）')
    if bad:
        print('CHECK_IMPORTS_FAILED')
        return 1
    print('== CHECK_IMPORTS_OK ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())

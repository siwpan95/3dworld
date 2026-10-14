# -*- coding: utf-8 -*-
"""P2-4 迁移验证（1/3）：**逐个 import 全仓模块**，把"改漏/改坏"当场抓出来。

为什么要有这一步：迁移是机械改写（147 个文件、600 行），单测只覆盖到"被测试引用的那些模块"
—— 没被任何测试 import 的模块（例如某些只在 demo 里用的）改坏了**单测不会红**。
本脚本把 `world3d/` 下每个 `.py` 都 import 一遍，因此它是"全仓可达性"的门禁。

## P5-a 追加：第二条不变量 —— **sys.path 不许被污染**

只"能 import"不够。P5-a 用脚手架实战检验时发现：8 个 `games/<名>/kernel.py` 的自举块
把 `world3d/`（而不是仓库根）推上了 `sys.path`，而 `world3d/platform/` 因此
**遮蔽标准库 `platform`** —— 实测

    import world3d.games.void.kernel
    import platform; platform.system()
    # AttributeError: module 'platform' has no attribute 'system'

pyglet 自己 `import platform`（`pyglet/app/__init__.py`、`pyglet/info.py`），
所以这是"跑起来才炸、且报错与原因完全无关"的那一类。

判据用**不变量**而不是"去数 `dirname` 的层数"：层数写法将来会换
（`Path(__file__).parents[3]`、`__package__` 推导…），而不变量不会 ——
**"仓库内自带 `__init__.py` 的目录不允许出现在 sys.path 上"**。
（`vendor/cordis/src` 是刻意加的白名单项：它没有 `__init__.py`，其子目录
`cordis/` 是 vendored 包，不与标准库冲突。）

⚠ 另外那条 `platform 已被遮蔽` 只是**症状探针，会漏报**：`platform` 通常在污染发生
**之前**就已被 import 进 `sys.modules`，此时再 `import platform` 拿到的仍是标准库那个
（实测注入破坏后它就没打印）。**权威判据只有 sys.path 那条不变量。**

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
ALLOW_FAIL_PREFIX = (
    'main', 'kkrieger_demo',           # 顶层 demo 入口按需再测
)

#: 与"仓库内包目录上 sys.path"无关的噪声目录（venv / 构建产物）
_PATH_NOISE = ('.venv', 'venv', 'build', 'dist', 'node_modules', '__pycache__')


def poisoned_sys_path():
    """返回到目前为止**被推上 sys.path 的仓库内部包目录**（相对仓库根）。

    判定：该目录在仓库内、且**自带 `__init__.py`** —— 那种目录一旦上 sys.path，
    它的**每个子目录**都会以顶层包名出现在 import 世界里（本项目实测：
    `world3d/platform/` 遮蔽标准库 `platform`）。
    """
    out = []
    for p in list(sys.path):
        if not p:
            continue
        ap = os.path.abspath(p)
        if ap == ROOT or not ap.startswith(ROOT + os.sep):
            continue
        rel = os.path.relpath(ap, ROOT).replace(os.sep, '/')
        if rel.split('/')[0] in _PATH_NOISE:
            continue
        if os.path.isfile(os.path.join(ap, '__init__.py')):
            out.append(rel)
    return out



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

    # ---- 第二条不变量：sys.path 不许被污染（P5-a 追加，见模块 docstring）----
    import platform as _platform

    poisoned = poisoned_sys_path()
    pfile = getattr(_platform, '__file__', '') or ''
    shadowed = bool(pfile) and os.path.abspath(pfile).startswith(ROOT + os.sep)
    if poisoned or shadowed:
        print('  !! sys.path 被污染：import 过程中把**仓库内的包目录**推上了搜索路径')
        for rel in poisoned:
            print(f'     sys.path += {rel}/   （自带 __init__.py ⇒ 其子目录成了顶层包名）')
        if shadowed:
            print(f'     标准库 platform 已被遮蔽：{pfile}')
        print('     修法：自举块算到**仓库根**（dirname 层数 = 该文件所在包层数 + 1），'
              '不要把包目录自身插进 sys.path。')

    print('-' * 62)
    print(f'[check_imports] world3d/ 下 {total} 个模块，失败 {len(bad)}，'
          f'跳过 {len(skipped)}')
    for n, why in skipped:
        print(f'   skip: {n}（{why}）')
    if bad or poisoned or shadowed:
        print('CHECK_IMPORTS_FAILED')
        return 1
    print('== CHECK_IMPORTS_OK ==')
    return 0


if __name__ == '__main__':
    sys.exit(main())

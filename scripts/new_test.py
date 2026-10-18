# -*- coding: utf-8 -*-
"""脚手架（P4）：生成新测试模块的**能跑**骨架。

2026-10-15 demo 剥离：原 `new_demo.py` 一分为二 —— demo 模板随 demo 迁往
独立仓 `siwpan/3dworld-demos`；本文件保留引擎仓的**测试模块**脚手架
（`new_kernel.py` 复用的登记器/写盘工具也在这里）。

## 为什么值得做

手写测试模块每个都要抄一遍同样的样板：`sys.path` 显式引导块、
`from world3d...` 导入、runner + `ALL_*_TESTS_PASSED` 标记。
**抄错一处就是半小时的排查**。

## 生成的骨架必须"开箱能跑"

模板不是伪代码：`python -m tests.<名>` 必须立刻通过。
门禁 `scaffold-templates-valid`（`scripts/verify.py`）会**渲染模板并 `ast.parse`**，
所以模板不会随着项目演进悄悄腐掉（例如导入路径改了）。

## 用法

    python scripts/new_test.py mathx              # 生成 tests/test_mathx.py
    python scripts/new_test.py mathx --register   # 顺便登记进 run_all_tests.py
    python scripts/new_test.py mathx --force      # 覆盖已存在的文件

生成之后**紧接着跑一次门禁**：它会告诉你还差哪几处（README 测试清单…）——
这正是"脚手架 + 门禁"配合的意义：**新增东西要改的地方由机器列出来，不靠记性。**
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import string
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

TEST_TEMPLATE = '''# -*- coding: utf-8 -*-
"""${title} 测试套件。

运行::

    python -m tests.test_${name}

约定（与其它测试模块一致，`repo-consistency-gates` 的门禁会核对）：

* 模块自带 runner，通过时打印 `ALL_${upper}_TESTS_PASSED`；
* **每条断言都要能被反例打穿** —— 写完自问"我把要防的那件事做出来，它会不会红"；
* 通过时也要**给数字**（"13 项边界一致"比"通过"有用得多），不要只说 ok；
* 需要 GL 的用例单独 try/except 记 SKIP，**跳过 ≠ 通过**；
* 注册进 `scripts/run_all_tests.py` 的 `MODULES`（`--register` 可自动做），
  并在 README 的测试清单里加一行（门禁 `readme-test-modules` 会核对双向一致）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.core.contracts import CORES  # noqa: E402  ← 换成被测模块


def test_smoke():
    """最少一条：被测模块能 import、关键符号存在。"""
    assert CORES, '契约注册表不该为空'
    print(f'  ok: 契约注册表 {len(CORES)} 条（把这里换成你真正要断言的东西）')


def test_boundary_that_can_be_broken():
    """**模板示范**：写一条"能被反例打穿"的判据。

    反例思路（挑一个真的会犯的错）：
      · 边界值两处不一致（一处改了另一处忘了）；
      · 单调性/守恒（拆成 N 段之和 == 整段）；
      · 确定性（同输入两次调用逐字相同）。
    """
    from world3d.core.contracts import status_table
    first = status_table()
    second = status_table()
    assert first == second, '同输入两次调用应当逐字相同（确定性）'
    assert first, '状态表不该为空'
    print(f'  ok: status_table 确定性（{len(first)} 行逐字一致）')


def main() -> int:
    tests = [
        ('test_smoke', test_smoke),
        ('test_boundary_that_can_be_broken', test_boundary_that_can_be_broken),
    ]
    failed = []
    for name, fn in tests:
        try:
            print(name)
            fn()
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {exc}')
        except Exception as exc:                            # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_${name}] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for n, exc in failed:
            print(f'  - {n}: {exc}')
        print('ALL_${upper}_TESTS_FAILED')
        return 1
    print('ALL_${upper}_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())
'''


def render_test(name):
    return string.Template(TEST_TEMPLATE).substitute(
        name=name, upper=name.upper(), title=name.replace('_', ' '))


def write_preserving_eol(path, text):
    """按**原文件的行尾**写回。

    为什么值得单独一个函数：本仓库 `core.autocrlf=true`（工作区是 CRLF、库里是 LF）。
    脚本若一律写 LF，虽然**归一化后内容完全相同**（`git hash-object` 与 HEAD 一致），
    但 `git status` 会把这些文件一直标成 `M` / `needs update` —— 于是"脚手架生成后
    撤销干净"这句话就变成了"内容干净但状态栏不干净"，人会开始忽略 status。
    新建文件走 LF（入库时由 git 归一化）。
    """
    try:
        with open(path, 'rb') as f:
            raw = f.read()
        crlf = raw.count(b'\r\n')
        eol = '\r\n' if crlf > raw.count(b'\n') - crlf else '\n'
    except OSError:
        eol = '\n'
    text = text.replace('\r\n', '\n')
    if eol == '\r\n':
        text = text.replace('\n', '\r\n')
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write(text)


def register_test(name, quiet=False):
    """把新测试模块登记进 `scripts/run_all_tests.py` 的 MODULES（幂等）。"""
    path = os.path.join(ROOT, 'scripts', 'run_all_tests.py')
    text = open(path, encoding='utf-8').read()
    mod = f'tests.test_{name}'
    if f"'{mod}'" in text:
        if not quiet:
            print(f'[new] {mod} 已在 MODULES 里，跳过登记')
        return False
    m = re.search(r'^MODULES = \[\n(.*?)^\]$', text, re.S | re.M)
    if not m:
        print('[new] 找不到 MODULES 列表 —— 请手工加一行：'
              f"\n      ('{mod}', 'ALL_{name.upper()}_TESTS_PASSED', '{name}'),")
        return False
    entry = (f"    ('{mod}', 'ALL_{name.upper()}_TESTS_PASSED', '{name}'),\n")
    new = text[:m.end(1)] + entry + text[m.end(1):]
    ast.parse(new)                      # 改坏了立刻抛，不写盘
    write_preserving_eol(path, new)
    if not quiet:
        print(f'[new] 已登记进 run_all_tests.py：{mod}')
    return True


def unregister_test(name, quiet=False):
    """把测试模块从 `run_all_tests.py` 的 MODULES 里摘掉（`new_kernel.py --undo` 用）。

    按**整行**摘除（不是正则替换片段）：`MODULES` 里的一行是自足的，
    摘行不会误伤相邻条目。返回摘掉的行数（0 = 本来就没有）。
    """
    path = os.path.join(ROOT, 'scripts', 'run_all_tests.py')
    text = open(path, encoding='utf-8').read()
    mod = f'tests.test_{name}'
    out, n = [], 0
    for ln in text.split('\n'):
        if f"'{mod}'" in ln and ln.strip().startswith('('):
            n += 1
            continue
        out.append(ln)
    if not n:
        if not quiet:
            print(f'[undo] {mod} 不在 MODULES 里，跳过')
        return 0
    new = '\n'.join(out)
    ast.parse(new)
    write_preserving_eol(path, new)
    if not quiet:
        print(f'[undo] 已从 run_all_tests.py 摘除：{mod}')
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description='脚手架：新测试模块')
    ap.add_argument('name', help='名字（小写字母/下划线；自动加 test_ 前缀）')
    ap.add_argument('--register', action='store_true',
                    help='顺便登记进 run_all_tests.py 的 MODULES')
    ap.add_argument('--force', action='store_true', help='覆盖已存在的文件')
    args = ap.parse_args(argv)

    name = args.name.strip()
    if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
        print(f'[new] 名字要小写字母开头、只含小写与下划线：{name!r}')
        return 2
    rel = f'tests/test_{name}.py'
    text = render_test(name)

    path = os.path.join(ROOT, rel)
    if os.path.exists(path) and not args.force:
        print(f'[new] {rel} 已存在 —— 要覆盖请加 --force')
        return 2
    ast.parse(text)                     # 模板自身先过一遍语法
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, 'w', encoding='utf-8', newline='\n').write(text)
    print(f'[new] 已生成 {rel}（{len(text.splitlines())} 行）')

    if args.register:
        register_test(name)

    print()
    print('[new] 接下来（**跑一次门禁，它会列出还差哪几处**）：')
    print('      python scripts/verify.py')
    print(f'      python -m tests.test_{name}          # 先确认骨架能跑')
    print(f"      → 若没自动登记：把 ('tests.test_{name}', "
          f"'ALL_{name.upper()}_TESTS_PASSED', '{name}') 加进 run_all_tests.MODULES")
    print(f'      → README「测试与验收」加一行（门禁 readme-test-modules 会核对）')
    return 0


if __name__ == '__main__':
    sys.exit(main())

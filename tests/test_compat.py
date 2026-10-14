# -*- coding: utf-8 -*-
"""旧导入路径兼容层测试 —— `world3d/compat.py`（P2-4 的过渡层）。

运行::

    python -m tests.test_compat

兼容层的价值判据只有两条，而且都能被打穿：

1. **旧路径在 install 之前必须真的不可用** —— 否则"兼容层"是个装饰，
   真实情况可能是"顶层包其实还在"（那说明 P2-4 没搬干净）；
2. **别名必须返回同一个模块对象**（不是副本、不是 re-export 一份）——
   这样才能保住模块级单例、`sys.modules` 里的状态与 `isinstance`。
   本条正是被一个真 bug 逼出来的：finder 挂在 `sys.meta_path` **末尾**时，
   `PathFinder` 会顺着父包 `__path__` 把子模块**加载第二遍**（顶层包别名成功、
   子模块却是两个对象）。

⚠ 本模块**必须自己控制 install 的时机**（先断言"不可用"，再 install），
所以 runner 把它放在各模块独立进程里跑是必要的。
"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from world3d.compat import (LEGACY_PREFIXES, install_legacy_aliases,  # noqa: E402
                            legacy_aliases_installed)


def test_legacy_paths_unavailable_before_install():
    """**判据 1**：install 之前，旧顶层路径必须不存在。

    这条同时反证"P2-4 真的把包搬走了"—— 如果 `import engine` 还能成，
    说明仓库顶层还留着 `engine/`（那兼容层就是多余的，迁移也没完成）。
    """
    assert not legacy_aliases_installed(), \
        '本测试要求"还没 install"的初始状态；若前置步骤装过，请调整测试顺序'
    bad = []
    for pkg in LEGACY_PREFIXES:
        try:
            importlib.import_module(pkg)
        except ModuleNotFoundError:
            continue
        bad.append(pkg)
    assert not bad, f'这些顶层包仍然存在（P2-4 没搬干净）：{bad}'
    print(f'  ok: {len(LEGACY_PREFIXES)} 个旧顶层路径在 install 之前都不可用'
          f'（反证仓库顶层已无这些包）')


def test_install_is_idempotent():
    first = install_legacy_aliases()
    second = install_legacy_aliases()
    assert first == second == LEGACY_PREFIXES, (first, second)
    assert legacy_aliases_installed() is True
    n = sum(1 for f in sys.meta_path if type(f).__name__ == '_LegacyFinder')
    assert n == 1, f'meta_path 里应只有一个别名 finder，实际 {n} 个（幂等被破坏）'
    print(f'  ok: 幂等（重复调用返回同一份前缀表，meta_path 里只有 1 个 finder）')


def test_alias_and_real_module_are_the_same_object():
    """**判据 2**：旧名与新名必须是**同一个模块对象**。

    ⚠ 这一条必须**覆盖子模块**：本会话实踩 —— finder 挂在 `sys.meta_path` 末尾时，
    顶层包 `host` 别名成功（`is` 成立），但 `host.tree` 会走 `PathFinder` +
    父包 `__path__` **再加载一遍**，变成两个模块对象（模块级单例各存一份）。
    只测顶层包的话这个 bug 完全看不出来。
    """
    for pkg in LEGACY_PREFIXES:
        old = importlib.import_module(pkg)
        new = importlib.import_module(f'world3d.{pkg}')
        assert old is new, f'{pkg} 不是同一个对象：{old} vs {new}'
    for deep in ('host.tree', 'engine.csm', 'core.geometry', 'logic.camera',
                 'games.brickward.kernel', 'presentation.world_renderer',
                 'cli.config_boot'):
        old = importlib.import_module(deep)
        new = importlib.import_module(f'world3d.{deep}')
        assert old is new, (f'{deep} 被加载了两遍（两个模块对象）—— '
                            f'别名 finder 是不是不在 sys.meta_path 最前面？')
        assert sys.modules.get(deep) is new, f'{deep} 没被登记进 sys.modules'
    print(f'  ok: {len(LEGACY_PREFIXES)} 个顶层 + 7 个（含 3 层深）子模块全部是**同一对象**')


def test_unknown_legacy_submodule_still_raises():
    """不存在的旧子模块**必须照常报错**，不能被别名 finder 静默吞成一个空模块。"""
    for name in ('core.nonexistent_thing', 'engine.no_such_module',
                 'games.nope.kernel'):
        try:
            importlib.import_module(name)
        except ModuleNotFoundError:
            continue
        raise AssertionError(f'{name} 竟然被别名解析出来了（finder 太宽松）')
    print('  ok: 3 个不存在的旧子模块都照常 ModuleNotFoundError（没被静默吞掉）')


def test_new_namespace_still_wins():
    """别名**不干扰**新命名空间：`world3d.*` 仍然照常工作、且是同一个对象。"""
    a = importlib.import_module('world3d.engine')
    b = importlib.import_module('engine')
    c = importlib.import_module('world3d.engine')
    assert a is b is c
    assert hasattr(a, '__path__'), 'world3d.engine 应仍是包'
    print('  ok: 新命名空间不受影响（三次 import 同一个对象，包属性完整）')


def test_install_does_not_shadow_stdlib_or_third_party():
    """finder 只认那 7 个前缀；标准库/三方模块不受影响。"""
    for name in ('os', 'sys', 'json', 'math', 'pyglet', 'yaml', 'cordis'):
        mod = importlib.import_module(name)
        assert mod is not None
        assert not str(getattr(mod, '__name__', '')).startswith('world3d.'), \
            f'{name} 被别名劫持成了 {getattr(mod, "__name__", None)}'
    print('  ok: os/sys/json/math/pyglet/yaml/cordis 都没被劫持')


# ------------------------------------------------------------------ runner --
def main() -> int:
    tests = [
        ('test_legacy_paths_unavailable_before_install',
         test_legacy_paths_unavailable_before_install),
        ('test_install_is_idempotent', test_install_is_idempotent),
        ('test_alias_and_real_module_are_the_same_object',
         test_alias_and_real_module_are_the_same_object),
        ('test_unknown_legacy_submodule_still_raises',
         test_unknown_legacy_submodule_still_raises),
        ('test_new_namespace_still_wins', test_new_namespace_still_wins),
        ('test_install_does_not_shadow_stdlib_or_third_party',
         test_install_does_not_shadow_stdlib_or_third_party),
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
    print(f'[test_compat] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_COMPAT_TESTS_FAILED')
        return 1
    print('ALL_COMPAT_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

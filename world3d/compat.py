# -*- coding: utf-8 -*-
"""旧导入路径的兼容层（P2-4 的"过渡一版"）。

## 背景

P2-4 把 `core/ logic/ engine/ presentation/ games/ host/ cli/` 全部收进了 `world3d.*`，
所以**旧路径 `import world3d.engine.x` 不再能用**。规划要求"旧路径留兼容再导出层一版"。

## 为什么不是"每个模块一个桩文件"

最直白的兼容层是给旧路径造一堆 `core/renderer.py` 里写 `from world3d.core.renderer import *`
—— 但那等于把**公共面复制成第二份真相源**：新增模块要记得补桩、删模块要记得删桩，
而且 `import world3d.core.x.y` 这种子模块路径还得逐个铺。**桩文件的数量会随包规模线性增长。**

## 这里的做法：一条元路径别名

`install_legacy_aliases()` 往 `sys.meta_path` 挂一个 finder，把 `core.x` 直接**解析成
同一个模块对象**（不是复制、不是 re-export）：

* `import world3d.core.renderer` 与 `import world3d.core.renderer` 拿到的是**同一个 module 实例**
  ⇒ `isinstance`、模块级单例、`sys.modules` 里的状态全部一致（桩文件做不到这点）；
* 零维护：新增/删除模块都不用改这里；
* **默认不开**（opt-in）：需要旧路径的下游调一次即可。
  这一版是过渡用的，等外部使用者迁完就删掉整个模块。

用法::

    from world3d.compat import install_legacy_aliases
    install_legacy_aliases()          # 之后 import world3d.engine.csm 仍然可用

    python -c "from world3d.compat import install_legacy_aliases as f; f(); import world3d.engine.csm"
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys

__all__ = ['LEGACY_PREFIXES', 'install_legacy_aliases', 'legacy_aliases_installed']

#: 迁移前位于仓库顶层的包（现在都在 `world3d.` 下）
LEGACY_PREFIXES = ('core', 'logic', 'engine', 'presentation', 'games', 'host', 'cli')

_INSTALLED = False


class _AliasLoader(importlib.abc.Loader):
    """把旧名字直接绑到**已经存在的模块对象**上（不做 exec）。"""

    def __init__(self, module):
        self._module = module

    def create_module(self, spec):
        return self._module                 # ← 同一个对象，不是副本

    def exec_module(self, module):
        """模块体已经在 `import_module` 里执行过了，这里什么都不做。"""
        return None


class _LegacyFinder(importlib.abc.MetaPathFinder):
    """把 `<旧前缀>.*` 解析到 `world3d.<旧前缀>.*`。"""

    def find_spec(self, fullname, path=None, target=None):
        head = fullname.split('.')[0]
        if head not in LEGACY_PREFIXES:
            return None
        try:
            module = importlib.import_module(f'world3d.{fullname}')
        except ImportError:
            return None                     # 新命名空间里也没有 ⇒ 让它按正常流程报错
        spec = importlib.util.spec_from_loader(fullname, _AliasLoader(module))
        if spec is not None:
            spec.submodule_search_locations = getattr(
                module, '__path__', None)   # 包要能继续往下找子模块
        return spec


def install_legacy_aliases() -> tuple:
    """挂上别名 finder（幂等）；返回被兼容的顶层前缀。

    ⚠ **必须挂在 `sys.meta_path` 最前面**（不是末尾）。我第一版挂在末尾，
    结果"顶层包"别名成功、**子模块却变成了第二个模块对象**：

        import world3d.host.tree                     # 走 PathFinder：父包 host.__path__
        assert host.tree is world3d.host.tree  # ✗ False —— 加载了两遍

    `PathFinder` 会顺着父包的 `__path__` 直接找到 `world3d/host/tree.py` 并**新建一个模块**，
    于是"同一模块两个实例"：模块级单例/缓存全部各存一份，`isinstance` 也可能不成立。
    挂在最前，别名 finder 先接住 `host.tree`，才真的复用同一个对象。

    代价是它优先于常规查找 —— 可以接受：本项目**已经没有**这些顶层包，
    而 finder 只在 `world3d.<同名>` 真的能 import 时才返回 spec。
    """
    global _INSTALLED
    if not _INSTALLED:
        sys.meta_path.insert(0, _LegacyFinder())
        _INSTALLED = True
    return LEGACY_PREFIXES


def legacy_aliases_installed() -> bool:
    return _INSTALLED

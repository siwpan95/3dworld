# -*- coding: utf-8 -*-
"""vendored cordis 的统一定位入口 —— clean clone 不再需要手工接线。

背景（上线前全检 P0）
--------------------
仓库内共有 10 处需要 ``import cordis``，此前每处各自复制一段

    try:
        import cordis
    except ImportError:
        _src = os.environ.get('CORDIS_SRC')
        if _src and os.path.isdir(_src):
            sys.path.insert(0, _src)
        import cordis

的兜底，带来两个真实缺陷：

1. **兜底不认仓库自带源码**：只查环境变量 ``CORDIS_SRC``，不查仓库内的
   ``vendor/cordis/src``。于是全新克隆 ``pip install -r requirements.txt`` 之后，
   7 个发行 demo 与 ``core/`` 全部共同核仍然 import 失败。
2. **报错吞掉真实根因**：``ModuleNotFoundError`` 是 ``ImportError`` 的子类，
   所以「cordis 找到了，但它自身依赖 PyYAML / watchdog 缺失」也会被这段兜底捕获，
   并统一改写成「未找到 cordis 包，请设置 CORDIS_SRC」。用户于是去设 ``CORDIS_SRC``
   —— 而 cordis 其实早就找到了。这是一个**明确但永远无效**的修复指令。

本模块把定位与报错收口到一处。各站点改为::

    import _cordis_locate
    _cordis_locate.ensure()          # 失败时抛出带真实根因的 ImportError

安全
----
``CORDIS_SRC`` 仍可用来显式覆盖，但**必须先通过 ``os.path.isdir`` 校验**才允许进入
``sys.path``（全检 F-004：环境变量目录注入）。仓库内 ``vendor/cordis/src`` 由
``__file__`` 绝对推导，不接受外部输入。
"""
from __future__ import annotations

import importlib
import importlib.util
import os
import sys

__all__ = ['ensure', 'locate', 'repo_root', 'vendor_dir']

_VENDOR_PARTS = ('vendor', 'cordis', 'src')


def repo_root() -> str:
    """3dworld 仓库根（本文件位于仓库根，故取其所在目录）。"""
    return os.path.dirname(os.path.abspath(__file__))


def vendor_dir() -> str:
    """仓库内 vendored cordis 的源码目录（``vendor/cordis/src``）。"""
    return os.path.join(repo_root(), *_VENDOR_PARTS)


def _find_spec():
    """``importlib.util.find_spec('cordis')`` 的安全包装（不真正导入）。"""
    try:
        return importlib.util.find_spec('cordis')
    except (ImportError, ValueError, AttributeError):
        return None


def locate() -> str | None:
    """返回可用的 cordis 源码目录；都不存在时返回 ``None``。

    优先级：① ``CORDIS_SRC``（显式覆盖，需通过 ``isdir`` 校验）；
    ② 仓库内 ``vendor/cordis/src``。
    """
    env = os.environ.get('CORDIS_SRC')
    if env and os.path.isdir(env) and os.path.isdir(os.path.join(env, 'cordis')):
        return env
    v = vendor_dir()
    if os.path.isdir(v) and os.path.isdir(os.path.join(v, 'cordis')):
        return v
    return None


def _import_or_raise(where: str, strict: bool) -> bool:
    # 注意同时捕获 TypeError：缺 watchdog 时 cordis 的 hmr.py 会把 None 当基类用，
    # 抛的是 ``TypeError: NoneType takes no arguments`` 而**不是** ImportError ——
    # 只捕获 ImportError 的话这条路径会漏掉（正是"缺 watchdog"这条最难查的路径）。
    try:
        importlib.import_module('cordis')
        return True
    except (ImportError, TypeError) as exc:
        if not strict:
            return False
        raise ImportError(
            f'[cordis] cordis 已定位到 {where}，但导入失败 —— 几乎总是它自身的依赖缺失'
            f'（PyYAML / watchdog）：{exc.__class__.__name__}: {exc}。'
            f'请执行 pip install -r requirements.txt。'
            f'提示：缺 watchdog 的典型症状是 "TypeError: NoneType takes no arguments"'
            f'（cordis/hmr.py 把未安装 watchdog 时的 None 当基类使用）。'
        ) from exc


def ensure(strict: bool = True) -> bool:
    """确保 ``cordis`` 可被 import；必要时把 vendored 源码目录加入 ``sys.path``。

    :param strict: 为 ``True`` 时失败直接抛 ImportError（**报错文案包含真实根因**）；
        为 ``False`` 时静默返回 ``False``。
    """
    # ① 已被安装或被 .pth 暴露
    if _find_spec() is not None:
        return _import_or_raise('已安装 / 已被 .pth 暴露', strict)

    # ② CORDIS_SRC 或仓库内 vendor/cordis/src
    d = locate()
    if d is not None:
        if d not in sys.path:
            sys.path.insert(0, d)
        if _find_spec() is not None:
            return _import_or_raise(d, strict)

    if not strict:
        return False
    raise ImportError(
        f'[cordis] 未找到 cordis 包。已尝试 CORDIS_SRC={os.environ.get("CORDIS_SRC")!r} '
        f'与仓库内 {vendor_dir()}。安装方式见 docs/cordis集成.md。'
    )

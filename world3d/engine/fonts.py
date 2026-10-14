# -*- coding: utf-8 -*-
"""CJK 字体解析：本地/环境变量/平台系统字体 → pyglet 可用的 font_name。

背景与边界（2026-10-09 实测）：
- **Windows 上无需修复**——pyglet 自动字体解析即可良好渲染中文（开发环境与
  打包 exe 均实测正常；打包验证见 `docs/分发指南.md`）；
- 本模块解决的是**跨平台兜底**（Linux/macOS 缺 CJK 字体时的豆腐块）与
  **显式控制**（指定/覆盖字体族名）。

策略借鉴 siwpan/an-engine 的 ``allin_engine/utils/font.py``（MIT，siwpan 自研；
此处为 **pyglet 适配版**——用字体族名经系统 API 查询，无需解析字体文件）。
解析顺序：
  1. 环境变量 ``WORLD3D_FONT_NAME``（字体族名，最高优先级）
  2. 平台系统字体候选（族名，非路径：pyglet 走系统字体 API）

用法::

    from world3d.engine.fonts import resolve_cjk_font
    name = resolve_cjk_font()              # 如 'Microsoft YaHei'；无则 None
    label = pyglet.text.Label('中文', font_name=name)   # None 时 pyglet 用默认
"""
import os
import sys

__all__ = ['resolve_cjk_font', 'CANDIDATES']

# 平台候选（字体族名；pyglet.font.have_font 逐一检测）
_WIN = ['Microsoft YaHei', '微软雅黑', 'SimHei', '黑体', 'SimSun', '宋体']
_MAC = ['PingFang SC', 'STHeiti', 'Hiragino Sans GB', 'Arial Unicode MS']
_LINUX = ['WenQuanYi Micro Hei', 'Noto Sans CJK SC', 'Droid Sans Fallback',
          'AR PL UMing CN']

CANDIDATES = (_WIN if sys.platform == 'win32'
              else _MAC if sys.platform == 'darwin' else _LINUX)

_cache = {'done': False, 'name': None}


def resolve_cjk_font():
    """返回当前环境可用的 CJK 字体族名（带缓存）；找不到返回 None（用默认）。"""
    if _cache['done']:
        return _cache['name']
    names = []
    env = os.environ.get('WORLD3D_FONT_NAME')
    if env:
        names.append(env)
    names.extend(CANDIDATES)
    name = None
    try:
        import pyglet
        for cand in names:
            try:
                if pyglet.font.have_font(cand):
                    name = cand
                    break
            except Exception:
                continue
    except Exception:
        name = None
    _cache.update(done=True, name=name)
    return name

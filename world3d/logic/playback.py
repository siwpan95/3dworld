# -*- coding: utf-8 -*-
"""播放控制器 —— 兼容再导出层。

实现已下沉到 :mod:`world3d.engine.timeline`（核心引擎抽取，2026-10），
API 完全一致，直接转发。
"""
from world3d.engine.timeline import PlaybackController       # noqa: F401

__all__ = ['PlaybackController']

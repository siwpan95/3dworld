# -*- coding: utf-8 -*-
"""渲染共同核 ``render``（实现 ``core.contracts.RenderCore``）。

自 ``experiments/cordis/tower_defense_kernel`` 上提的**跨内核共享实现**（P0-1 去重）：
只提供 GL 资源的**原语**（上传 / 编译 / 释放）与绘制入口；不持有任何游戏状态。
headless 下不真正开 GL 上下文，但资源记账与回滚语义完整执行。

> 依赖：仅 ``cordis``（vendored，第三方）——**不依赖 engine/logic，也不依赖 experiments**。
"""
from __future__ import annotations

from core._cordis import Service


class RenderService(Service):
    """渲染共同核（实现 RenderCore 契约）。

    只提供 GL 资源的**原语**（上传 / 编译 / 释放）与绘制入口；不持有任何游戏状态。
    headless 下不真正开 GL 上下文，但资源记账与回滚语义完整执行。
    """

    def __init__(self, ctx, config):
        super().__init__(ctx, "render")
        self._window = None
        self._live: set = set()        # 当前存活的 GL 资源句柄 id
        self._acquired = 0
        self._released = 0
        self._programs: dict = {}
        self._meshes: dict = {}

    # -- RenderCore 契约 --
    def program(self, name: str):
        return self._programs.get(name)

    def render(self, camera=None, scene=None, aspect=None, window=None):
        # headless 下为空操作；真实实现会把 scene 绘到当前帧缓冲
        return None

    def resize(self, width: int, height: int):
        pass

    def set_point_lights(self, lights):
        pass

    @property
    def window(self):
        return self._window

    # -- GL 资源原语（共同核提供，内核负责生命周期）--
    def compile_program(self, name: str):
        """编译一个着色器程序（headless：仅记账 + 分配 id）。"""
        pid = self._next_id()
        self._programs[name] = pid
        self._live.add(pid)
        self._acquired += 1
        if self._window is not None:
            # 真实路径：pid = glCreateProgram(); glLinkProgram(pid)
            pass
        return pid

    def upload_mesh(self, name: str, pos, shade):
        """上传一份网格（headless：仅记账 + 分配 id）。"""
        mid = self._next_id()
        self._meshes[name] = mid
        self._live.add(mid)
        self._acquired += 1
        if self._window is not None:
            # 真实路径：buf = glGenBuffers(1); glBufferData(...)
            pass
        return mid

    def release(self, handle) -> None:
        """释放一个 GL 资源（headless：记账；真实路径走 glDelete*）。"""
        if handle in self._live:
            self._live.discard(handle)
            self._released += 1
            if self._window is not None:
                # 真实路径：glDeleteBuffers(1, [handle])
                pass

    def live_count(self) -> int:
        return len(self._live)

    def stats(self) -> dict:
        return dict(acquired=self._acquired, released=self._released, live=self.live_count())

    def _next_id(self) -> int:
        return self._acquired + self._released + 1

    def __cordis_init__(self):
        return None


__all__ = ['RenderService']

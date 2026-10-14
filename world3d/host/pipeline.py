# -*- coding: utf-8 -*-
"""场景校验流水线（Cordis 化 · 原「试点一」，P0-5 转正）。

用 Cordis v4.0.0（geohotstan/cordis-py，忠实移植 cordiverse/cordis v4）的
**fiber 属性式 API** 组织 3dworld 的场景管线：

    SceneCheckService  -> 提供 ``scene_check`` 服务（包装 engine.scene_build.validate）
    LoaderService      -> inject scene_check；装载场景并注册 ``scene`` 服务（可逆）
    ReporterService    -> inject scene；输出统计（依赖消失时自动停用）

v4.0.0 相对旧式 functional 写法的重要变化：
  * 插件用 ``Service`` 子类 + 声明式 ``inject = [...]``（属性式依赖声明），
    不再手动给函数挂 ``plugin.inject = [...]`` 属性。
  * ``ctx.provide(name, value)`` 返回**可回滚的 FiberEffect**——服务在所属
    fiber dispose 时自动撤回，无需再套一层 ``ctx.effect`` 去手动撤回。
  * ``ctx.effect(execute)`` 接受「返回 disposer 的 callable」或「yield disposer
    的 generator」，所有副作用都绑定到 fiber，dispose 时统一清理（可逆副作用）。

演示点：
  1. inject 依赖自动排序（无需手工编排启动顺序）
  2. 卸载即回滚（loader 卸载 -> scene 服务撤回）
  3. coeffect 级联（依赖消失 -> reporter 自动停用）

依赖：cordis 的 Python 移植（见 `docs/cordis集成.md`）。
运行::

    python -m host.pipeline
    python -m host.pipeline --scene docs/llm_scene_demo.json
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# ---- cordis 定位：优先已安装；其次 CORDIS_SRC 指向的源码目录 ----
import world3d._cordis_locate as _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Context, Service                 # noqa: E402
from world3d.engine.scene_build import load_json, validate  # noqa: E402


def log(msg: str) -> None:
    print(msg)


class SceneCheckService(Service):
    """校验服务：把 engine.scene_build 的 JSON 校验器包装成 ``scene_check`` 服务。"""

    def __init__(self, ctx, config):
        super().__init__(ctx, "scene_check")

    def check(self, path: str):
        data = load_json(path)
        issues = validate(data)
        errors = [i for i in issues if i[0] == 'error']
        stats = next(i[2] for i in issues if i[0] == 'stats')
        return errors, stats

    def __cordis_init__(self):
        log('[checker] service ready: scene_check')
        return None


class LoaderService(Service):
    """装载插件：inject scene_check；把已装载场景注册为 ``scene`` 服务（可逆）。"""

    inject = ['scene_check']

    def __init__(self, ctx, config):
        super().__init__(ctx, "scene")
        errors, stats = ctx.scene_check.check(config['scene'])
        if errors:
            raise RuntimeError(f'{len(errors)} scene error(s): {errors[:2]}')
        self.path = config['scene']
        self.stats = stats

    def __cordis_init__(self):
        log(f"[loader] loaded {os.path.basename(self.path)}: "
            f"nodes={self.stats['nodes']} verts={self.stats['vertices']}")

        # 可逆副作用：loader fiber 卸载时自动触发——证明服务撤回与
        # 副作用回滚是绑定的（v4.0.0 的 effect 语义）。
        ctx = self.ctx

        def unload():
            log('[loader] unloaded: scene service withdrawn (effect reverted)')

        ctx.effect(lambda: unload)
        return None


class ReporterService(Service):
    """报告插件：inject scene；依赖消失时自动停用（coeffect 级联）。"""

    inject = ['scene']

    def __init__(self, ctx, config):
        super().__init__(ctx, "reporter")

    def __cordis_init__(self):
        scene = self.ctx.scene
        st = scene.stats
        log(f"[reporter] {os.path.basename(scene.path)}: "
            f"meshes={st['meshes']} tris={st['triangles']}")

        ctx = self.ctx

        def deactivated():
            log('[reporter] deactivated: scene withdrawn (coeffect reacted)')

        ctx.effect(lambda: deactivated)
        return None


async def run_pipeline(scene_path: str) -> int:
    ctx = Context()

    log('== mount plugin tree (order by dependency graph) ==')
    checker_fiber = await ctx.plugin(SceneCheckService)
    loader_fiber = await ctx.plugin(LoaderService, {'scene': scene_path})
    reporter_fiber = await ctx.plugin(ReporterService)

    # 等待依赖图解析完成（checker 无依赖，应立即激活；loader/reporter 待其上游）
    await checker_fiber
    await loader_fiber
    await reporter_fiber

    log('== dispose loader (service rollback + downstream deactivation) ==')
    await loader_fiber.dispose()
    await asyncio.sleep(0.1)
    log(f"[verify] scene service accessible: {ctx.get('scene') is not None}")
    log('== PIPELINE_OK ==')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description='scene pipeline on cordis (pilot 1, v4.0.0)')
    p.add_argument('--scene',
                   default=os.path.join(ROOT, 'docs', 'llm_scene_demo.json'))
    args = p.parse_args(argv)
    if not os.path.exists(args.scene):
        print(f'[error] scene not found: {args.scene}')
        return 2
    return asyncio.run(run_pipeline(os.path.abspath(args.scene)))


if __name__ == '__main__':
    sys.exit(main())

# -*- coding: utf-8 -*-
"""场景管线的 Cordis 化试点（试点一）。

用 Cordis（「时空可组合性」元框架，DeepSeek Harness 的运行时内核；此处为
其 Python 移植）的「服务 + inject + 可逆副作用」组织 3dworld 的场景管线：

    checker  插件 → 提供 scene_check 服务（包装 engine.scene_build.validate）
    loader   插件 → inject scene_check；装载场景 JSON 并注册 scene 服务（可逆）
    reporter 插件 → inject scene；输出统计（依赖消失时自动停用）

演示点：
  1. inject 依赖自动排序（无需手工编排启动顺序）
  2. 卸载即回滚（loader 卸载 → scene 服务撤回）
  3. coeffect 级联（依赖消失 → reporter 自动停用）

依赖：cordis 的 Python 移植（安装见 experiments/cordis/README.md）。
运行::

    python experiments/cordis/scene_pipeline.py
    python experiments/cordis/scene_pipeline.py --scene docs/llm_scene_demo.json
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

# ---- cordis 定位：优先已安装；其次 CORDIS_SRC 指向的源码目录 ----
try:
    import cordis  # noqa: F401
except ImportError:
    _src = os.environ.get('CORDIS_SRC')
    if _src and os.path.isdir(_src):
        sys.path.insert(0, _src)
    try:
        import cordis  # noqa: F401
    except ImportError:
        sys.exit('[cordis] 未找到 cordis 包。安装方式见 '
                 'experiments/cordis/README.md（或设置 CORDIS_SRC）')

from cordis import Context                          # noqa: E402
from engine.scene_build import load_json, validate  # noqa: E402


def make_plugins(log):
    """构建三个插件（闭包持有 log 便于演示输出）。"""

    async def checker_plugin(ctx, config):
        """校验服务：包装 engine.scene_build 的 JSON 校验器。"""

        def check(path):
            data = load_json(path)
            issues = validate(data)
            errors = [i for i in issues if i[0] == 'error']
            stats = next(i[2] for i in issues if i[0] == 'stats')
            return errors, stats

        ctx.provide('scene_check', check)
        log('[checker] service ready: scene_check')

    async def loader_plugin(ctx, config):
        """装载插件：inject scene_check；把已装载场景注册为服务（可逆）。"""
        check = ctx.get('scene_check')
        errors, stats = check(config['scene'])
        if errors:
            raise RuntimeError(f'{len(errors)} scene error(s): {errors[:2]}')

        def unload():
            log('[loader] unloaded: scene service withdrawn (effect reverted)')

        ctx.effect(lambda: unload)
        ctx.provide('scene', {'path': config['scene'], 'stats': stats})
        log(f"[loader] loaded {os.path.basename(config['scene'])}: "
            f"nodes={stats['nodes']} verts={stats['vertices']}")

    loader_plugin.inject = ['scene_check']

    async def reporter_plugin(ctx, config):
        """报告插件：inject scene；依赖消失时自动停用（coeffect）。"""
        scene = ctx.get('scene')
        st = scene['stats']
        log(f"[reporter] {os.path.basename(scene['path'])}: "
            f"meshes={st['meshes']} tris={st['triangles']}")

        def deactivated():
            log('[reporter] deactivated: scene withdrawn (coeffect reacted)')

        ctx.effect(lambda: deactivated)

    reporter_plugin.inject = ['scene']

    return checker_plugin, loader_plugin, reporter_plugin


async def run_pipeline(scene_path):
    ctx = Context()

    def log(msg):
        print(msg)

    checker, loader, reporter = make_plugins(log)

    log('== mount plugin tree (order by dependency graph) ==')
    await ctx.plugin(checker)
    loader_fiber = await ctx.plugin(loader, {'scene': scene_path})
    await ctx.plugin(reporter)

    log('== dispose loader (service rollback + downstream deactivation) ==')
    await loader_fiber.dispose()
    await asyncio.sleep(0.1)
    log(f"[verify] scene service accessible: {ctx.get('scene') is not None}")
    log('== PIPELINE_OK ==')
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description='scene pipeline on cordis (pilot 1)')
    p.add_argument('--scene',
                   default=os.path.join(ROOT, 'docs', 'llm_scene_demo.json'))
    args = p.parse_args(argv)
    if not os.path.exists(args.scene):
        print(f'[error] scene not found: {args.scene}')
        return 2
    return asyncio.run(run_pipeline(os.path.abspath(args.scene)))


if __name__ == '__main__':
    sys.exit(main())

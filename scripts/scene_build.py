# -*- coding: utf-8 -*-
"""场景 JSON 校验/试构建 CLI（实现见 ``engine/scene_build.py``）。

用法::

    python scripts/scene_build.py docs/llm_scene_demo.json --validate   # 只校验
    python scripts/scene_build.py docs/llm_scene_demo.json --dry-run    # 校验+CPU 构建

交互式渲染请用 ``examples/scene_viewer.py --file <json>``（热重载查看器）。
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scene_build import build_meshdata, load_json, summarize  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(description='scene JSON validate / dry-run')
    p.add_argument('json_path')
    p.add_argument('--validate', action='store_true', help='only validate')
    p.add_argument('--dry-run', action='store_true',
                   help='validate + CPU-build all meshes + stats')
    args = p.parse_args(argv)

    if not os.path.exists(args.json_path):
        print(f'[error] file not found: {args.json_path}')
        return 2
    data = load_json(args.json_path)

    n_err = summarize(data)
    if args.dry_run and n_err == 0:
        def count(nd):
            k = 0
            if isinstance(nd.get('mesh'), dict):
                k += build_meshdata(nd['mesh']).vertex_count
            return k + sum(count(c) for c in nd.get('children', ()))

        total = count(data.get('root', {}))
        print(f'[dry-run] CPU build OK, total vertices = {total}')
    elif n_err == 0:
        print('[hint] render it with: python examples/scene_viewer.py --file '
              + args.json_path)
    return 0 if n_err == 0 else 1


if __name__ == '__main__':
    sys.exit(main())

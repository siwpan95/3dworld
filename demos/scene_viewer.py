# -*- coding: utf-8 -*-
"""场景查看器：加载场景 JSON 并实时渲染，改文件即热重载。

这是「LLM 场景生成工作流」的『看』这一端::

    1. 生成  LLM/人写场景 JSON（prompt 模板见 docs/LLM场景生成工作流.md）
    2. 校验  python scripts/scene_build.py <json> --validate
    3. 查看  python demos/scene_viewer.py --file <json>
    4. 迭代  编辑 JSON 保存 → 本查看器自动重载（含错误的 JSON 会被拒绝、保留旧场景）

用法::

    python demos/scene_viewer.py --file docs/llm_scene_demo.json
    python demos/scene_viewer.py --file xxx.json --smoke 3
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pyglet  # noqa: E402

from world3d.platform.app import (DemoApp, PROJECT_ROOT, orbit_camera_input,  # noqa: E402
                            run)

DEFAULT_JSON = os.path.join(PROJECT_ROOT, 'docs', 'llm_scene_demo.json')

# 允许出现在 JSON environment 字段里的 LightEnvironment 构造参数
_ENV_KEYS = ('sun_dir', 'sun_color', 'sun_intensity', 'sky_zenith',
             'sky_horizon', 'sky_ground', 'ibl_intensity', 'sky_intensity',
             'env_size', 'prefilter_levels')


def _extract_file(argv):
    """从 argv 中摘出 --file（world3d.platform.app.parse_args 不认识它）。"""
    args = list(argv)
    path = None
    if '--file' in args:
        i = args.index('--file')
        if i + 1 < len(args):
            path = args[i + 1]
            del args[i:i + 2]
        else:
            del args[i]
    else:
        for i, a in enumerate(args):
            if a.startswith('--file='):
                path = a.split('=', 1)[1]
                del args[i]
                break
    return path, args


class SceneViewerApp(DemoApp):
    TITLE = 'scene viewer · scene JSON (hot reload)'
    HELP = 'ESC quit   SPACE pause   R reload JSON   drag/wheel orbit'
    HUD = True
    FOV = 55.0
    Z_FAR = 600.0
    JSON_PATH = None                    # main() 里设置

    def __init__(self, width, height, args=None):
        self._json_path = os.path.abspath(self.JSON_PATH or DEFAULT_JSON)
        super().__init__(width, height, args=args)

    # ------------------------------------------------------------- setup --
    def setup(self):
        from engine.camera import OrbitCamera
        from engine.env import LightEnvironment
        from engine.renderer import Renderer
        from engine.scene_build import build_scene, load_json
        from engine.serialize import SceneWatcher
        from engine.shaders import get

        try:
            self._data = load_json(self._json_path)
        except Exception as e:
            print(f'[viewer] cannot load {self._json_path}: {e}')
            self._data = {'name': 'empty', 'root': {'name': 'root'}}

        env_def = self._data.get('environment') or {}
        self.env = LightEnvironment(
            **{k: v for k, v in env_def.items() if k in _ENV_KEYS}).bake()

        self.renderer = Renderer(self.window.width, self.window.height,
                                 environment=self.env)
        self.scene = build_scene(self._data, get('pbr'))
        self.camera = OrbitCamera(target=(0.0, 3.0, 0.0), yaw=35.0,
                                  pitch=20.0, dist=24.0,
                                  dist_range=(4.0, 160.0),
                                  fov=self.FOV, far=self.Z_FAR)
        self.on_mouse_drag, self.on_mouse_scroll = orbit_camera_input(
            self, self.camera)
        self.watcher = SceneWatcher(self._json_path,
                                    on_reload=lambda _scene: self._reload())
        self.reload_msg = 'loaded'
        print(f'[viewer] loaded {self._json_path} '
              f'({len(self.scene.drawables())} drawables)')

    # ------------------------------------------------------------ reload --
    def _reload(self):
        """热重载：校验通过才换场景（错误的 JSON 保留旧画面）。"""
        from engine.scene_build import build_scene, load_json, validate
        from engine.shaders import get

        try:
            data = load_json(self._json_path)
        except Exception as e:
            self.reload_msg = 'JSON parse error'
            print(f'[viewer] reload failed (parse): {e}')
            return
        errors = [i for i in validate(data) if i[0] == 'error']
        if errors:
            self.reload_msg = f'rejected ({len(errors)} errors)'
            print(f'[viewer] reload rejected: {len(errors)} error(s)')
            for _lvl, path, msg in errors[:6]:
                print(f'    {path}: {msg}')
            return
        self._swap_scene(build_scene(data, get('pbr')))
        self._data = data
        self.reload_msg = 'reloaded OK'
        print(f'[viewer] reloaded OK ({len(self.scene.drawables())} drawables)')

    def _swap_scene(self, new_scene):
        old = getattr(self, 'scene', None)
        if old is not None:
            for d in old.drawables():
                v = getattr(d, 'vlist', None)
                if v is not None:
                    try:
                        v.delete()
                    except Exception:
                        pass
        self.scene = new_scene

    # ------------------------------------------------------- 主循环钩子 --
    def update(self, dt):
        self.watcher.poll()
        self.scene.update(dt)

    def render(self):
        w, h = self.window.width, self.window.height
        self.renderer.render(self.camera, self.scene, w / float(h), self.window)

    def on_key(self, symbol, modifiers):
        if symbol == pyglet.window.key.R:
            self._reload()

    def _on_resize(self, width, height):
        result = super()._on_resize(width, height)
        if hasattr(self, 'renderer'):
            self.renderer.resize(width, height)
        return result

    def status_text(self):
        base = super().status_text()
        draws = len(self.scene.drawables()) if hasattr(self, 'scene') else 0
        return (f'{base}   {os.path.basename(self._json_path)}   '
                f'draws={draws}   [{self.reload_msg}]')


def main():
    path, argv = _extract_file(sys.argv[1:])
    if path:
        SceneViewerApp.JSON_PATH = path
    run(SceneViewerApp, argv)


if __name__ == '__main__':
    main()

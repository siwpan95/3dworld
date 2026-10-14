# -*- coding: utf-8 -*-
"""场景序列化与热重载。

JSON 里存的是**结构与参数**（节点层级、TRS、材质参数、光照环境），
不存几何顶点——顶点数据动辄 MB 级，进 JSON 既慢又没意义；
几何按名字从程序化生成器 / glTF 资源里重建。

热重载：``SceneWatcher.poll()`` 每帧（或定时）检查 mtime，变了就回调。
调场景参数时不用重启程序，改 JSON 保存即可——调光照/材质时特别省时间。
"""
import json
import os
import time

__all__ = ['save_scene', 'load_scene', 'SceneWatcher', 'dumps', 'loads']


def dumps(scene, indent=2):
    """场景 -> JSON 字符串。"""
    return json.dumps(scene.to_dict(), ensure_ascii=False, indent=indent)


def loads(text):
    """JSON 字符串 -> Scene（只有结构，几何需调用方重建）。"""
    from world3d.engine.scene import Scene
    return Scene.from_dict(json.loads(text))


def save_scene(scene, path):
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(dumps(scene))
    return path


def load_scene(path):
    with open(path, 'r', encoding='utf-8') as f:
        return loads(f.read())


class SceneWatcher:
    """按 mtime 监听场景文件，变化时回调（热重载）。"""

    def __init__(self, path, on_reload=None, min_interval=0.5):
        self.path = os.path.abspath(path)
        self.on_reload = on_reload
        self.min_interval = float(min_interval)
        self._mtime = self._current_mtime()
        self._last_check = 0.0
        self.reloads = 0

    def _current_mtime(self):
        try:
            return os.path.getmtime(self.path)
        except OSError:
            return None

    def poll(self, force=False):
        """检查一次；有变化则回调。返回是否触发了重载。"""
        now = time.time()
        if not force and now - self._last_check < self.min_interval:
            return False
        self._last_check = now
        m = self._current_mtime()
        if m is None or m == self._mtime:
            return False
        self._mtime = m
        self.reloads += 1
        if self.on_reload is not None:
            try:
                scene = load_scene(self.path)
            except Exception as e:                       # 解析失败不打断主循环
                print(f'[hotreload] failed: {e}')
                return False
            self.on_reload(scene)
            return True
        return True

    def __repr__(self):
        return f'SceneWatcher({os.path.basename(self.path)}, reloads={self.reloads})'

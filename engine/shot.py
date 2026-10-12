# -*- coding: utf-8 -*-
"""演出模型 ``Shot``（可视化导演台的底座）。

**设计**（design note `docs/3_可视化导演台_design_note.md`）：

* 一条 **channel = (node, prop, sampler)** —— 与 `engine.timeline` 的
  `Sampler`/`Channel`/`Timeline` **零转换**映射（单一事实来源）；
* **任意节点、任意可插值属性**都支持（相机只是 `node == CAMERA_NODE` 的一类通道）：
  - ``position`` / ``target`` / ``translation`` / ``scale`` / ``tint`` / ``color`` → ``vec3``
  - ``fov`` / ``intensity`` / ``glow`` / 任意标量 → ``float``
  - ``rotation`` → ``quat``（wxyz，与 `Node.rotation` 一致）
* 关键帧只存**裸值**（float / (x,y,z) / (w,x,y,z)），``CUBICSPLINE`` 的 in/out
  切线由 `engine.timeline.make_sampler` **自动生成**（Catmull-Rom 有限差分）——
  手写演出 JSON 时不需要算切线；两关键帧时自动退化为**精确线性**（不会过冲）；
* 导出/导入 **演出 JSON**（`shot` 格式），`scene` 字段只存**路径引用**（复用
  `engine/serialize.py` 的场景格式，不内嵌）。

典型用法::

    s = Shot(name='dolly', duration=12.0)
    s.add_channel(CAMERA_NODE, 'position', [0, 6, 12], [(0,2,10), (0,2,0), (0,1.6,-8)])
    s.add_channel(CAMERA_NODE, 'target',   [0, 12],     [(0,1.5,0), (0,1.2,-6)])
    s.save('shots/dolly.shot.json')

    p = ShotPlayer(s, scene=scene, camera=cam)
    p.apply(3.5)                       # 把 t=3.5s 的采样值写回节点/相机
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from engine.timeline import INTERPOLATIONS, Channel, Timeline, make_sampler

# 相机"伪节点"名（不是场景图里的节点，由 ShotPlayer 单独处理）
CAMERA_NODE = '$camera'
CAMERA_PROPS = ('position', 'target', 'fov')

# 环境光"伪节点"名 —— 对应 engine.env.LightEnvironment 的属性。
# Renderer 每帧调 ``environment.apply()``，所以这些通道是**真·灯光演出**（立刻可见），
# 且不需要 rebake（只走 uniform，不动 IBL 贴图）。
ENV_NODE = '$env'
ENV_PROPS = ('sun_intensity', 'sun_dir', 'ibl_intensity', 'sky_intensity')

# 各属性的取值形态（决定默认插值与校验）
_PROP_KIND = {
    'position': 'vec3', 'target': 'vec3', 'translation': 'vec3', 'scale': 'vec3',
    'tint': 'vec3', 'color': 'vec3', 'sun_dir': 'vec3',
    'fov': 'float', 'intensity': 'float', 'glow': 'float',
    'sun_intensity': 'float', 'ibl_intensity': 'float', 'sky_intensity': 'float',
    'rotation': 'quat',
}
# 需要"绕最短弧"插值的属性（四元数）
_QUAT_PROPS = ('rotation',)


def prop_kind(prop: str) -> str:
    """属性取值形态：``vec3`` / ``float`` / ``quat``（未知按 float）。"""
    return _PROP_KIND.get(prop, 'float')


def _deval(v):
    """采样结果 -> 纯 Python 值（float / tuple），便于单测与 JSON 比对。"""
    if isinstance(v, (tuple, list)):
        return tuple(float(x) for x in v)
    return float(v)


class Shot:
    """一段演出：通道集合 + 幕标记 + 元信息，可往返 JSON。"""

    VERSION = 1

    def __init__(self, name: str = 'shot', duration: float = 10.0,
                 scene: Optional[str] = None, acts=None, notes: str = ''):
        self.name = name
        self.duration = float(duration)
        self.scene = scene                      # 场景 JSON 的**路径引用**（可为 None）
        self.acts = list(acts or [])            # [{'t': float, 'label': str}]
        self.notes = notes
        self.channels = []                      # [dict(node, prop, times, values, interpolation)]

    # ---------------------------------------------------------------- 通道
    def add_channel(self, node: str, prop: str, times, values,
                    interpolation: str = 'CUBICSPLINE') -> int:
        """新增通道，返回下标。``times``/``values`` 长度必须一致（关键帧）。

        ``times`` 会自动**按时间升序排序**并**去重**（同 ``t`` 取后者），
        因此手写 JSON 顺序颠倒/重复都不会让 ``Sampler`` 的严格递增断言炸掉。
        """
        if interpolation not in INTERPOLATIONS:
            raise ValueError(f'未知插值方式：{interpolation}（可选 {INTERPOLATIONS}）')
        if len(times) != len(values):
            raise ValueError(f'关键帧数不匹配：{len(times)} times vs {len(values)} values')
        merged = {}
        for t, v in zip(times, values):
            merged[round(float(t), 6)] = self._norm_value(prop, v)   # 同 t 后者胜
        ordered = sorted(merged.items())
        ch = dict(node=node, prop=prop,
                  times=[t for t, _ in ordered],
                  values=[v for _, v in ordered],
                  interpolation=interpolation)
        self.channels.append(ch)
        self._touch(ch)
        return len(self.channels) - 1

    def channel(self, index: int) -> dict:
        return self.channels[index]

    def find_channel(self, node: str, prop: str) -> Optional[int]:
        for i, c in enumerate(self.channels):
            if c['node'] == node and c['prop'] == prop:
                return i
        return None

    def ensure_channel(self, node: str, prop: str, interpolation: str = 'CUBICSPLINE') -> int:
        """有则返回、无则建（**方便 UI**：`K` 键记录时自动建通道）。"""
        i = self.find_channel(node, prop)
        return i if i is not None else self.add_channel(node, prop, [], [], interpolation)

    def remove_channel(self, index: int):
        self.channels.pop(index)

    # ---------------------------------------------------------------- 关键帧
    def set_key(self, index: int, t: float, value, tol: float = 1e-6) -> dict:
        """增/改关键帧（同 ``t`` 覆盖）。返回 ``{ok, replaced}``。"""
        ch = self.channels[index]
        t = float(t)
        v = self._norm_value(ch['prop'], value)
        for k, kt in enumerate(ch['times']):
            if abs(kt - t) <= tol:
                ch['values'][k] = v
                self._touch(ch)
                return dict(ok=True, replaced=True)
        # 插入并保持按时间排序
        pos = 0
        while pos < len(ch['times']) and ch['times'][pos] < t:
            pos += 1
        ch['times'].insert(pos, t)
        ch['values'].insert(pos, v)
        self._touch(ch)
        return dict(ok=True, replaced=False)

    def del_key(self, index: int, t: float, tol: float = 0.03) -> bool:
        """删最近的一个关键帧（``tol`` 内）。"""
        ch = self.channels[index]
        best, bd = None, 1e9
        for k, kt in enumerate(ch['times']):
            d = abs(kt - float(t))
            if d < bd:
                best, bd = k, d
        if best is None or bd > tol:
            return False
        ch['times'].pop(best)
        ch['values'].pop(best)
        self._touch(ch)
        return True

    # ---------------------------------------------------------------- 采样
    def _sampler(self, ch: dict):
        """通道 → ``Sampler``（裸值 + 自动切线，见 `engine.timeline.make_sampler`）。"""
        return make_sampler(ch['times'], ch['values'], ch['interpolation'],
                            kind=prop_kind(ch['prop']))

    def build_timeline(self) -> Timeline:
        """构建 `engine.timeline.Timeline`（通道 → Sampler → Channel，**零转换**）。"""
        chans = []
        for c in self.channels:
            if not c['times']:
                continue
            # node 传字符串（Timeline 只把它当标签用；真正写回由 ShotPlayer 做）
            chans.append(Channel(c['node'], c['prop'], self._sampler(c)))
        return Timeline(max(self.duration, 1e-6), chans)

    def sample(self, t: float) -> dict:
        """在 ``t`` 处采样全部通道 → ``{(node, prop): value}``（不依赖 Timeline，便于测试）。

        返回值一律是纯 Python（``float`` / ``tuple``），空通道（无关键帧）被跳过。
        """
        out = {}
        for c in self.channels:
            if not c['times']:
                continue
            if len(c['times']) == 1:
                out[(c['node'], c['prop'])] = _deval(c['values'][0])
                continue
            out[(c['node'], c['prop'])] = _deval(self._sampler(c).sample(float(t)))
        return out

    # ---------------------------------------------------------------- IO
    def to_dict(self) -> dict:
        return dict(version=self.VERSION, name=self.name, duration=self.duration,
                    scene=self.scene, acts=[dict(a) for a in self.acts],
                    notes=self.notes,
                    channels=[dict(node=c['node'], prop=c['prop'],
                                   interpolation=c['interpolation'],
                                   times=list(c['times']),
                                   values=[self._ser(c['prop'], v) for v in c['values']])
                              for c in self.channels])

    @classmethod
    def from_dict(cls, d: dict) -> 'Shot':
        s = cls(name=d.get('name', 'shot'), duration=float(d.get('duration', 10.0)),
                scene=d.get('scene'), acts=d.get('acts') or [], notes=d.get('notes', ''))
        for c in (d.get('channels') or []):
            s.add_channel(c['node'], c['prop'], c.get('times') or [],
                          c.get('values') or [], c.get('interpolation', 'CUBICSPLINE'))
        return s

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=1)
        return path

    @classmethod
    def load(cls, path: str) -> 'Shot':
        with open(path, 'r', encoding='utf-8') as f:
            return cls.from_dict(json.load(f))

    # ---------------------------------------------------------------- 内部
    @staticmethod
    def _norm_value(prop: str, v):
        kind = prop_kind(prop)
        if kind in ('vec3', 'quat'):
            need = 3 if kind == 'vec3' else 4
            label = f'{prop}（四元数）' if kind == 'quat' else prop
            if isinstance(v, (int, float)):
                raise ValueError(f'{label} 需要 {need} 个分量，收到标量 {v!r}')
            try:
                t = tuple(float(x) for x in v)
            except TypeError:
                raise ValueError(f'{label} 需要 {need} 个分量，收到 {v!r}') from None
            if len(t) != need:
                raise ValueError(f'{label} 需要 {need} 个分量，得到 {len(t)}')
            return t
        if isinstance(v, (tuple, list)):
            raise ValueError(f'{prop} 是标量（{kind}），但收到 {len(v)} 个分量：{v!r}')
        return float(v)

    @staticmethod
    def _ser(prop: str, v):
        kind = prop_kind(prop)
        return list(v) if kind in ('vec3', 'quat') else float(v)

    @staticmethod
    def _touch(ch: dict):
        """占位：通道变化钩子（UI 可覆写用于标脏）。"""
        ch['_rev'] = ch.get('_rev', 0) + 1

    def status_text(self) -> str:
        nodes = len({c['node'] for c in self.channels})
        keys = sum(len(c['times']) for c in self.channels)
        return (f'演出「{self.name}」 {self.duration:.1f}s  '
                f'通道 {len(self.channels)}（{nodes} 个对象）  关键帧 {keys}  幕 {len(self.acts)}')


# ==================================================================== 播放
class ShotPlayer:
    """把 `Shot` 在时刻 ``t`` 的采样值**写回**场景节点 / 相机 / 环境光。"""

    def __init__(self, shot: Shot, scene=None, camera=None, env=None, fov_attr='fov'):
        self.shot = shot
        self.scene = scene
        self.camera = camera
        self.env = env
        self.fov_attr = fov_attr
        self._node_cache = {}

    def _node(self, name: str):
        if self.scene is None:
            return None
        if name not in self._node_cache:
            found = None
            root = getattr(self.scene, 'root', None)
            if root is not None:
                for n in root.traverse():
                    if n.name == name:
                        found = n
                        break
            self._node_cache[name] = found
        return self._node_cache[name]

    def apply(self, t: float) -> int:
        """写入 ``t`` 时刻的采样值；返回**实际写入的通道数**（找不到目标的会被跳过）。"""
        n = 0
        for (node_name, prop), val in self.shot.sample(t).items():
            if node_name == CAMERA_NODE:
                if self._apply_camera(prop, val):
                    n += 1
                continue
            if node_name == ENV_NODE:
                if self._apply_env(prop, val):
                    n += 1
                continue
            node = self._node(node_name)
            if node is None:
                continue
            if prop == 'rotation':
                node.rotation = tuple(val)
            elif prop == 'scale':
                node.scale = tuple(val)
            elif prop in ('translation', 'position'):
                node.translation = tuple(val)
            elif hasattr(node, 'user_data'):
                node.user_data[prop] = val          # 任意自定义属性（如 intensity）
            else:
                continue
            node.mark_dirty() if hasattr(node, 'mark_dirty') else None
            n += 1
        return n

    def _apply_env(self, prop: str, val) -> bool:
        """写回 `engine.env.LightEnvironment`（渲染器每帧 apply，立刻可见）。"""
        env = self.env
        if env is None or not hasattr(env, prop):
            return False
        setattr(env, prop, tuple(val) if prop_kind(prop) in ('vec3', 'quat') else float(val))
        return True

    def _apply_camera(self, prop: str, val) -> bool:
        cam = self.camera
        if cam is None:
            return False
        try:
            if prop == 'position':
                if hasattr(cam, 'position'):
                    cam.position = val
                elif hasattr(cam, 'target') and hasattr(cam, 'dist'):
                    # 轨道相机：把"位置"换算成 yaw/pitch/dist（尽力而为）
                    return False
                else:
                    return False
            elif prop == 'target':
                if hasattr(cam, 'target'):
                    from pyglet.math import Vec3
                    cam.target = Vec3(*val) if not isinstance(cam.target, tuple) else tuple(val)
                else:
                    return False
            elif prop == 'fov':
                if hasattr(cam, self.fov_attr):
                    setattr(cam, self.fov_attr, float(val))
                else:
                    return False
            else:
                return False
            return True
        except Exception:
            return False

    def capture_camera(self, t: float) -> dict:
        """把**当前**相机位姿作为关键帧记到 ``t``（导演台 `K` 键用）。"""
        cam = self.camera
        out = {}
        if cam is None:
            return out
        pos = getattr(cam, 'position', None)
        if pos is not None:
            out['position'] = tuple(pos)
        tgt = getattr(cam, 'target', None)
        if tgt is not None:
            out['target'] = tuple(tgt)
        fov = getattr(cam, self.fov_attr, None)
        if fov is not None:
            out['fov'] = float(fov)
        return out

    def capture_env(self) -> dict:
        """把**当前**环境光参数作为关键帧值（导演台编辑 `$env` 时用）。"""
        env = self.env
        out = {}
        if env is None:
            return out
        for prop in ENV_PROPS:
            v = getattr(env, prop, None)
            if v is None:
                continue
            out[prop] = tuple(v) if prop_kind(prop) in ('vec3', 'quat') else float(v)
        return out

    def capture_node(self, name: str) -> dict:
        """把**当前**节点 TRS 作为关键帧值（导演台编辑场景对象时用）。"""
        node = self._node(name)
        if node is None:
            return {}
        return {'translation': tuple(node.translation),
                'rotation': tuple(node.rotation),
                'scale': tuple(node.scale)}

    # 相机伪节点 → 可编辑属性
    def camera_props(self):
        return CAMERA_PROPS

    def animatable_nodes(self) -> list:
        """场景里可被编辑/打关键帧的节点名（去掉纯容器的 root）。"""
        if self.scene is None:
            return []
        root = getattr(self.scene, 'root', None)
        if root is None:
            return []
        names = []
        for n in root.traverse():
            if n is root or n.name in names:
                continue
            names.append(n.name)
        return names


__all__ = ['Shot', 'ShotPlayer', 'CAMERA_NODE', 'CAMERA_PROPS',
           'ENV_NODE', 'ENV_PROPS', 'prop_kind']

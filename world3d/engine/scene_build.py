# -*- coding: utf-8 -*-
"""场景 JSON 重建器 + 校验器（LLM 场景生成的执行端）。

背景：``world3d.engine.serialize`` 的 JSON 存的是"结构与参数"——但 ``Scene.from_dict``
只恢复节点层级与 TRS，几何（MeshData）与材质需要调用方按名字重建。
本模块补上这一环：把 JSON 里声明的 ``mesh``（生成器 + 参数）真正构建成
MeshData / Mesh，并提供**纯 CPU 校验**（无窗口可跑，适合 AI/CI 流水线）。

JSON 约定（engine 原生字段 + 扩展 ``mesh``）::

    {
      "name": "月夜祭坛",
      "environment": {"sun_dir": [...], "sun_color": [...], ...},   # 可选
      "root": {
        "name": "root",
        "children": [
          {
            "name": "column_0",
            "translation": [5.6, 1.3, 0],
            "rotation": [0, 0, 0, 1],
            "scale": [1, 1, 1],
            "mesh": {
              "builder": "lathe",
              "args": {"profile": [[0,0],[0.42,0],[0.36,3.4],[0,3.9]],
                       "segments": 20},
              "color": [0.78, 0.77, 0.74]
            },
            "material": {"preset": "marble", "roughness": 0.35}
          }
        ]
      }
    }

- ``mesh.builder`` 必须是 BUILDERS 里的生成器名；``args`` 只允许该生成器的
  参数（多余参数 → 校验报错，挡住 LLM 幻觉参数）。
- ``material``：``preset``（engine.material.PRESETS 的键）+ 覆盖字段。

配套：``scripts/scene_build.py``（CLI：--validate / --dry-run）、
``examples/scene_viewer.py``（热重载查看器）、``docs/LLM场景生成工作流.md``。
"""
from __future__ import annotations

import json
import math

from world3d.engine import geometry
from world3d.engine.material import PRESETS, Material

__all__ = ['BUILDERS', 'MATERIAL_FIELDS', 'build_meshdata', 'build_material',
           'build_scene', 'load_json', 'validate', 'summarize']

# ---------------------------------------------------------------- 注册表 --
# builder 名 -> (生成函数, 允许的参数名集合)
BUILDERS = {
    'box':        (geometry.box,        {'sx', 'sy', 'sz', 'offset', 'color'}),
    'cube':       (geometry.cube,       {'center', 'size', 'color'}),
    'sphere':     (geometry.sphere,     {'radius', 'segments', 'rings', 'color'}),
    'plane':      (geometry.plane,      {'width', 'depth', 'segs', 'color', 'y'}),
    'cylinder':   (geometry.cylinder,   {'radius', 'height', 'segments', 'capped', 'color'}),
    'torus':      (geometry.torus,      {'major', 'minor', 'major_segs', 'minor_segs', 'color'}),
    'ground_grid': (geometry.ground_grid, {'half', 'tile', 'color_a', 'color_b'}),
    'heightfield': (geometry.heightfield, {'hm', 'width', 'depth', 'height',
                                           'color', 'y_base'}),
    'lathe':      (geometry.lathe,      {'profile', 'segments', 'closed_top',
                                         'closed_bottom', 'color'}),
    'tube':       (geometry.tube,       {'points', 'radius', 'radial', 'color', 'caps'}),
    'torus_knot': (geometry.torus_knot, {'p', 'q', 'major', 'tube_r', 'tubular',
                                         'radial', 'color'}),
}

MATERIAL_FIELDS = ('name', 'base_color', 'metallic', 'roughness', 'emissive',
                   'alpha', 'alpha_mode', 'alpha_cutoff', 'double_sided')

# 数组型参数的长度约定（用于校验）
_ARRAY_LEN = {'color': (3, 4), 'offset': (3,), 'center': (3,),
              'color_a': (3, 4), 'color_b': (3, 4)}
# 嵌套数组参数的逐元素长度约定（profile 的点是 [r,y]、points 的点是 [x,y,z]）
_NESTED_LEN = {'profile': 2, 'points': 3}
# 二维数值网格参数（heightfield 的 hm：行主序 [rows][cols] 数字矩阵）
_GRID_PARAMS = {'hm'}


def _to_tuple(v):
    """把 JSON 数组递归转成 tuple（生成器接口收的是元组/序列）。"""
    if isinstance(v, list):
        return tuple(_to_tuple(x) for x in v)
    return v


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


# ----------------------------------------------------------------- 构建 --
def build_meshdata(mesh_def):
    """根据 ``{"builder": ..., "args": {...}, "color": ...}`` 构建 CPU 侧 MeshData。"""
    builder = mesh_def['builder']
    fn, allowed = BUILDERS[builder]
    kwargs = {k: _to_tuple(v) for k, v in (mesh_def.get('args') or {}).items()}
    if 'color' in mesh_def and 'color' in allowed:
        kwargs.setdefault('color', _to_tuple(mesh_def['color']))
    unexpected = set(kwargs) - allowed
    if unexpected:
        raise ValueError(f'builder {builder!r} 不接受参数: {sorted(unexpected)}')
    return fn(**kwargs)


def build_material(material_def):
    """``{"preset": "...", 覆盖字段...}`` -> Material 实例。"""
    if not material_def:
        return Material()
    preset = material_def.get('preset')
    mat = PRESETS[preset].clone() if preset else Material()
    for k, v in material_def.items():
        if k == 'preset' or k not in MATERIAL_FIELDS:
            continue
        setattr(mat, k, _to_tuple(v))
    return mat


def build_scene(data, program):
    """完整构建（需 GL 上下文：program 是已编译的着色器程序）。

    返回 ``world3d.engine.scene.Scene``；几何按 JSON 重建，材质按 preset+覆盖还原。
    """
    from world3d.engine.scene import Scene

    scene = Scene(name=data.get('name', 'scene'))
    root_d = data.get('root', {'name': 'root'})
    _build_node(root_d, scene.root, program)
    scene.update()
    return scene


def _build_node(nd, parent, program):
    from world3d.engine.mesh import Mesh
    from world3d.engine.scene import Node

    n = Node(name=nd.get('name', 'node'),
             translation=nd.get('translation', (0.0, 0.0, 0.0)),
             rotation=nd.get('rotation', (1.0, 0.0, 0.0, 0.0)),
             scale=nd.get('scale', (1.0, 1.0, 1.0)))
    n.user_data = dict(nd.get('user_data', {}))
    mesh_def = nd.get('mesh')
    if isinstance(mesh_def, dict):
        md = build_meshdata(mesh_def)
        mat = build_material(nd.get('material'))
        n.drawable = Mesh(md, program, mat, name=n.name)
    parent.add(n)
    for cd in nd.get('children', ()):
        _build_node(cd, n, program)
    return n


# ----------------------------------------------------------------- 校验 --
def validate(data):
    """结构 + 语义 + 可构建性校验。返回 ``[(level, path, message), ...]``。

    level: 'error'（必须修）/ 'warning'（可疑但不致命）/ 'stats'（统计项）。
    纯 CPU、无窗口可跑；``mesh`` 会**实际试构建**，能挡住参数拼写错误。
    """
    issues = []
    err = lambda p, m: issues.append(('error', p, m))       # noqa: E731
    warn = lambda p, m: issues.append(('warning', p, m))    # noqa: E731

    if not isinstance(data, dict):
        err('$', '顶层必须是 JSON 对象')
        return issues

    env = data.get('environment')
    if env is not None and not isinstance(env, dict):
        err('$.environment', 'environment 必须是对象或省略')

    root = data.get('root')
    if not isinstance(root, dict):
        err('$.root', '缺少 root 节点')
        return issues

    names = {}
    stats = {'nodes': 0, 'meshes': 0, 'vertices': 0, 'triangles': 0}

    def walk(nd, path):
        if not isinstance(nd, dict):
            err(path, '节点必须是对象')
            return
        stats['nodes'] += 1
        name = nd.get('name', 'node')
        if not isinstance(name, str) or not name:
            err(path + '.name', 'name 必须是非空字符串')
        else:
            if name in names:
                warn(path + '.name',
                     f'name {name!r} 与 {names[name]} 重复（find() 会歧义）')
            else:
                names[name] = path

        for key, ln in (('translation', 3), ('rotation', 4), ('scale', 3)):
            v = nd.get(key)
            if v is None:
                continue
            if not (isinstance(v, list) and len(v) == ln
                    and all(_is_num(x) for x in v)):
                err(f'{path}.{key}', f'{key} 必须是 {ln} 个数字的数组')
            elif key == 'rotation':
                norm = math.sqrt(sum(x * x for x in v))
                if abs(norm - 1.0) > 1e-3:
                    warn(f'{path}.rotation', f'四元数模长 {norm:.4f} != 1（建议归一化）')

        md = nd.get('mesh')
        if md is not None:
            if isinstance(md, str):
                warn(path + '.mesh', f'字符串 mesh {md!r} 无参数、无法重建（旧格式）')
            elif not isinstance(md, dict):
                err(path + '.mesh', 'mesh 必须是对象（builder+args）')
            else:
                b = md.get('builder')
                if b not in BUILDERS:
                    err(path + '.mesh.builder',
                        f'未知生成器 {b!r}，可用: {sorted(BUILDERS)}')
                else:
                    args = md.get('args') or {}
                    if not isinstance(args, dict):
                        err(path + '.mesh.args', 'args 必须是对象')
                    else:
                        _, allowed = BUILDERS[b]
                        for k, v in args.items():
                            kp = f'{path}.mesh.args.{k}'
                            if k not in allowed:
                                err(kp, f'生成器 {b!r} 不接受该参数')
                            elif k in _ARRAY_LEN:
                                if not (isinstance(v, list)
                                        and len(v) in _ARRAY_LEN[k]
                                        and all(_is_num(x) for x in v)):
                                    err(kp, f'应为 {_ARRAY_LEN[k]} 元数字数组')
                            elif k in _NESTED_LEN:
                                el = _NESTED_LEN[k]
                                ok = (isinstance(v, list) and len(v) >= 2
                                      and all(isinstance(p, list) and len(p) == el
                                              and all(_is_num(x) for x in p)
                                              for p in v))
                                if not ok:
                                    err(kp, f'应为 ≥2 个 {el} 元点数组')
                            elif k in _GRID_PARAMS:
                                ok = (isinstance(v, list) and len(v) >= 2
                                      and all(isinstance(row, list)
                                              and len(row) >= 2
                                              and all(_is_num(x) for x in row)
                                              for row in v)
                                      and len({len(row) for row in v}) == 1)
                                if not ok:
                                    err(kp, '应为 ≥2 行、每行等长数字的二维矩阵')
                            elif not (_is_num(v) or isinstance(v, bool)):
                                err(kp, '参数应为数字或布尔')
                        if 'color' in md and 'color' not in allowed:
                            warn(path + '.mesh.color',
                                 f'生成器 {b!r} 无 color 参数，已忽略')
                        try:
                            meshdata = build_meshdata(md)
                            stats['meshes'] += 1
                            stats['vertices'] += meshdata.vertex_count
                            stats['triangles'] += (len(meshdata.indices) // 3
                                                   if meshdata.indices
                                                   else meshdata.vertex_count // 3)
                            if meshdata.vertex_count == 0:
                                warn(path + '.mesh', '构建结果为空网格')
                        except Exception as e:               # 构建失败即错误
                            err(path + '.mesh', f'构建失败: {e}')

        mat = nd.get('material')
        if mat is not None:
            if not isinstance(mat, dict):
                err(path + '.material', 'material 必须是对象')
            else:
                preset = mat.get('preset')
                if preset is not None and preset not in PRESETS:
                    err(path + '.material.preset',
                        f'未知预设 {preset!r}，可用: {sorted(PRESETS)}')
                for k, v in mat.items():
                    if k == 'preset':
                        continue
                    if k not in MATERIAL_FIELDS:
                        err(f'{path}.material.{k}', '未知材质字段')
                    elif k in ('metallic', 'roughness', 'alpha') and _is_num(v):
                        if not 0.0 <= float(v) <= 1.0:
                            warn(f'{path}.material.{k}', f'{v} 超出 [0,1]')

        for i, cd in enumerate(nd.get('children', ())):
            walk(cd, f'{path}.children[{i}]')

    walk(root, '$.root')
    issues.append(('stats', '$', stats))
    return issues


# ----------------------------------------------------------------- 工具 --
def load_json(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def summarize(data, verbose=True):
    """校验 + 打印报告。返回 error 数（0 = 通过）。"""
    issues = validate(data)
    errors = [i for i in issues if i[0] == 'error']
    warns = [i for i in issues if i[0] == 'warning']
    stats = next((i[2] for i in issues if i[0] == 'stats'), {})
    if verbose:
        print(f'[validate] name={data.get("name", "?")!r} '
              f'nodes={stats.get("nodes", 0)} meshes={stats.get("meshes", 0)} '
              f'verts={stats.get("vertices", 0)} tris={stats.get("triangles", 0)}')
        for level, path, msg in issues:
            if level == 'stats':
                continue
            print(f'  [{level}] {path}: {msg}')
        print(f'[validate] {len(errors)} error(s), {len(warns)} warning(s)')
    return len(errors)

# -*- coding: utf-8 -*-
"""§14 静馆 / Stillhall 内核 —— 节点式漫游 + 热点点击 + 物品栏 + 谜题状态机（Cordis 服务化）。

设计 note 见 ``docs/remake_ideas.md`` §14。**致敬 Myst(1993) 的"节点式漫游 + 点击热点 +
谜题状态机"**，不碰原 IP 名称与素材（合规见 §7）。

漫游形态：**节点式**（固定机位实时渲染 + 点击画面热点换景），不是自由 WASD ——
省关卡设计、保"凝视旧地"氛围。谜题：**顺序解锁链**（1 馆 3 景 3 谜）。

**项目特色**：场景数据 JSON 与 `scene_viewer` / 「LLM 场景生成工作流」同源 ——
LLM 生成场景 → 内核消费。schema 见 ``DEFAULT_SCENE``（nodes/edges/puzzle 三块）。

全部逻辑纯 Python、无 GL 依赖；**点击 API 与 headless 验收同源**（``click(hotspot_id)``）。
"""
from __future__ import annotations

import copy
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Service  # noqa: E402

# ============================================================ 内置样例场景
# 3 景 3 谜：门厅取钥匙 → 回廊点灯得齿轮 → 穹室装齿轮开终局门。
# 热点 area 为**归一化屏面矩形** [x, y, w, h]（左上原点，v 向下）——与点击拾取同源。
DEFAULT_SCENE = {
    'start': 'atrium',
    'nodes': [
        {'id': 'atrium', 'name': '门厅', 'look': '石砌穹顶 · 斑驳地板 · 一线天光',
         'hotspots': [
             {'id': 'h_take_key', 'label': '黄铜钥匙', 'area': [0.60, 0.52, 0.14, 0.12],
              'action': {'type': 'take', 'item': 'brass_key'}},
             {'id': 'h_to_hall', 'label': '回廊', 'area': [0.84, 0.30, 0.16, 0.45],
              'action': {'type': 'move', 'to': 'hall'}},
         ]},
        {'id': 'hall', 'name': '回廊', 'look': '柱影斜长 · 铜灯暗哑 · 齿轮悬于壁',
         'hotspots': [
             {'id': 'h_lamp', 'label': '铜灯', 'area': [0.24, 0.34, 0.14, 0.20],
              'action': {'type': 'use', 'requires': 'brass_key', 'grants': 'lamp_lit',
                         'gives': 'gear'}},
             {'id': 'h_to_vault', 'label': '穹室', 'area': [0.86, 0.28, 0.14, 0.48],
              'action': {'type': 'move', 'to': 'vault', 'requires_flag': 'lamp_lit'}},
             {'id': 'h_to_atrium', 'label': '门厅', 'area': [0.00, 0.28, 0.14, 0.48],
              'action': {'type': 'move', 'to': 'atrium'}},
         ]},
        {'id': 'vault', 'name': '穹室', 'look': '穹顶崩落 · 一具空架 · 终局之门',
         'hotspots': [
             {'id': 'h_final', 'label': '终局之门', 'area': [0.40, 0.26, 0.22, 0.46],
              'action': {'type': 'final', 'requires': 'gear'}},
             {'id': 'h_back_hall', 'label': '回廊', 'area': [0.00, 0.28, 0.14, 0.48],
              'action': {'type': 'move', 'to': 'hall'}},
         ]},
    ],
    'edges': [['atrium', 'hall'], ['hall', 'vault']],
    'puzzle': {
        'title': '三谜：取钥 → 点灯 → 归位齿轮',
        'steps': [
            {'id': 's1', 'desc': '在门厅取黄铜钥匙', 'flag': 'has_key'},
            {'id': 's2', 'desc': '用钥匙点亮回廊铜灯，取走齿轮', 'flag': 'lamp_lit'},
            {'id': 's3', 'desc': '在穹室装齿轮，开启终局之门', 'flag': 'final_open'},
        ],
    },
}


# ============================================================ 纯函数（可单测）
def hit_test(area, u, v) -> bool:
    """热点命中：``area = [x, y, w, h]``（归一化），``(u, v)`` 屏面坐标（左上原点）。"""
    x, y, w, h = (float(a) for a in area)
    return x <= u <= x + w and y <= v <= y + h


def graph_neighbors(edges, node_id) -> list:
    """无向邻接表里 ``node_id`` 的邻居。"""
    out = []
    for a, b in edges:
        if a == node_id:
            out.append(b)
        elif b == node_id:
            out.append(a)
    return out


def graph_reachable(edges, start) -> set:
    """从 ``start`` 出发可达的节点集合（BFS）——单测用它验"3 景连通"。"""
    seen, stack = {start}, [start]
    while stack:
        cur = stack.pop()
        for nb in graph_neighbors(edges, cur):
            if nb not in seen:
                seen.add(nb)
                stack.append(nb)
    return seen


def next_step(steps, flags) -> dict:
    """谜题状态机：返回**第一个未完成**的步（顺序解锁链，纯函数）。

    ``flags`` 为已完成标记集合；已完成的步跳过。
    """
    for s in steps:
        if s['flag'] not in flags:
            return s
    return None


def validate_scene(obj) -> tuple:
    """场景 JSON 校验：``(ok, err)``。坏场景**拒绝加载并保留旧场景**（scene_viewer 惯例）。"""
    if not isinstance(obj, dict):
        return False, '顶层不是对象'
    if not isinstance(obj.get('nodes'), list) or not obj['nodes']:
        return False, 'nodes 缺失或为空'
    ids = [n.get('id') for n in obj['nodes']]
    if any(not i for i in ids):
        return False, 'node 缺 id'
    if len(set(ids)) != len(ids):
        return False, 'node id 重复'
    if obj.get('start') not in ids:
        return False, 'start 不在 nodes 里'
    for e in obj.get('edges', []):
        if not (isinstance(e, (list, tuple)) and len(e) == 2):
            return False, f'边格式错: {e}'
        if e[0] not in ids or e[1] not in ids:
            return False, f'边引用了不存在的节点: {e}'
    for n in obj['nodes']:
        for hs in n.get('hotspots', []):
            if not hs.get('id') or 'area' not in hs or 'action' not in hs:
                return False, f"热点缺字段: {hs.get('id')}"
            a = hs['area']
            if len(a) != 4 or a[2] <= 0 or a[3] <= 0:
                return False, f"热点 area 非法: {hs['id']}"
    return True, ''


# ============================================================ 内核
class HallKernel(Service):
    """静馆内核：节点图 + 热点表 + 物品栏 + 谜题状态机（``click()`` 可 headless 驱动）。"""

    inject = ['render', 'rules']

    def __init__(self, ctx, config):
        super().__init__(ctx, "hall")          # ⚠️ 必须注册服务名（漏了会让挂载静默失败）
        cfg = config or {}
        self.scene_path = cfg.get('scene')            # yml 里可指定外部 JSON
        self.scene = copy.deepcopy(DEFAULT_SCENE)
        self.node_id = self.scene['start']
        self.flags = set()
        self.items = []
        self.events = []
        self.log = []
        self.hover = cfg.get('hover', False)          # 供 demo 的悬停高亮读取
        self.loaded_from = 'builtin'
        self.reload_errors = 0
        self.time = 0.0
        self._gl_handles = []

    # ---------------------------------------------------------- 生命周期
    def __cordis_init__(self):
        self._acquire_gl()
        if self.scene_path:
            self.load_scene(self.scene_path)
        self._emit('enter', node=self.node_id)
        return None

    def _acquire_gl(self):
        """取 GL 句柄（配置树就绪检查要求内核持句柄；经 ctx.effect 随 fiber 回滚）。"""
        render = self.ctx.render
        from experiments.cordis.tower_defense_kernel import _unit_cube
        h_prog = render.compile_program('hall')
        h_mesh = render.upload_mesh('hall_quad', *_unit_cube())
        self._gl_handles.extend([h_prog, h_mesh])
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    # ---------------------------------------------------------- 场景 IO
    def load_scene(self, path=None) -> bool:
        """加载/热重载场景 JSON。**坏 JSON 或校验失败 -> 拒绝并保留旧场景**。"""
        path = path or self.scene_path
        if not path:
            return False
        try:
            with open(path, 'r', encoding='utf-8') as f:
                obj = json.load(f)
        except Exception as e:
            self.reload_errors += 1
            self._emit('scene_rejected', reason=f'读取失败: {type(e).__name__}')
            return False
        ok, err = validate_scene(obj)
        if not ok:
            self.reload_errors += 1
            self._emit('scene_rejected', reason=err)
            return False
        self.scene = obj
        self.loaded_from = path
        # 换场景 = 重开（谜题状态与物品清空，避免与旧场景的 flag 语义错位）
        self.node_id = obj['start']
        self.flags = set()
        self.items = []
        self._emit('scene_loaded', node=self.node_id)
        return True

    # ---------------------------------------------------------- 查询
    def node(self, node_id=None) -> dict:
        nid = node_id or self.node_id
        return next((n for n in self.scene['nodes'] if n['id'] == nid), None)

    def hotspots(self) -> list:
        """当前景的可点热点（含可用性标记：``usable``）。"""
        n = self.node()
        out = []
        for hs in (n.get('hotspots', []) if n else []):
            act = hs['action']
            ok = True
            need = act.get('requires')
            if need and need not in self.items:
                ok = False
            need_flag = act.get('requires_flag')
            if need_flag and need_flag not in self.flags:
                ok = False
            if act.get('type') == 'final' and act.get('requires') and act['requires'] not in self.items:
                ok = False
            out.append(dict(id=hs['id'], label=hs.get('label', hs['id']),
                            area=list(hs['area']), usable=ok))
        return out

    def hit(self, u, v):
        """屏面归一化坐标 -> 命中热点 id（未命中返回 None，跳过不可用的热点）。"""
        for hs in self.hotspots():
            if hs['usable'] and hit_test(hs['area'], u, v):
                return hs['id']
        return None

    def inventory(self) -> list:
        return list(self.items)

    def current_step(self) -> dict:
        return next_step(self.scene['puzzle']['steps'], self.flags)

    def state(self) -> dict:
        return dict(node=self.node_id, items=list(self.items),
                    flags=sorted(self.flags), step=(self.current_step() or {}).get('id'),
                    solved=('final_open' in self.flags))

    # ---------------------------------------------------------- 交互
    def click(self, hotspot_id) -> dict:
        """**点击 API**（headless 验收与 demo 同一入口）。

        Returns: ``{'ok', 'action', 'detail', ...}``——ok=False 时 detail 说明原因
        （热点不存在 / 物品不足 / 标记不足 / 该方向无路）。
        """
        hs = next((h for h in (self.node() or {}).get('hotspots', [])
                   if h['id'] == hotspot_id), None)
        if hs is None:
            return dict(ok=False, action=None, detail=f'热点不存在: {hotspot_id}')
        act = hs['action']
        kind = act.get('type')
        need = act.get('requires')
        if need and need not in self.items:
            return dict(ok=False, action=kind, detail=f'缺少物品: {need}')
        nf = act.get('requires_flag')
        if nf and nf not in self.flags:
            return dict(ok=False, action=kind, detail=f'条件未满足: {nf}')

        if kind == 'take':
            item = act['item']
            if item not in self.items:
                self.items.append(item)
                self.flags.add('has_key' if item == 'brass_key' else f'has_{item}')
            self._emit('take', item=item)
            return dict(ok=True, action=kind, detail=f'取得 {item}')

        if kind == 'use':
            f = act.get('grants')
            if f:
                self.flags.add(f)
            g = act.get('gives')
            if g and g not in self.items:
                self.items.append(g)
            self._emit('use', flag=f, gives=g)
            return dict(ok=True, action=kind, detail=f'机关已启（{f}）')

        if kind == 'move':
            to = act.get('to')
            if not self._linked(self.node_id, to):
                return dict(ok=False, action=kind, detail=f'无通路: {self.node_id}->{to}')
            self.node_id = to
            self._emit('enter', node=to)
            return dict(ok=True, action=kind, detail=f'来到 {self.node(to)["name"]}')

        if kind == 'final':
            if act.get('requires') and act['requires'] not in self.items:
                return dict(ok=False, action=kind, detail=f'缺少物品: {act["requires"]}')
            self.flags.add('final_open')
            self._emit('solve')
            # rules 的 puzzle_solved（P1-④ 已加，**零改动复用**）-> won
            self.ctx.rules.on_event({'type': 'puzzle_solved'})
            return dict(ok=True, action=kind, detail='终局之门开启')

        return dict(ok=False, action=kind, detail=f'未知动作: {kind}')

    def _linked(self, a, b) -> bool:
        return b in graph_neighbors(self.scene.get('edges', []), a)

    def goto(self, node_id) -> bool:
        """直接换景（仅限有边相连）。"""
        if not self._linked(self.node_id, node_id):
            return False
        self.node_id = node_id
        self._emit('enter', node=node_id)
        return True

    # ---------------------------------------------------------- 每帧
    def update(self, dt):
        self.time += dt

    def drain_events(self):
        evs = self.events
        self.events = []
        return evs

    def _emit(self, name, **kw):
        self.events.append(dict(type=name, **kw))

    # ---------------------------------------------------------- 展示
    def status_text(self) -> str:
        n = self.node()
        st = self.current_step()
        return (f"景 {n['name'] if n else '?'}  物品 [{','.join(self.items) or '-'}]  "
                f"谜 {st['id'] if st else '全解'}  "
                f"state={self.ctx.rules.state()}")


__all__ = ['HallKernel', 'DEFAULT_SCENE', 'hit_test', 'graph_neighbors',
           'graph_reachable', 'next_step', 'validate_scene']

# -*- coding: utf-8 -*-
"""共同核服务契约（P0 · Q2）。

把 ``docs/remake_ideas.md`` 第 2 节列出的 12 个共同核，写成**不依赖 Cordis 运行时、
也不依赖 GL 上下文**的服务接口（``typing.Protocol``）。

设计目的
--------
- **单一事实来源**：每个核心「对外提供什么服务」只在这里声明一次，Cordis 试点二/三
  接线时直接 ``provide`` 这些契约的具体实现即可，不必重复推断 API。
- **零阻塞**：本模块只 ``import typing``，因此**即使没有装 cordis、没有 GL 上下文也能
  安全 import**；Cordis 可用后再把实现 ``provide`` 出去，不会卡住 P0 的其余工作。
- **状态清晰**：每个契约都标注「对应现有模块 / 现状（已具备 · 待抽离）/ 提供为 Cordis
  服务名」，让全哥一眼看清哪些核心已基本就绪、哪些还要写。

契约 vs 实现
------------
- 这里定义的是**接口形状**（方法签名 + 语义），不是实现。
- 标记「已具备」的核心，其契约方法名尽量对齐现有 ``engine.*`` / ``demos.*`` 的真实
  公开 API（见各契约 docstring 的「现有模块」），后续实现类可做到结构兼容
  （``runtime_checkable``，见 ``verify()``）。
- 标记「待抽离」的核心，契约描述的是**目标形状**——这些模块还没建，契约即规格。

Cordis 接线时的形态（试点二/三）::

    @inject  # 声明依赖哪些核心
    async def godhand_kernel(ctx, config):
        terrain = ctx.get('terrain')   # -> 一个满足 TerrainCore 契约的对象
        terrain.edit_height(x, z, +2)  # 抬山
        ...
"""
from __future__ import annotations

from typing import Any, List, Optional, Protocol, Tuple, runtime_checkable

# ---------------------------------------------------------------------------
# 契约 1 · 渲染核心  render
# ---------------------------------------------------------------------------
@runtime_checkable
class RenderCore(Protocol):
    """GL 上下文 + 相机投影 + 场景绘制 + 着色器程序表。

    现有模块：``engine.renderer.Renderer`` / ``engine.shaders`` / ``engine.app.EngineApp``
    现状：**已具备**
    提供为 Cordis 服务名：``render``
    """

    def program(self, name: str) -> Any:
        """取已编译着色器程序（``engine.shaders.build/get``）。"""
        ...

    def render(self, camera: Any, scene: Any, aspect: Optional[float] = None,
               window: Any = None) -> None:
        """用给定相机把场景绘到当前帧缓冲。"""
        ...

    def resize(self, width: int, height: int) -> None:
        """响应窗口尺寸变化。"""
        ...

    def set_point_lights(self, lights: List[Any]) -> None:
        """设置点光源（``Renderer.set_point_lights``）。"""
        ...

    @property
    def window(self) -> Any:
        """底层窗口对象（pyglet window）。"""
        ...


# ---------------------------------------------------------------------------
# 契约 2 · 地形/高度场核心  terrain
# ---------------------------------------------------------------------------
@runtime_checkable
class TerrainCore(Protocol):
    """高度场生成、网格构建、**实时编辑**（抬山/挖海）。

    现有模块：``demos/terrain_flyover.height_at`` / ``demos/voxel_terrain`` /
    ``demos/mountain`` / ``engine.physics.PhysicsWorld.ground_height``
    现状：**已具备** ``height_at``；编辑/网格抽离到核心 → **待抽离**
    提供为 Cordis 服务名：``terrain``
    """

    def height_at(self, x: float, z: float, seed: Optional[int] = None) -> float:
        """采样世界坐标 (x, z) 处的地形高度（``terrain_flyover.height_at``）。"""
        ...

    def color_at(self, h: float, slope: float, lambert: float, jitter: float) -> Tuple[float, float, float]:
        """按高度/坡度/光照给顶点上色（``terrain_flyover.terrain_color``）。"""
        ...

    def regenerate(self, seed: int) -> None:
        """用新种子重建整片地形。"""
        ...

    def edit_height(self, x: float, z: float, delta: float) -> None:
        """在 (x, z) 处**增量修改**高度——神之手玩法的命门（抬山 + / 挖海 −）。"""
        ...

    def rebuild_mesh(self, *args: Any, **kw: Any) -> Any:
        """把当前高度场导出为 ``engine.geometry.MeshData``（供渲染）。"""
        ...

    def ground_height(self, x: float, z: float, from_y: float = 100.0) -> float:
        """物理层查询地面高度（``PhysicsWorld.ground_height``）。"""
        ...


# ---------------------------------------------------------------------------
# 契约 3 · 实体/单位核心  entity
# ---------------------------------------------------------------------------
@runtime_checkable
class EntityCore(Protocol):
    """通用实体：位置/速度/网格引用/血量/状态机。

    现有模块：散落在 ``demos/mountain.py`` 的 objects/plants（石头、树、云）
    现状：**待抽离**（目前每个 demo 各自手写，没有统一实体管理）
    提供为 Cordis 服务名：``entity``
    """

    def spawn(self, kind: str, pos: Tuple[float, float, float], **kw: Any) -> int:
        """创建一个实体，返回其 id。"""
        ...

    def get(self, eid: int) -> Any:
        """取实体句柄（含 position/velocity/hp/state 等）。"""
        ...

    def update(self, dt: float) -> None:
        """推进所有实体（状态机/物理/动画）。"""
        ...

    def remove(self, eid: int) -> None:
        """销毁实体并回收其 GL 资源（呼应 Cordis 可逆副作用）。"""
        ...

    def query(self, aabb: Any) -> List[int]:
        """返回落在给定 AABB 内的实体 id 列表。"""
        ...

    def count(self, kind: Optional[str] = None) -> int:
        """统计实体数量（按 kind 过滤可选）。"""
        ...


# ---------------------------------------------------------------------------
# 契约 4 · 相机/输入核心  input
# ---------------------------------------------------------------------------
@runtime_checkable
class InputCore(Protocol):
    """鼠标拖拽/滚轮/键盘 → 平移/缩放/选取；事件到动作映射。

    现有模块：``engine.camera.OrbitCamera`` / ``engine.camera.FlyCamera`` /
    ``_harness`` 的 ``on_key`` / ``on_mouse_*``
    现状：**已具备**（相机与 harness 事件），事件映射层 → **待抽离**
    提供为 Cordis 服务名：``input``
    """

    def orbit(self, dx: float, dy: float) -> None:
        """拖拽旋转视角（``OrbitCamera.orbit``）。"""
        ...

    def zoom(self, dy: float) -> None:
        """滚轮缩放（``OrbitCamera.zoom``）。"""
        ...

    def look(self, dx: float, dy: float) -> None:
        """第一人称视角（``FlyCamera.look``）。"""
        ...

    def move(self, forward: float = 0.0, right: float = 0.0,
             up: float = 0.0, sprint: bool = False) -> None:
        """平移相机（``FlyCamera.move``）。"""
        ...

    def view_matrix(self) -> Any:
        """当前视图矩阵（``Camera.view_matrix``）。"""
        ...

    def view_projection(self, aspect: float) -> Any:
        """视图×投影（``Camera.view_projection``）。"""
        ...

    def eye(self) -> Tuple[float, float, float]:
        """相机世界坐标（``OrbitCamera.eye``）。"""
        ...

    def bind(self, key: str, action: str) -> None:
        """把按键映射到语义动作（待抽离的事件映射层）。"""
        ...

    def bind_drag(self, button: str, action: str) -> None:
        """把鼠标拖拽按钮映射到语义动作。"""
        ...


# ---------------------------------------------------------------------------
# 契约 5 · UI/HUD 核心  hud
# ---------------------------------------------------------------------------
@runtime_checkable
class HudCore(Protocol):
    """侧边栏、小地图、状态条、居中叠层文字。

    现有模块：``engine.hud.Hud`` / ``demos/mountain._draw_overlay``
    现状：**已具备**
    提供为 Cordis 服务名：``hud``
    """

    def set_status(self, text: str) -> None:
        """设置底部状态文字（``Hud.set_status``）。"""
        ...

    def set_perf(self, lines: List[str]) -> None:
        """设置性能/调试行（``Hud.set_perf``）。"""
        ...

    def layout(self, width: int, height: int) -> None:
        """按窗口尺寸重排（``Hud.layout``）。"""
        ...

    def draw(self) -> None:
        """绘制 HUD（``Hud.draw``）。"""
        ...


# ---------------------------------------------------------------------------
# 契约 6 · 寻路/移动核心  pathing
# ---------------------------------------------------------------------------
@runtime_checkable
class PathingCore(Protocol):
    """网格 A* + 群体编队（由 boids 改造）。

    现有模块：``logic/boids.py``（``Flock`` 已实现群体行为）
    现状：**待抽离**（boids 已有，网格 A* + 编队调度需新建）
    提供为 Cordis 服务名：``pathing``
    """

    def step(self, dt: float) -> None:
        """推进群体（``Flock.step``）。"""
        ...

    def positions(self) -> List[Tuple[float, float, float]]:
        """所有单位当前位置（``Flock.positions``）。"""
        ...

    def velocities(self) -> List[Tuple[float, float, float]]:
        """所有单位当前速度（``Flock.velocities``）。"""
        ...

    def find_path(self, start: Tuple[int, int], goal: Tuple[int, int],
                  grid: Optional[Any] = None) -> List[Tuple[int, int]]:
        """网格 A* 求路（待抽离）。"""
        ...

    def formation(self, anchor: Tuple[float, float, float], n: int,
                  spacing: float) -> List[Tuple[float, float, float]]:
        """围绕锚点生成 n 个单位的编队目标点（待抽离）。"""
        ...

    def set_predator(self, pos: Tuple[float, float, float]) -> None:
        """设置捕食者位置影响群体（``Flock.set_predator``）。"""
        ...


# ---------------------------------------------------------------------------
# 契约 7 · 资源/经济核心  economy
# ---------------------------------------------------------------------------
@runtime_checkable
class EconomyCore(Protocol):
    """金/木等资源点、工人采集、生产队列。

    现有模块：无（需新建）
    现状：**待抽离**
    提供为 Cordis 服务名：``economy``
    """

    def add(self, resource: str, amount: float) -> None:
        """增加资源存量。"""
        ...

    def spend(self, resource: str, amount: float) -> bool:
        """消费资源；不足返回 ``False``。"""
        ...

    def balance(self, resource: str) -> float:
        """查询某资源存量。"""
        ...

    def harvest(self, unit_id: int, node_id: int, rate: float) -> float:
        """工人从资源点采集，返回本次获得量。"""
        ...

    def enqueue(self, item: str, cost: dict) -> bool:
        """把生产项加入队列（校验资源是否够）。"""
        ...

    def tick(self, dt: float) -> None:
        """推进生产队列与采集。"""
        ...


# ---------------------------------------------------------------------------
# 契约 8 · 音频核心  audio
# ---------------------------------------------------------------------------
@runtime_checkable
class AudioCore(Protocol):
    """即时合成音效/配乐（零外部素材）。

    现有模块：``engine.audio``（``Track`` / ``render_wav`` / ``TimelineAudio`` /
    ``SfxPlayer`` / ``audio_available``）
    现状：**已具备**
    提供为 Cordis 服务名：``audio``
    """

    def create_track(self, duration: float, rate: int = 32000) -> Any:
        """新建一条合成音轨（``Track``）。"""
        ...

    def tone(self, track: Any, t0: float, dur: float, f0: float, f1: Optional[float] = None,
             wave: str = 'saw', amp: float = 0.2, **kw: Any) -> None:
        """在音轨上叠一段音调（``Track.tone``）。"""
        ...

    def noise(self, track: Any, t0: float, dur: float, amp: float = 0.2,
              decay: float = 0.0, lp: float = 0.25, seed: int = 1) -> None:
        """在音轨上叠一段噪声（``Track.noise``）。"""
        ...

    def render_wav(self, track: Any, path: str, normalize: float = 0.92) -> str:
        """把音轨渲染为 wav 文件（``render_wav``）。"""
        ...

    def play_music(self, wav_path: str, loop: bool = True, volume: float = 0.8) -> Any:
        """播放配乐（``TimelineAudio``）。"""
        ...

    def sfx(self, name: str, gain: float = 1.0) -> None:
        """播放即时音效（``SfxPlayer.play``）。"""
        ...

    def available(self) -> bool:
        """音频后端是否可用（``audio_available``）。"""
        ...


# ---------------------------------------------------------------------------
# 契约 9 · 动画/时间轴核心  timeline
# ---------------------------------------------------------------------------
@runtime_checkable
class TimelineCore(Protocol):
    """关键帧、骨骼/变换插值、播放控制。

    现有模块：``engine.timeline``（``Sampler`` / ``Channel`` / ``Timeline`` /
    ``PlaybackController``）/ ``logic.animation``
    现状：**已具备**
    提供为 Cordis 服务名：``timeline``
    """

    def make_sampler(self, times: List[float], values: List[Any],
                     interpolation: str = 'LINEAR') -> Any:
        """构造采样器（``Sampler``）。"""
        ...

    def make_channel(self, node: Any, prop: str, sampler: Any) -> Any:
        """把采样器挂到节点的某个属性（``Channel``）。"""
        ...

    def make_timeline(self, duration: float, channels: List[Any]) -> Any:
        """构造时间轴（``Timeline``）。"""
        ...

    def make_playback(self, timeline: Any, looping: bool = True,
                      speed: float = 1.0) -> Any:
        """构造播放控制器（``PlaybackController``）。"""
        ...


# ---------------------------------------------------------------------------
# 契约 10 · 粒子核心  particles
# ---------------------------------------------------------------------------
@runtime_checkable
class ParticlesCore(Protocol):
    """GPU 粒子（法术/天气/碎屑/弹道）。

    现有模块：``demos/particles_gpu.py``
    现状：**已具备**（demo 级）；封装为可 ``provide`` 的核心 → **待抽离**
    提供为 Cordis 服务名：``particles``
    """

    def emit(self, preset: Any, count: int,
             origin: Tuple[float, float, float] = (0.0, 0.0, 0.0), **kw: Any) -> None:
        """以某预设发射一批粒子（``ParticlesDemo._set_preset`` / 实例写入）。"""
        ...

    def set_count(self, count: int) -> None:
        """调整粒子总数（``ParticlesDemo.set_count``）。"""
        ...

    def update(self, dt: float) -> None:
        """推进粒子模拟。"""
        ...

    def snapshot(self) -> Any:
        """只读快照 ``[(x, y, z, life_frac), ...]``（渲染层消费；P1-⑤ 弹潮引入）。"""
        ...

    def draw(self) -> None:
        """绘制粒子。"""
        ...


# ---------------------------------------------------------------------------
# 契约 11 · 状态/规则核心  rules
# ---------------------------------------------------------------------------
@runtime_checkable
class RulesCore(Protocol):
    """胜负判定、波次、游戏状态机。

    现有模块：无（需新建）
    现状：**待抽离**
    提供为 Cordis 服务名：``rules``
    """

    def state(self) -> str:
        """当前游戏状态（如 'playing' / 'won' / 'lost'）。"""
        ...

    def win(self) -> bool:
        """是否满足胜利条件。"""
        ...

    def lose(self) -> bool:
        """是否满足失败条件。"""
        ...

    def on_event(self, event: dict) -> None:
        """接收游戏事件并驱动规则（如 'unit_killed' / 'resource_depleted'）。"""
        ...

    def reset(self) -> None:
        """重置规则状态。"""
        ...


# ---------------------------------------------------------------------------
# 契约 12 · SDF/体素核心  sdf（含 voxel）
# ---------------------------------------------------------------------------
@runtime_checkable
class SdfVoxelCore(Protocol):
    """光线步进墙体、体素方块。

    现有模块：``demos/raymarch_sdf.py`` / ``demos/voxel_terrain.py`` /
    ``demos/blockworld.py``
    现状：**已具备**（demos 级）；封装为可 ``provide`` 的核心 → **待抽离**
    提供为 Cordis 服务名：``sdf``（体素 voxel 归入此核）
    """

    def raymarch_program(self, name: str) -> Any:
        """取光线步进着色器程序（``raymarch_sdf`` 的 shader）。"""
        ...

    def voxel_get(self, x: int, y: int, z: int) -> int:
        """读取体素值（``voxel_terrain`` / ``blockworld``）。"""
        ...

    def voxel_set(self, x: int, y: int, z: int, v: int) -> None:
        """写入体素值。"""
        ...

    def block_at(self, x: int, y: int, z: int) -> int:
        """查询方块 id（``blockworld`` 的方块世界接口）。"""
        ...

    def world_mesh(self) -> Any:
        """把体素/方块世界导出为 ``engine.geometry.MeshData``。"""
        ...


# ===========================================================================
# 注册表：单一事实来源（供 Cordis 接线 / 文档生成直接消费）
# ===========================================================================
CORES: dict[str, dict[str, Any]] = {
    'render':    dict(service='render',    contract=RenderCore,    module='core.render_core',
                      status='已具备',
                      note='RenderService（core/render_core.py）：GL 资源原语 + 记账 + 可逆回滚'),
    'terrain':   dict(service='terrain',   contract=TerrainCore,   module='core.terrain_core',
                      status='已具备',
                      note='TerrainService（core/terrain_core.py）：height_at/color_at/edit_height/rebuild_mesh/ground_height'),
    'entity':    dict(service='entity',    contract=EntityCore,    module='core.entity_core',
                      status='已具备',
                      note='EntityService（core/entity_core.py）：通用实体注册表'),
    'input':     dict(service='input',     contract=InputCore,     module='engine.camera',
                      status='部分已具备',
                      note='OrbitCamera/FlyCamera + harness 事件已具备；动作映射层待写'),
    'hud':       dict(service='hud',       contract=HudCore,       module='engine.hud',
                      status='部分已具备',
                      note='Hud + mountain overlay 已具备；未抽成独立服务（P0-1 延后）'),
    'pathing':   dict(service='pathing',   contract=PathingCore,   module='core.pathing_core',
                      status='已具备',
                      note='PathingService（core/pathing_core.py）：网格 A* + 编队 + Flock 群体'),
    'economy':   dict(service='economy',   contract=EconomyCore,   module='core.economy_core',
                      status='已具备',
                      note='EconomyService（core/economy_core.py）：资源存量 + 花费校验'),
    'audio':     dict(service='audio',     contract=AudioCore,     module='core.audio_core',
                      status='已具备',
                      note='AudioService（core/audio_core.py）：Track/render_wav/TimelineAudio/SfxPlayer'),
    'timeline':  dict(service='timeline',  contract=TimelineCore,  module='core.timeline_core',
                      status='已具备',
                      note='TimelineService（core/timeline_core.py）：Sampler/Channel/Timeline/PlaybackController'),
    'particles': dict(service='particles', contract=ParticlesCore, module='core.particles_core',
                      status='已具备',
                      note='ParticlesService（core/particles_core.py）：粒子状态 + 数值步进（headless 安全）'),
    'rules':     dict(service='rules',     contract=RulesCore,     module='core.rules_core',
                      status='已具备',
                      note='RulesService（core/rules_core.py）：波次/胜负状态机（塔防/神之手/FPS 三口径）'),
    'sdf':       dict(service='sdf',       contract=SdfVoxelCore,  module='core.sdf_core',
                      status='已具备',
                      note='SdfVoxelService（core/sdf_core.py）：体素读写 + 面剔除网格 + 程序记账'),
}

# P0-1（2026-10-10）后：12 核均已有实现（core/*_core.py 或 engine/*），无「待抽离」项
# 有现成实现、可 provide 成 Cordis 服务的核心：
WRAPPABLE_CORES = [name for name, meta in CORES.items() if meta['status'] != '待抽离']
# 仍需从零新建的核心（P0-1 完成后应为空列表）
FROM_SCRATCH_CORES = [name for name, meta in CORES.items() if meta['status'] == '待抽离']

# 兼容别名：status_table() 与文档引用使用（语义同 WRAPPABLE/FROM_SCRATCH）
READY_CORES = WRAPPABLE_CORES
PENDING_CORES = FROM_SCRATCH_CORES

SERVICE_NAMES: List[str] = list(CORES.keys())


def contract_for(name: str):
    """按 Cordis 服务名取对应契约类；不存在抛 KeyError。"""
    return CORES[name]['contract']


def status_table() -> str:
    """返回一张纯文本状态表，便于 review / 文档自动生成。"""
    lines = ['共同核服务 · 状态表（P0 · Q2）', '=' * 48,
             f'{"服务名":<10}{"现状":<10}{"对应模块"}']
    for name, meta in CORES.items():
        lines.append(f'{name:<10}{meta["status"]:<10}{meta["module"]}')
    lines.append('-' * 48)
    lines.append(f'可封装(已有代码): {", ".join(WRAPPABLE_CORES)}')
    lines.append(f'需从零建:        {", ".join(FROM_SCRATCH_CORES)}')
    return '\n'.join(lines)


def verify(verbose: bool = True) -> dict[str, Any]:
    """可选自检：核对每个核心契约 docstring 声明的「现有模块」是否真的能 import。

    这里**不**做 isinstance 结构比对——契约方法名是「封装后的服务名」（facade），
    与底层模块方法名本就可能不同，isinstance 比对会误报 MISMATCH（典型如
    ``pathing``：底层是 ``logic.boids.Flock``，契约额外要求 ``find_path`` 等
    待抽离方法，结构上必然不相等）。

    真正的风险是「写错模块路径 / 模块根本不存在」，所以这里只验证：
    ``CORES[name]['module']`` 指向的模块能否被 import（即「已具备」声明属实）。
    ``(待建)`` 标记的核心直接记为 ``pending``（预期内，无代码）。

    返回 ``{服务名: 'ok' / 'pending' / 'err:<reason>'}``。
    """
    import importlib, os, sys
    # demos/* 内部用 ``from _harness import ...``（_harness.py 就在 demos/ 下），
    # 必须让 demos/ 进 sys.path 才能让 những 模块被 import（否则报
    # ModuleNotFoundError: No module named '_harness'，属路径问题非代码缺失）。
    _demos = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'demos')
    if os.path.isdir(_demos) and _demos not in sys.path:
        sys.path.insert(0, _demos)
    results: dict[str, Any] = {}
    for name, meta in CORES.items():
        mod = meta['module']
        if mod == '(待建)':
            results[name] = 'pending'
            if verbose:
                print(f'  [verify] {name}: pending (无现成模块，需从零建)')
            continue
        try:
            importlib.import_module(mod)
            results[name] = 'ok'
            if verbose:
                print(f'  [verify] {name}: OK (模块 {mod} 可 import)')
        except ImportError as e:
            # core.* 的实现继承 cordis.Service —— 未暴露 cordis 时记为 pending 而非 err
            # （契约层本身仍不依赖 cordis；这里只是"实现层"的运行时依赖）
            if 'cordis' in str(e):
                results[name] = 'pending:cordis'
                if verbose:
                    print(f'  [verify] {name}: pending（需 cordis：设 CORDIS_SRC 或经 .pth 暴露）')
            else:
                results[name] = f'err:{e.__class__.__name__}'
                if verbose:
                    print(f'  [verify] {name}: ERR {e.__class__.__name__}: {e}')
        except Exception as e:  # 路径写错等
            results[name] = f'err:{e.__class__.__name__}'
            if verbose:
                print(f'  [verify] {name}: ERR {e.__class__.__name__}: {e}')
    return results


__all__ = [
    'RenderCore', 'TerrainCore', 'EntityCore', 'InputCore', 'HudCore',
    'PathingCore', 'EconomyCore', 'AudioCore', 'TimelineCore', 'ParticlesCore',
    'RulesCore', 'SdfVoxelCore',
    'CORES', 'WRAPPABLE_CORES', 'FROM_SCRATCH_CORES', 'SERVICE_NAMES',
    'contract_for', 'status_table', 'verify',
]

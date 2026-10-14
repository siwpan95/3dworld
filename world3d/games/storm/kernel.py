# -*- coding: utf-8 -*-
"""P1-⑤ 弹幕固定屏射击「弹潮 / Swarmward」内核 —— 固定屏全向生存（Cordis 服务化）。

设计 note 见 ``docs/remake_ideas.md`` §12；先问结论（全哥 2026-10-11 定）：

- 玩法：**全向生存**（致敬 ``Robotron 2084``(1982) 品类，固定屏全向刷敌 + 双摇杆）
- 波次：**程序化编波**（``wave_plan(seed, wave_i)`` 难度单调；教学波硬编码）
- 操作：**双摇杆**（WASD 移动 + 方向键/鼠标射击）

与既有内核同一范式，但本次是 **particles 与 pathing 两个共同核的首个内核消费者**：

- ``particles``：击杀碎屑 / 命中火花 / 受击冲击的发射 + 每帧驱动（headless 安全）；
- ``pathing.formation``：敌群编队进场图案（``ring`` / ``grid`` 委托共同核，
  ``line`` / ``v`` 内核自算——不为此扩共同核）。

rules **零改动复用**：玩家命数复用 ``base_hp``（=3）、``player_hit`` → lost、
``wave_cleared``（total_waves=10 → 第 10 波清空即 won）；economy 存 ``score`` 计分。

**auto-pilot = 闪避 AI**：敌弹威胁度采样转向 + 最近敌锁定射击——headless 验收、
demo ``--flag auto`` 同源（§10/§11/§12 一贯理念）。全部逻辑纯 Python、无 GL 依赖。
"""
from __future__ import annotations

import math
import os
import random
import sys

# ---- cordis 定位（与 td/godhand/fps/puzzle 内核一致）----
# P0-3：本文件由 experiments/cordis/ 移到 games/<名>/，层级少一层
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import world3d._cordis_locate as _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Service  # noqa: E402
from world3d.engine.baked_mesh import unit_cube  # noqa: E402

# ===================================================================== 平衡常量
DEFAULT_SEED = 20261011
ARENA_W, ARENA_H = 24.0, 15.0        # 固定屏世界尺寸（x ∈ [-W/2, W/2]，z 同理）
TOTAL_WAVES = 10                      # 通关波数（yml rules.total_waves 同步）
LIVES = 3                             # 玩家命数（yml rules.base_hp 同步）

PLAYER_R = 0.45
PLAYER_SPEED = 7.0
INVULN_T = 2.0                        # 受击无敌帧（v1.1：boss 战多来源连击，1.2->2.0）

BULLET_SPEED, BULLET_R, BULLET_CD = 14.0, 0.14, 0.135   # CD 0.16->0.135（v1.1：
                                                       # 缩短 boss 战时长=减少受击机会）
EBULLET_SPEED, EBULLET_R = 6.0, 0.18

ENEMY_R = 0.5
TURN_RATE = 2.6                       # chaser 转向率（rad/s），防完美追踪
WEAVER_KEEP = (6.0, 9.0)              # weaver 与玩家的保持距离带
WEAVER_FIRE_CD = 1.8                  # weaver 射击冷却
WEAVER_FIRE_RANGE = 10.5              # weaver 开火距离
WEAVER_SPREAD = 0.21                  # 散射半角（rad）

COMBO_WINDOW = 3.0                    # 连击窗口（秒）
WAVE_INTRO_T = 1.6                    # 波次开场提示时长（喘息）
FORM_HOLD_T = 2.0                     # 编队保持时长（进场→散开）

KIND_SCORE = dict(chaser=100, weaver=150, drone=50, boss=2500)
DEMO_SEEDS = (20261011, 7, 13, 42, 101, 202, 777, 2024, 31415, 99999)

# ---- v1.1：boss 波与弹幕图案（§13）----
BOSS_WAVES = (5, 10)                 # 1 基波号：这两波是 boss 波（无尽模式每 5 波）
BOSS_HP_BASE, BOSS_HP_PER_WAVE = 60, 3   # boss5=80 / boss10=100（§13.6 平衡后）
BOSS_R = 1.6
BOSS_MOVE_SPEED = (1.0, 1.2, 1.5)    # 三阶段的移动速度倍率（P3 狂暴加速）
BOSS_FIRE_CD = (2.4, 2.1, 2.0)       # 三阶段的图案发射间隔（§13.6 平衡后放宽）
BOSS_PHASE_CUTS = (0.66, 0.50)       # 血量占比阈值 -> 阶段 2 / 3。
                                     # 第二阈值 0.33->0.50：**缩短狂暴 P3**（P3 是
                                     # 全图案混合，占比过大 auto-pilot 必死；扫描 10 种子得出）
BOSS_DRONE_CALL_CD = 5.0             # P2 召唤 drone 的冷却
BOSS_DRONE_CALL_N = 3                # 每次召唤数量
EBULLET_CAP = 300                    # 弹幕上限：超限拒发 + 优先回收最老弹（§13.4）
BOSS_BULLET_SCALE = 0.46             # boss 弹速倍率（§13.6 对策：boss 弹速慢利于闪避）
BOSS_PATTERN_N = (6, 2)              # 图案弹数 = 6 + 2*阶段（P1=8 / P2=10 / P3=12）
BOSS_KILL_HEAL = 1                   # 击杀 boss 奖励命数（boss 战消耗大，需回血续航）
EDGE_GRAD_ZONE = 4.0                 # auto-pilot 边界梯度区（世界单位；扫描调出来的）
EDGE_GRAD_W = 30.0                   # 梯度权重（每单位）
EDGE_CENTER_PULL = 1.6               # 向心引导系数（弱线性；见 auto_step 注释）
BOSS_INVULN_MULT = 1.8               # boss 战期间无敌帧倍率
AI_HORIZONS = (0.2, 0.4, 0.6, 0.8, 1.0)   # 敌弹预测时刻（v1.1：多时刻取最危险）
AI_BULLET_W = 0.8                    # 敌弹威胁系数（配合 1/horizon）
BOSS_PATTERNS = {                    # 各阶段可用的图案（P3 = 全图案混合）
    1: ('spiral',),
    2: ('flower', 'ring_burst'),
    3: ('spiral', 'flower', 'fan', 'ring_burst', 'sweep'),
}
BOSS_HP_HUD = 2.0                    # 血条高度（世界单位，demo 顶部绘制）


def boss_phase(hp, hp_max):
    """血量占比 → 阶段号（1/2/3），阈值 66% / 33%（纯函数，单测把关）。"""
    f = float(hp) / max(1.0, float(hp_max))
    if f > BOSS_PHASE_CUTS[0]:
        return 1
    if f > BOSS_PHASE_CUTS[1]:
        return 2
    return 3


def boss_hp_for_wave(wave_i):
    """boss 血量：``60 + 10 * 波号``（1 基波号）。"""
    return BOSS_HP_BASE + BOSS_HP_PER_WAVE * int(wave_i)


def is_boss_wave(wave_i):
    """``wave_i`` 为 **1 基**波号；波 5/10 是 boss 波。"""
    return int(wave_i) in BOSS_WAVES


def emit_plan(pattern, t, phase=0.0, count=8, speed=None, aim=0.0):
    """**弹幕图案生成器**（§13.2）：``f(t, 参数) → [(angle, speed), ...]`` 发射计划。

    **纯函数**：同 ``(pattern, t, phase, count, speed, aim)`` 必得同计划——确定性可单测，
    也让 headless auto-pilot 与实机看到完全一样的弹幕。

    五种图案：``spiral`` 螺旋 / ``flower`` 花瓣 / ``fan`` 扇形（朝 ``aim``）/
    ``ring_burst`` 环爆 / ``sweep`` 扫射。未识别图案返回空计划（调用方跳过）。
    """
    sp = EBULLET_SPEED if speed is None else float(speed)
    n = max(0, int(count))
    out = []
    if n == 0:
        return out
    if pattern == 'spiral':
        # 双臂螺旋：角度随 t 连续旋转（两臂差 π）——"转起来的枪口"
        for k in range(n):
            arm = k % 2
            j = k // 2
            a = phase + t * 1.9 + arm * math.pi + j * 0.10
            out.append((a, sp * (0.92 + 0.02 * j)))
    elif pattern == 'flower':
        # 花瓣：n 条主瓣，每瓣内一串小偏角（瓣宽随 t 轻微呼吸）
        petals = 6
        per = max(1, n // petals)
        for k in range(petals):
            a0 = phase + 2.0 * math.pi * k / petals + 0.35 * math.sin(t * 1.1 + k)
            for j in range(per):
                out.append((a0 + (j - (per - 1) / 2.0) * 0.15,
                            sp * (0.88 + 0.05 * j)))
    elif pattern == 'fan':
        # 扇形：朝 aim 张开 ±spread（P3 用得上，考验玩家侧移）
        spread = 0.55
        for k in range(n):
            f = (k / max(1, n - 1)) * 2.0 - 1.0
            out.append((aim + f * spread, sp * (0.95 + 0.10 * (1.0 - abs(f)))))
    elif pattern == 'ring_burst':
        # 环爆：均匀一圈（相位随 t 缓慢自转，避免"死环"）
        for k in range(n):
            out.append((phase + t * 0.35 + 2.0 * math.pi * k / n, sp))
    elif pattern == 'sweep':
        # 扫射：一条正弦摆动的射线（角度整体摆动，弹速递增）
        a = phase + math.sin(t * 0.8) * 0.9
        for j in range(n):
            out.append((a + j * 0.065, sp * (1.0 + 0.05 * j)))
    return out


def _circle_hit(x1, y1, r1, x2, y2, r2):
    """两圆是否重叠（纯函数，碰撞判定与单测共用）。"""
    dx, dy = x1 - x2, y1 - y2
    r = r1 + r2
    return dx * dx + dy * dy <= r * r


def wave_plan(seed, wave_i):
    """程序化编波：``wave_i``（0 基）→ 该波的敌数 / 敌型混比 / 编队图案 / 速度倍率。

    难度**单调递增**（数量、速度、会射击的 weaver 占比随波号单调）——单测把关。
    v1.1：波 5/10（1 基）为 **boss 波**（``is_boss=True``，小兵减量、boss 单独出）。
    """
    w = int(wave_i)
    rng = random.Random(f'{seed}/wave/{w}')
    boss = is_boss_wave(w + 1)                     # wave_i 是 0 基，波号 = w+1
    count = min(6 + 2 * w, 24)
    if boss:
        count = max(3, count // 3)                 # boss 波小兵减到 1/3（boss 是主角）
    f_chaser = max(0.30, 0.55 - 0.03 * w)          # 直冲占比递减
    f_weaver = min(0.35, 0.15 + 0.02 * w)          # 会射击的占比递增
    f_drone = max(0.10, 1.0 - f_chaser - f_weaver)
    speed = 1.0 + 0.07 * w
    pattern = 'line' if w == 0 else rng.choice(('ring', 'grid', 'line', 'v'))
    edge = rng.randrange(4)                        # 进场边：0=+z 1=-z 2=+x 3=-x
    return dict(count=count, f_chaser=f_chaser, f_weaver=f_weaver,
                f_drone=f_drone, speed=speed, pattern=pattern, edge=edge,
                is_boss=boss, boss_hp=boss_hp_for_wave(w + 1) if boss else 0)

# 粒子预设（固定屏：y=0 平面、无重力——把粒子核的 3D 状态压到屏面）
FX_KILL = dict(speed=5.0, life=0.7, gravity=0.0)
FX_HIT = dict(speed=3.0, life=0.35, gravity=0.0)
FX_HURT = dict(speed=6.0, life=0.6, gravity=0.0)


def _layout_points(pattern, anchor, n, spacing, formation_fn=None):
    """编队图案 → n 个 (x, z) 阵位。

    ``ring`` / ``grid`` 委托 ``pathing.formation``（共同核消费）；
    ``line`` / ``v`` 内核自算（共同核没有的图案，不为此扩核）。
    ``formation_fn`` 供单测注入（PathingService.formation 不依赖 self）。
    """
    ax, az = anchor[0], anchor[1]
    if n <= 0:
        return []
    if pattern in ('ring', 'grid') and formation_fn is not None:
        pts = formation_fn((ax, 0.0, az), n, spacing, pattern)
        return [(p[0], p[2]) for p in pts]
    if pattern == 'line':
        step = spacing
        return [(ax + (k - (n - 1) / 2.0) * step, az) for k in range(n)]
    if pattern == 'v':
        out = []
        for k in range(n):
            rank = (k + 1) // 2
            side = 1 if k % 2 == 0 else -1
            out.append((ax + rank * spacing * 1.4 * side,
                        az + rank * spacing * 0.9))
        return out
    # ring/grid 无 formation_fn（不应发生；兜底按 ring 自算）
    r = spacing * n / (2.0 * math.pi) if n > 1 else 0.0
    return [(ax + r * math.cos(2.0 * math.pi * k / n),
             az + r * math.sin(2.0 * math.pi * k / n)) for k in range(n)]


# ===================================================================== 内核
class StormKernel(Service):
    """弹幕固定屏射击内核：声明 inject 只取所需共同核，玩法全在此编排。"""

    inject = ['render', 'rules', 'economy', 'entity', 'particles', 'pathing']

    def __init__(self, ctx, config):
        super().__init__(ctx, "storm")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEFAULT_SEED))
        self.endless = bool(cfg.get('endless', False))
        self.player = dict(x=0.0, z=0.0, cd=0.0, inv=0.0, trail=0.0,
                           mx=0.0, mz=0.0)
        self.bullets = []                 # 玩家弹 {x, z, vx, vz, life}
        self.ebullets = []                # 敌弹
        self.enemies = []                 # {eid, kind, x, z, hp, state, t, ...}
        self.wave = -1                    # 0 基当前波（-1 = 尚未开始）
        self.wave_t = 0.0                 # 波内计时（intro/清波间歇）
        self.wave_spawned = False
        self.combo = 0
        self.combo_t = 0.0
        self.stats = dict(shots=0, hits=0, kills=0, max_combo=0)
        self.boss_max = 0.0               # v1.1：boss 血条上限（0 = 当前无 boss）
        self.boss_killed = 0              # v1.1：本局已击杀的 boss 数
        self.events = []
        self._gl_handles = []
        self.time = 0.0

    # ---------------------------------------------------------- 启动编排
    def __cordis_init__(self):
        rules = self.ctx.rules
        rules.reset()
        self._acquire_gl()
        self._emit('wave')                # 第一波即将开场
        return None

    def _acquire_gl(self):
        render = self.ctx.render
        h_prog = render.compile_program('storm')
        h_cube = render.upload_mesh('storm_cube', *unit_cube())
        h_dot = render.upload_mesh('storm_dot', *unit_cube())
        self._gl_handles.extend([h_prog, h_cube, h_dot])
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    # ---------------------------------------------------------- 事件 --
    def _emit(self, name):
        self.events.append(name)

    def drain_events(self):
        evs = self.events
        self.events = []
        return evs

    # ---------------------------------------------------------- 波次 --
    def spawn_wave(self):
        """按 ``wave_plan`` 生成并入场一波（formation 阵位 + 屏外偏移）。"""
        plan = wave_plan(self.seed, self.wave)
        rng = random.Random(f'{self.seed}/spawn/{self.wave}')
        n = plan['count']
        kinds = (['chaser'] * max(1, round(n * plan['f_chaser']))
                 + ['weaver'] * max(1, round(n * plan['f_weaver']))
                 + ['drone'] * max(1, round(n * plan['f_drone'])))
        kinds = kinds[:n]
        while len(kinds) < n:
            kinds.append('chaser')
        rng.shuffle(kinds)
        spacing = 1.35
        ex, ez = self._edge_anchor(plan['edge'], out=True)
        pts = self._layout_points(plan['pattern'], (ex, ez), n, spacing)
        form_t = FORM_HOLD_T
        for (x, z), kind in zip(pts, kinds):
            eid = self.ctx.entity.spawn(kind, (x, 0.0, z))
            self.enemies.append(dict(
                eid=eid, kind=kind, x=x, z=z,
                hp=2 if kind == 'weaver' else 1,
                state='ENTER', t=0.0, cd=rng.uniform(0.6, 1.6), flash=0.0,
                speed=plan['speed'], phase=rng.uniform(0.0, 6.28),
            ))
        self.wave_spawned = True
        # ---- v1.1：boss 波额外生成一个 boss（它是主角，小兵已减量）----
        if plan['is_boss']:
            # **boss 战前补给**：命数回满。这是"检查点"式的设计选择 —— 实测
            # auto-pilot 能稳定满命通过 wave 1..9，但 boss 战强度高于普通波，
            # 若把前面积累的损耗带进来会必死（10 种子回归验证得出）。
            r = self.ctx.rules
            r.base_hp = int(r.base_hp0)
            bx, bz = self._edge_anchor(plan['edge'], out=True)
            bid = self.ctx.entity.spawn('boss', (bx, 0.0, bz))
            hp = plan['boss_hp']
            self.enemies.append(dict(
                eid=bid, kind='boss', x=bx, z=bz, hp=hp, hp_max=hp,
                state='ENGAGE', t=0.0, cd=BOSS_FIRE_CD[0], flash=0.0,
                speed=plan['speed'], phase=rng.uniform(0.0, 6.28),
                r=BOSS_R, phase_i=1, drone_cd=BOSS_DRONE_CALL_CD,
                fire_i=0))
            self.boss_max = hp
            self._emit('boss')
        return plan

    def _edge_anchor(self, edge, out=False):
        """进场边的中点（``out=True`` 再往外推 3.0，从屏外进场）。"""
        hw, hh = ARENA_W / 2, ARENA_H / 2
        off = (3.5 if out else 1.5)
        if edge == 0:
            return (0.0, hh + off)
        if edge == 1:
            return (0.0, -hh - off)
        if edge == 2:
            return (hw + off, 0.0)
        return (-hw - off, 0.0)

    def _layout_points(self, pattern, anchor, n, spacing):
        """formation 分发：ring/grid 走共同核，line/v 自算（§12.4）。"""
        return _layout_points(pattern, anchor, n, spacing,
                              formation_fn=self.ctx.pathing.formation)

    # ---------------------------------------------------------- 操作 --
    # ---------------------------------------------------------- P1-⑤ 技能加成
    def _skill_cd_mult(self):
        """射击冷却倍率：读 ``skills`` 核的 ``fire_cd_pct`` 被动（0~0.6 夹取）。

        **零漂移保证**：skills 核缺席、或处于中性（未学技能、无装备）时返回 1.0 ——
        接入技能树不改变既有平衡（`config_boot storm --seeds` 仍 10/10）。
        """
        sk = getattr(self.ctx, 'skills', None)
        if sk is None:
            return 1.0
        try:
            if sk.is_neutral():
                return 1.0
            pct = float(sk.effects().get('fire_cd_pct', 0.0))
        except Exception:
            return 1.0
        return 1.0 - max(0.0, min(0.6, pct))

    def move(self, dx, dy, dt):
        """双摇杆的移动端：方向归一化 + 边界夹紧。"""
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return
        p = self.player
        p['mx'], p['mz'] = dx / d, dy / d     # 记录朝向（auto-pilot 连续性用）
        p['x'] = min(ARENA_W / 2 - PLAYER_R, max(-ARENA_W / 2 + PLAYER_R,
                                                 p['x'] + dx / d * PLAYER_SPEED * dt))
        p['z'] = min(ARENA_H / 2 - PLAYER_R, max(-ARENA_H / 2 + PLAYER_R,
                                                 p['z'] + dy / d * PLAYER_SPEED * dt))

    def shoot(self, dx, dy):
        """双摇杆的射击端：朝给定方向发弹（冷却限制）。"""
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return False
        p = self.player
        if p['cd'] > 0.0:
            return False
        d = math.hypot(dx, dy)
        if d < 1e-6:
            return False
        # P1-⑤ 技能树接入（**软引用**：skills 核缺席时跳过；未学任何技能时
        # `is_neutral()` 为真 -> mult 恒为 1.0，**冷却行为与接入前逐字一致**）。
        p['cd'] = BULLET_CD * self._skill_cd_mult()
        self.bullets.append(dict(x=p['x'], z=p['z'],
                                 vx=dx / d * BULLET_SPEED, vz=dy / d * BULLET_SPEED,
                                 life=2.0))
        self.stats['shots'] += 1
        self._emit('shoot')
        return True

    # ---------------------------------------------------------- 每帧 --
    def update(self, dt):
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return
        self.time += dt
        # 粒子核驱动（消费端职责：emit 之外还要让粒子前进）
        self.ctx.particles.update(dt)

        # 波次状态机（wave 为 int 属性——Traceable 下算术前显式解包）
        if not self.wave_spawned:
            self.wave_t = float(self.wave_t) - dt
            if self.wave_t <= 0.0:
                self.wave = int(self.wave) + 1
                self.spawn_wave()
                self._emit('wave')
        elif not self.enemies:
            self.wave_spawned = False
            self.wave_t = WAVE_INTRO_T
            if not self.endless:
                rules.on_event({'type': 'wave_cleared'})

        # 玩家冷却 / 无敌帧 / 连击窗口
        p = self.player
        p['cd'] = max(0.0, p['cd'] - dt)
        p['inv'] = max(0.0, p['inv'] - dt)
        if self.combo:
            self._tick_combo(dt)

        self._update_enemies(dt)
        self._update_bullets(dt)
        self._update_ebullets(dt)
        self._collide(dt)

    # ---------------------------------------------------------- 敌人 --
    def _update_enemies(self, dt):
        p = self.player
        for e in self.enemies:
            e['t'] += dt
            e['flash'] = max(0.0, e['flash'] - dt * 6.0)    # 受击白闪衰减
            mult = e['speed']
            if e['state'] == 'ENTER':
                # 从屏外朝屏内收缩（目标点 = 当前位置 ×0.82，逐帧逼近竞技场内）
                tx, tz = self._edge_target(e)
                dx, dz = tx - e['x'], tz - e['z']
                d = math.hypot(dx, dz) or 1.0
                sp = 4.5 * mult
                e['x'] += dx / d * sp * dt
                e['z'] += dz / d * sp * dt
                if d < 0.25:
                    e['state'] = 'FORM'
                    e['t'] = 0.0
            elif e['state'] == 'FORM':
                if e['t'] >= FORM_HOLD_T and e['kind'] != 'drone':
                    e['state'] = 'ENGAGE'
            elif e['state'] == 'ENGAGE':
                if e['kind'] == 'chaser':
                    self._chase(e, dt, mult)
                elif e['kind'] == 'weaver':
                    self._weave(e, dt, mult)
                elif e['kind'] == 'boss':
                    self._boss(e, dt)
                else:
                    self._orbit(e, dt)
        # ENTER 目标随时间推进：直接用"向屏心方向收"——见 _edge_target

    def _edge_target(self, e):
        """ENTER 阶段的目标点：当前位置向屏心收缩（×0.82）——"压进场"观感。

        不精确复现 spawn 阵位（formation 点只是出生散布），FORM 期敌群在屏内
        自然散开，视觉上等价且省一份阵位状态。
        """
        return (max(-ARENA_W / 2 + 2.0, min(ARENA_W / 2 - 2.0, e['x'] * 0.82)),
                max(-ARENA_H / 2 + 2.0, min(ARENA_H / 2 - 2.0, e['z'] * 0.82)))

    def _chase(self, e, dt, mult):
        """chaser：限转向率追玩家。"""
        p = self.player
        want = math.atan2(p['z'] - e['z'], p['x'] - e['x'])
        cur = math.atan2(e.get('fz', 0.0), e.get('fx', 1.0))
        diff = (want - cur + math.pi * 3) % (2 * math.pi) - math.pi
        cur += max(-TURN_RATE * dt, min(TURN_RATE * dt, diff))
        sp = 2.0 * mult
        e['fx'], e['fz'] = math.cos(cur), math.sin(cur)
        e['x'] += e['fx'] * sp * dt
        e['z'] += e['fz'] * sp * dt

    def _weave(self, e, dt, mult):
        """weaver：保持距离带 + 正弦横移 + 周期散射。"""
        p = self.player
        dx, dz = p['x'] - e['x'], p['z'] - e['z']
        d = math.hypot(dx, dz) or 1.0
        lo, hi = WEAVER_KEEP
        radial = 0.0
        if d > hi:
            radial = 1.0
        elif d < lo:
            radial = -1.0
        nx, nz = dx / d, dz / d
        sway = math.sin(e['t'] * 2.2 + e['phase'])
        px, pz = -nz, nx                          # 垂直方向
        sp = 1.7 * mult
        e['x'] += (nx * radial + px * sway * 0.8) * sp * dt
        e['z'] += (nz * radial + pz * sway * 0.8) * sp * dt
        e['cd'] -= dt
        if e['cd'] <= 0.0 and d < WEAVER_FIRE_RANGE:
            e['cd'] = WEAVER_FIRE_CD / max(0.6, mult)
            base = math.atan2(dz, dx)
            for s in (-WEAVER_SPREAD, 0.0, WEAVER_SPREAD):
                a = base + s
                self.ebullets.append(dict(
                    x=e['x'], z=e['z'],
                    vx=math.cos(a) * EBULLET_SPEED, vz=math.sin(a) * EBULLET_SPEED,
                    life=4.0))
            self._emit('ebullet')

    # ---------------------------------------------------------- boss（v1.1）--
    def _boss(self, e, dt):
        """boss：三阶段（血条 66%/33% 切换）——缓移→加速 + 图案弹幕 + P2 召唤 drone。

        P1（>66%）螺旋 + 缓移；P2（>33%）花瓣/环爆 + 召唤 drone；
        P3（≤33%）全图案混合 + 加速 + 更密的发射间隔。
        """
        p = self.player
        ph = boss_phase(e['hp'], e['hp_max'])
        if ph != e['phase_i']:
            e['phase_i'] = ph
            e['cd'] = BOSS_FIRE_CD[ph - 1]
            self._emit('boss_phase')
        mv = BOSS_MOVE_SPEED[ph - 1]
        # 缓移：朝"玩家与屏心的插值点"漂移 + 正弦侧移；阶段越高越贴近玩家
        k = 0.25 + 0.22 * (ph - 1)
        tx, tz = p['x'] * k, p['z'] * k
        sway = math.sin(float(e['t']) * (0.7 + 0.25 * ph) + e['phase'])
        dx, dz = tx - e['x'], tz - e['z']
        d = math.hypot(dx, dz) or 1.0
        e['x'] += (dx / d * 0.55 - dz / d * sway * 0.45) * mv * dt
        e['z'] += (dz / d * 0.55 + dx / d * sway * 0.45) * mv * dt
        e['x'] = max(-ARENA_W / 2 + BOSS_R, min(ARENA_W / 2 - BOSS_R, e['x']))
        e['z'] = max(-ARENA_H / 2 + BOSS_R, min(ARENA_H / 2 - BOSS_R, e['z']))
        # 图案发射
        e['cd'] -= dt
        if e['cd'] <= 0.0:
            e['cd'] = BOSS_FIRE_CD[ph - 1]
            pats = BOSS_PATTERNS[ph]
            pat = pats[int(e['fire_i']) % len(pats)]
            e['fire_i'] = int(e['fire_i']) + 1
            aim = math.atan2(p['z'] - e['z'], p['x'] - e['x'])
            self._boss_fire(e, pat, aim, BOSS_PATTERN_N[0] + BOSS_PATTERN_N[1] * ph)
            self._emit('boss_fire')
        # P2 周期召唤 drone
        if ph == 2:
            e['drone_cd'] -= dt
            if e['drone_cd'] <= 0.0:
                e['drone_cd'] = BOSS_DRONE_CALL_CD
                for k2 in range(BOSS_DRONE_CALL_N):
                    a = 2.0 * math.pi * k2 / BOSS_DRONE_CALL_N + float(e['t'])
                    sx = e['x'] + math.cos(a) * (BOSS_R + 1.3)
                    sz = e['z'] + math.sin(a) * (BOSS_R + 1.3)
                    did = self.ctx.entity.spawn('drone', (sx, 0.0, sz))
                    self.enemies.append(dict(
                        eid=did, kind='drone', x=sx, z=sz, hp=1,
                        state='ENGAGE', t=0.0, cd=1.0, flash=0.0,
                        speed=e['speed'], phase=a, r=ENEMY_R))
                self._emit('boss_call')

    def _boss_fire(self, e, pattern, aim, count):
        """按图案计划发弹（受 ``EBULLET_CAP`` 限制：超限优先回收最老弹，保图案完整）。"""
        sp_scale = EBULLET_SPEED * BOSS_BULLET_SCALE      # boss 弹更慢（可闪避，§13.6）
        for ang, sp in emit_plan(pattern, float(e['t']), phase=e['phase'],
                                 count=count, speed=sp_scale, aim=aim):
            if len(self.ebullets) >= EBULLET_CAP:
                self.ebullets.pop(0)
                self.stats['ebullet_dropped'] = self.stats.get('ebullet_dropped', 0) + 1
            self.ebullets.append(dict(
                x=e['x'], z=e['z'],
                vx=math.cos(ang) * sp, vz=math.sin(ang) * sp, life=9.0))

    def _orbit(self, e, dt):
        """drone：绕自身阵心缓慢环绕（蜂群感，不主动攻击）。"""
        ang = e['phase'] + e['t'] * 0.5
        r = 0.6
        e['x'] += math.cos(ang) * r * dt
        e['z'] += math.sin(ang) * r * dt

    # ---------------------------------------------------------- 弹与碰撞 --
    def _update_bullets(self, dt):
        hw, hh = ARENA_W / 2, ARENA_H / 2
        for b in list(self.bullets):
            b['x'] += b['vx'] * dt
            b['z'] += b['vz'] * dt
            b['life'] -= dt
            if b['life'] <= 0.0 or not (-hw - 1 <= b['x'] <= hw + 1
                                        and -hh - 1 <= b['z'] <= hh + 1):
                self.bullets.remove(b)

    def _update_ebullets(self, dt):
        hw, hh = ARENA_W / 2, ARENA_H / 2
        for b in list(self.ebullets):
            b['x'] += b['vx'] * dt
            b['z'] += b['vz'] * dt
            b['life'] -= dt
            if b['life'] <= 0.0 or not (-hw - 1 <= b['x'] <= hw + 1
                                        and -hh - 1 <= b['z'] <= hh + 1):
                self.ebullets.remove(b)

    def _collide(self, dt):
        p = self.player
        parts = self.ctx.particles
        # 玩家弹 × 敌
        for b in list(self.bullets):
            for e in self.enemies:
                if _circle_hit(b['x'], b['z'], BULLET_R, e['x'], e['z'],
                               e.get('r', ENEMY_R)):
                    self.bullets.remove(b)
                    e['hp'] -= 1
                    e['flash'] = 1.0
                    self.stats['hits'] += 1
                    parts.emit(FX_HIT, 5, origin=(e['x'], 0.0, e['z']))
                    self._emit('hit')
                    if e['hp'] <= 0:
                        self._kill_enemy(e)
                    break
        if p['inv'] > 0.0 or self.ctx.rules.state() in ('won', 'lost'):
            return
        # 敌弹 × 玩家
        for b in list(self.ebullets):
            if _circle_hit(b['x'], b['z'], EBULLET_R, p['x'], p['z'], PLAYER_R):
                self.ebullets.remove(b)
                self._hurt_player()
                return
        # 敌体 × 玩家（撞击）
        for e in list(self.enemies):
            if _circle_hit(e['x'], e['z'], e.get('r', ENEMY_R), p['x'], p['z'],
                           PLAYER_R):
                if e['kind'] == 'boss':
                    # boss **不因撞击消失**（它是 boss）；只伤玩家，由 _hurt_player
                    # 给的无敌帧避免"贴着 boss 连续扣命"（§13 平衡）
                    self._hurt_player()
                    return
                self._remove_enemy(e)
                parts.emit(FX_KILL, 10, origin=(e['x'], 0.0, e['z']))
                self._hurt_player()
                return

    def _kill_enemy(self, e):
        parts = self.ctx.particles
        self._remove_enemy(e)
        # 注意：Traceable 代理下 int 属性读出为 ShadowView，算术前必须显式 int()
        self.combo = self._bump_combo(int(self.combo), float(self.combo_t), self.time)
        self.combo_t = self.time
        self.stats['kills'] += 1
        self.stats['max_combo'] = max(self.stats['max_combo'], int(self.combo))
        mult = 1.0 + min(self.combo, 10) * 0.1        # 连击倍率 cap ×2
        self.ctx.economy.add('score', KIND_SCORE.get(e['kind'], 50) * mult)
        n_fx = 40 if e['kind'] == 'boss' else 14          # boss 死亡大爆炸（§13.3）
        parts.emit(FX_KILL, n_fx, origin=(e['x'], 0.0, e['z']))
        if e['kind'] == 'boss':
            self.boss_max = 0.0                           # 血条归零
            self.boss_killed = int(self.boss_killed) + 1
            # boss 战消耗大：击杀**奖励命数**（不超上限）——让两个 boss 波之间有续航
            r = self.ctx.rules
            r.base_hp = min(int(r.base_hp0), int(r.base_hp) + BOSS_KILL_HEAL)
        self._emit('kill')

    def _remove_enemy(self, e):
        if e in self.enemies:
            self.enemies.remove(e)
            self.ctx.entity.remove(e['eid'])

    def _hurt_player(self):
        p = self.player
        # boss 战期间无敌帧加长：boss 的三阶段弹幕是**多来源同时压上**，
        # 普通长度会被连击（10 种子回归里最难的两个种子就死在这）。
        boss_alive = any(e['kind'] == 'boss' for e in self.enemies)
        p['inv'] = INVULN_T * (BOSS_INVULN_MULT if boss_alive else 1.0)
        self.combo = 0                                  # 受击断连击
        self.ctx.rules.on_event({'type': 'player_hit', 'dmg': 1})
        self.ctx.particles.emit(FX_HURT, 20,
                                origin=(p['x'], 0.0, p['z']))
        self._emit('hurt')
        if self.ctx.rules.state() == 'lost':
            self._emit('end')

    # ---------------------------------------------------------- 连击 --
    def _bump_combo(self, combo, combo_t, now, window=COMBO_WINDOW):
        """连击累积（窗口内 +1，超窗重计 1）。

        ⚠️ 必须是**实例方法**——Cordis Traceable 对 staticmethod 也注入 self
        （第 5 坑，§10.9），形参会整体错位成 ShadowView。模块级纯函数也安全。
        """
        return int(combo) + 1 if now - float(combo_t) <= window else 1

    def _tick_combo(self, dt):
        if self.time - self.combo_t > COMBO_WINDOW:
            self.combo = 0

    # ---------------------------------------------------------- 重开 --
    def restart(self, seed=None):
        """换种子重开（R 键 / --flag seedN）：全清 + rules 复位，GL 句柄不动。"""
        if seed is not None:
            self.seed = int(seed)
        rules = self.ctx.rules
        rules.reset()
        for e in list(self.enemies):
            self.ctx.entity.remove(e['eid'])
        self.enemies = []
        self.bullets = []
        self.ebullets = []
        ec = self.ctx.economy
        ec.add('score', -ec.balance('score'))
        self.combo = 0
        self.combo_t = 0.0
        self.stats = dict(shots=0, hits=0, kills=0, max_combo=0)
        self.boss_max = 0.0
        self.boss_killed = 0
        self.wave = -1
        self.wave_spawned = False
        self.wave_t = 0.4
        p = self.player
        p['x'] = p['z'] = 0.0
        p['cd'] = p['inv'] = 0.0
        self._emit('wave')
        return self.seed

    # --------------------------------------------- auto-pilot（录制/验收共用）
    def auto_step(self, dt):
        """自动驾驶 = 闪避 AI：敌弹威胁度采样转向 + 最近敌锁定射击。

        demo ``--flag auto`` 与 config_boot 的 headless 验收走**同一套**逻辑。
        """
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return
        p = self.player
        # 1) 射击：**boss 优先**（血厚，必须持续输出——实测"只打最近敌"会让一半火力
        #    被小兵吸走，boss 掉血速度不够）；无 boss 时打最近敌。
        boss = next((e for e in self.enemies if e['kind'] == 'boss'), None)
        best, best_d = boss, 1e9
        if boss is None:
            for e in self.enemies:
                d = math.hypot(e['x'] - p['x'], e['z'] - p['z'])
                if d < best_d:
                    best, best_d = e, d
        if best is not None:
            self.shoot(best['x'] - p['x'], best['z'] - p['z'])
        # 2) 移动：24 方向采样，选"威胁度"最低者。
        #    v1.1：敌弹按 **5 个时刻**（0.2/0.4/0.6/0.8/1.0s）预测取最危险——
        #    boss 的多方向图案单时刻采样会漏（§13.6「威胁度采样加密」）。
        best_dir, best_score = (0.0, 0.0), None
        for k in range(24):
            a = math.pi * 2 * k / 24
            dx, dz = math.cos(a), math.sin(a)
            nx = p['x'] + dx * PLAYER_SPEED * dt * 8
            nz = p['z'] + dz * PLAYER_SPEED * dt * 8
            score = 0.0
            # 边界：**梯度**惩罚（v1.1）。原先只有"越界 +50"的硬阈值 —— boss 弹幕
            # 把玩家逼到角落时，所有方向都被覆盖，auto-pilot 会选"最不坏"的角落
            # 然后**卡死**（实测：wave10 死在 (11.3,-6.8) = 右下角）。梯度让"远离
            # 边界"始终有正收益，能主动脱身。
            margin = min(ARENA_W / 2 - abs(nx), ARENA_H / 2 - abs(nz))
            if margin < PLAYER_R:
                score += 200.0
            elif margin < EDGE_GRAD_ZONE:
                score += (EDGE_GRAD_ZONE - margin) * EDGE_GRAD_W
            # **弱线性向心引导**：margin 越大越安全。系数要小 ——
            #   · 不引导 -> AI 停在 margin≈4.2 的边缘带（那里弹幕稀疏），被 boss 的
            #     环爆/扫射沿边打死；
            #   · 强区间惩罚(zone=6) -> 把 AI 推回屏心，而 boss 也在屏心 -> 被围死。
            #   弱线性给"持续轻微向心"的倾向，兼得两者。
            score -= margin * EDGE_CENTER_PULL
            for b in self.ebullets:
                worst = 0.0
                for horizon in AI_HORIZONS:
                    bx = b['x'] + b['vx'] * horizon
                    bz = b['z'] + b['vz'] * horizon
                    dd = math.hypot(nx - bx, nz - bz)
                    if dd < 4.0:
                        # 越早命中越危险；系数调出来的（过大 -> AI 过度避弹躲角落）
                        worst = max(worst, (4.0 - dd) * (AI_BULLET_W / max(0.20, horizon)))
                score += worst
            for e in self.enemies:
                dd = math.hypot(nx - e['x'], nz - e['z'])
                w = 2.2 if e['kind'] == 'chaser' else 1.0
                score += max(0.0, 3.0 - dd) * w
            # 方向连续性：与当前移动方向同向微奖励（抑制帧间抖动导致的横跳）
            score -= 1.5 * (dx * p['mx'] + dz * p['mz'])
            if best_score is None or score < best_score:
                best_score, best_dir = score, (dx, dz)
        self.move(best_dir[0], best_dir[1], dt)

    # ---------------------------------------------------------- 展示 --
    def status_text(self):
        rules = self.ctx.rules
        ec = self.ctx.economy
        boss = ''
        if float(self.boss_max) > 0.0:
            b = next((e for e in self.enemies if e['kind'] == 'boss'), None)
            if b is not None:
                boss = f" BOSS[{b['hp']}/{b['hp_max']} P{boss_phase(b['hp'], b['hp_max'])}]"
        return (f"state={rules.state()} wave={max(self.wave + 1, 0)}/{TOTAL_WAVES} "
                f"lives={rules.base_hp}/{rules.base_hp0} score={ec.balance('score'):.0f} "
                f"combo={self.combo} enemies={len(self.enemies)}{boss} seed={self.seed} "
                f"t={self.time:.1f}s")

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
import random

from cordis import Service  # noqa: E402
from experiments.cordis.tower_defense_kernel import _unit_cube  # noqa: E402

# ===================================================================== 平衡常量
DEFAULT_SEED = 20261011
ARENA_W, ARENA_H = 24.0, 15.0        # 固定屏世界尺寸（x ∈ [-W/2, W/2]，z 同理）
TOTAL_WAVES = 10                      # 通关波数（yml rules.total_waves 同步）
LIVES = 3                             # 玩家命数（yml rules.base_hp 同步）

PLAYER_R = 0.45
PLAYER_SPEED = 7.0
INVULN_T = 1.2                        # 受击无敌帧

BULLET_SPEED, BULLET_R, BULLET_CD = 14.0, 0.14, 0.16
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

KIND_SCORE = dict(chaser=100, weaver=150, drone=50)
DEMO_SEEDS = (20261011, 7, 13, 42, 101, 202, 777, 2024, 31415, 99999)

# 粒子预设（固定屏：y=0 平面、无重力——把粒子核的 3D 状态压到屏面）
FX_KILL = dict(speed=5.0, life=0.7, gravity=0.0)
FX_HIT = dict(speed=3.0, life=0.35, gravity=0.0)
FX_HURT = dict(speed=6.0, life=0.6, gravity=0.0)


def _circle_hit(x1, y1, r1, x2, y2, r2):
    """两圆是否重叠（纯函数，碰撞判定与单测共用）。"""
    dx, dy = x1 - x2, y1 - y2
    r = r1 + r2
    return dx * dx + dy * dy <= r * r


def wave_plan(seed, wave_i):
    """程序化编波：``wave_i``（0 基）→ 该波的敌数 / 敌型混比 / 编队图案 / 速度倍率。

    难度**单调递增**（数量、速度、会射击的 weaver 占比随波号单调）——单测把关。
    """
    w = int(wave_i)
    rng = random.Random(f'{seed}/wave/{w}')
    count = min(6 + 2 * w, 24)
    f_chaser = max(0.30, 0.55 - 0.03 * w)          # 直冲占比递减
    f_weaver = min(0.35, 0.15 + 0.02 * w)          # 会射击的占比递增
    f_drone = max(0.10, 1.0 - f_chaser - f_weaver)
    speed = 1.0 + 0.07 * w
    pattern = 'line' if w == 0 else rng.choice(('ring', 'grid', 'line', 'v'))
    edge = rng.randrange(4)                        # 进场边：0=+z 1=-z 2=+x 3=-x
    return dict(count=count, f_chaser=f_chaser, f_weaver=f_weaver,
                f_drone=f_drone, speed=speed, pattern=pattern, edge=edge)


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
        h_cube = render.upload_mesh('storm_cube', *_unit_cube())
        h_dot = render.upload_mesh('storm_dot', *_unit_cube())
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
        p['cd'] = BULLET_CD
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
                if _circle_hit(b['x'], b['z'], BULLET_R, e['x'], e['z'], ENEMY_R):
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
        # 敌体 × 玩家（撞击自爆）
        for e in list(self.enemies):
            if _circle_hit(e['x'], e['z'], ENEMY_R, p['x'], p['z'], PLAYER_R):
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
        parts.emit(FX_KILL, 14, origin=(e['x'], 0.0, e['z']))
        self._emit('kill')

    def _remove_enemy(self, e):
        if e in self.enemies:
            self.enemies.remove(e)
            self.ctx.entity.remove(e['eid'])

    def _hurt_player(self):
        p = self.player
        p['inv'] = INVULN_T
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
        # 1) 射击：最近敌（ENTER/FORM/ENGAGE 都打）
        best, best_d = None, 1e9
        for e in self.enemies:
            d = math.hypot(e['x'] - p['x'], e['z'] - p['z'])
            if d < best_d:
                best, best_d = e, d
        if best is not None:
            self.shoot(best['x'] - p['x'], best['z'] - p['z'])
        # 2) 移动：16 方向采样，选"威胁度"最低者（敌弹 0.5s 预测位 + 敌体 + 边界）
        best_dir, best_score = (0.0, 0.0), None
        for k in range(16):
            a = math.pi * 2 * k / 16
            dx, dz = math.cos(a), math.sin(a)
            nx = p['x'] + dx * PLAYER_SPEED * dt * 8
            nz = p['z'] + dz * PLAYER_SPEED * dt * 8
            score = 0.0
            if not (-ARENA_W / 2 + PLAYER_R < nx < ARENA_W / 2 - PLAYER_R
                    and -ARENA_H / 2 + PLAYER_R < nz < ARENA_H / 2 - PLAYER_R):
                score += 50.0
            for b in self.ebullets:
                bx, bz = b['x'] + b['vx'] * 0.5, b['z'] + b['vz'] * 0.5
                dd = math.hypot(nx - bx, nz - bz)
                score += max(0.0, 4.0 - dd) * 2.0
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
        return (f"state={rules.state()} wave={max(self.wave + 1, 0)}/{TOTAL_WAVES} "
                f"lives={rules.base_hp}/{rules.base_hp0} score={ec.balance('score'):.0f} "
                f"combo={self.combo} enemies={len(self.enemies)} seed={self.seed} "
                f"t={self.time:.1f}s")

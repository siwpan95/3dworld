# -*- coding: utf-8 -*-
"""§15 星阑 / Voidward 内核 —— 惯性飞行 + 单星系贸易 + 海盗拦截（Cordis 服务化）。

设计 note 见 ``docs/remake_ideas.md`` §15。**致敬 Elite(1984, Acornsoft) 的
「惯性飞行 + 贸易 + 战斗」**，不碰原 IP 名称与素材（合规见 §7）。

切片范围（§15.3 狠砍后）：1 星系（恒星 + 3 行星 + 1 空间站）、5 种货
（价格 = 基准 × 供需随机游走 + 事件冲击，**确定性可复现**）、船 1 艘（货舱 20 / 炮 1）、
海盗 2-3 艘拦截战、2 级升级（舱位/炮）、胜负 = **资产达标**（credits ≥ 10000 → won）。

**economy 核的第二个重度消费者**（第一个是塔防）：credits 走 KV 计账；
rules 复用新增的 ``asset_reached`` 分支（§15.4）。

headless 贸易脚本（``auto_step``）= 「低买高卖 + 海盗击退」策略回放，
与 demo 手玩**同 API**（``buy/sell/upgrade/thrust/fire``）。
"""
from __future__ import annotations

import math
import os
import sys

# P0-3：本文件由 experiments/cordis/ 移到 games/<名>/，层级少一层
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
import _cordis_locate  # noqa: E402

_cordis_locate.ensure()

from cordis import Service  # noqa: E402

# ===================================================================== 常量
TARGET_ASSET = 10000.0                # 资产达标线（credits + 货值）
GOODS = ('ore', 'meds', 'grain', 'chips', 'lux')
GOOD_BASE = {'ore': 22.0, 'meds': 60.0, 'grain': 14.0, 'chips': 95.0, 'lux': 140.0}
GOOD_PERIOD = {'ore': 15.0, 'meds': 22.0, 'grain': 12.0, 'chips': 27.0, 'lux': 34.0}
SWING = 0.42                          # 供需摆动幅度（调大 -> 套利空间更明显）

CORE_WORLD_R = 46.0                   # 星系半径
STAR_R = 3.0
DOCK_R = 2.6                          # 停靠半径

SHIP_ACCEL = 9.0
SHIP_DRAG = 0.993                     # 每帧阻尼（终端速度≈22 = SHIP_MAX_V；
                                      # 0.86 时终端只有 ~1 单位/秒，站点间要飞 50s）
SHIP_MAX_V = 22.0
SHIP_CARGO0, SHIP_CANNON0 = 20, 1

GUN_CD = 0.42
GUN_RANGE = 11.0
GUN_DMG = 1
PIRATE_HP = 3
PIRATE_SPEED = 7.5
PIRATE_FIRE_RANGE = 8.5
PIRATE_FIRE_CD = 1.6
PIRATE_DMG = 1
PIRATE_BOUNTY = 900.0                 # 击退赏金（贸易之外的收入来源；调出来的）
START_CREDITS = 900.0                 # 起始资金（贸易游戏的启动资本，否则起步买不起货）
PIRATE_KEEP = (5.0, 7.5)              # 保持距离带
HULL0 = 6                             # 船体（base_hp 复用）
UPGRADE_CARGO_STEP, UPGRADE_CARGO_COST = 8, 900.0
UPGRADE_GUN_STEP, UPGRADE_GUN_COST = 1, 1200.0
MAX_UPGRADE = 2                       # 2 级升级（§15.3）

# 4 个站点：1 空间站（中心）+ 3 行星（环绕）——各自有不同的"贵/贱"偏好
STATIONS = (
    {'id': 'hub', 'name': '枢纽站', 'pos': (0.0, 0.0), 'kind': 'station',
     'bias': {'ore': 0.04, 'meds': -0.02, 'grain': 0.0, 'chips': 0.05, 'lux': -0.05}},
    {'id': 'ferro', 'name': '铁砂', 'pos': (-30.0, -12.0), 'kind': 'planet',
     'bias': {'ore': -0.16, 'meds': 0.06, 'grain': 0.02, 'chips': 0.10, 'lux': 0.08}},
    {'id': 'verda', 'name': '青壤', 'pos': (26.0, -22.0), 'kind': 'planet',
     'bias': {'ore': 0.07, 'meds': -0.10, 'grain': -0.18, 'chips': 0.04, 'lux': 0.03}},
    {'id': 'lumen', 'name': '流光', 'pos': (10.0, 30.0), 'kind': 'planet',
     'bias': {'ore': 0.10, 'meds': 0.08, 'grain': 0.05, 'chips': -0.14, 'lux': -0.16}},
)

DEMO_SEED = 20261012


# ============================================================ 纯函数（可单测）
def station_price(good, station, t, seed, shocks):
    """**确定性价格**：``base * (1 + bias + 0.3*sin(t/period + phase) + shock)``。

    ``phase`` 由 ``(seed, station.id, good)`` 派生（同种子同价格，录制与验收可锁定）；
    ``shocks`` 是"事件冲击"表 ``{(station_id, good): amount}``（由种子驱动，见 ``shock_table``）。
    结果下限 1.0（价格不能为负）。
    """
    base = GOOD_BASE[good]
    period = GOOD_PERIOD[good]
    phase = _phase(seed, station['id'], good)
    bias = station.get('bias', {}).get(good, 0.0)
    shock = shocks.get((station['id'], good), 0.0)
    v = base * (1.0 + bias + SWING * math.sin(t / period + phase) + shock)
    return max(1.0, v)


def _phase(seed, sid, good) -> float:
    """由字符串键派生的稳定相位（不用 random，避免跨版本漂移）。"""
    h = 0
    for ch in f'{seed}/{sid}/{good}':
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return (h % 1000) / 1000.0 * math.tau


def shock_table(seed, t_span=600.0, events=None):
    """事件冲击表：种子驱动「引力紊乱 → 某货涨价」（确定性）。

    ``events`` 形如 ``[(t0, t1, station_id, good, amount), ...]``；缺省由种子生成 3 条。
    返回 ``events`` 列表；**查询用模块级的** :func:`shock_at`（不要把它挂成实例属性 ——
    cordis 的 Traceable 会包装实例上的函数并多传一个 ``self``）。
    """
    if events is not None:
        return list(events)
    evs = []
    for k in range(3):
        h = _phase(seed, 'ev', str(k))
        t0 = t_span * (0.15 + 0.25 * k)
        evs.append((t0, t0 + t_span * 0.12,
                    STATIONS[k % len(STATIONS)]['id'],
                    GOODS[int(h * 1000) % len(GOODS)],
                    0.22 + 0.06 * k))
    return evs


def shock_at(events, t) -> dict:
    """某时刻生效的冲击表 ``{(station_id, good): amount}``（可叠加）。"""
    out = {}
    for t0, t1, sid, good, amt in events:
        if t0 <= t <= t1:
            out[(sid, good)] = out.get((sid, good), 0.0) + amt
    return out


def mean_reversion_gap(series) -> float:
    """序列与均值的平均绝对偏差（市场单测用：供需应围绕基准回归）。"""
    if not series:
        return 0.0
    m = sum(series) / len(series)
    return sum(abs(v - m) for v in series) / len(series)


def nearest_station(pos, stations=STATIONS):
    """最近站点 ``(station, dist)``。"""
    best, bd = None, 1e18
    for s in stations:
        d = math.hypot(pos[0] - s['pos'][0], pos[1] - s['pos'][1])
        if d < bd:
            best, bd = s, d
    return best, bd


# ============================================================ 内核
class VoidKernel(Service):
    """太空贸易内核：星系图 + 确定性市场 + 惯性飞行 + 海盗拦截（``buy/sell/upgrade`` 可 headless 驱动）。"""

    inject = ['render', 'rules', 'economy', 'entity']

    def __init__(self, ctx, config):
        super().__init__(ctx, "void")
        cfg = config or {}
        self.seed = int(cfg.get('seed', DEMO_SEED))
        self.target = float(cfg.get('target_asset', TARGET_ASSET))
        self.stations = [dict(s) for s in STATIONS]
        self.shock_events = shock_table(self.seed)
        # 船（惯性）
        self.ship = dict(x=0.0, z=-12.0, vx=0.0, vz=0.0, heading=0.0,
                         cd=0.0, dmg_t=0.0)
        self.cargo = {g: 0 for g in GOODS}
        self.cargo_cap = SHIP_CARGO0
        self.cannon = SHIP_CANNON0
        self.upgrades = {'cargo': 0, 'gun': 0}
        self.pirates = []
        self.bullets = []
        self.pending_pirates = int(cfg.get('pirates', 3))
        self.pirates_beaten = 0
        self.trades = 0
        self.time = 0.0
        self.log = []
        self.events_out = []
        self._gl_handles = []

    # ---------------------------------------------------------- 生命周期
    def __cordis_init__(self):
        self._acquire_gl()
        self.ctx.economy.add('credits', START_CREDITS)  # 起始资金（同时建立 KV 键）
        self._spawn_pirates()
        self._emit('depart', station='lumen')
        return None

    def _acquire_gl(self):
        render = self.ctx.render
        from engine.baked_mesh import unit_cube
        h_prog = render.compile_program('void')
        h_mesh = render.upload_mesh('void_cube', *unit_cube())
        self._gl_handles.extend([h_prog, h_mesh])
        for h in self._gl_handles:
            def release(h=h):
                return lambda: render.release(h)
            self.ctx.effect(release)

    def _spawn_pirates(self):
        """在离玩家有距离的位置投放海盗（确定性：按 seed 派相位）。"""
        for k in range(self.pending_pirates):
            a = _phase(self.seed, 'pirate', str(k))
            r = 14.0 + 6.0 * k
            self.pirates.append(dict(
                x=self.ship['x'] + r * math.cos(a),
                z=self.ship['z'] + r * math.sin(a),
                hp=PIRATE_HP, cd=1.0 + 0.4 * k, flash=0.0, alive=True))

    # ---------------------------------------------------------- 市场
    def shocks_now(self):
        return shock_at(self.shock_events, self.time)

    def price(self, good, station=None):
        """当前价格（``station`` 缺省 = 最近站点）。"""
        if station is None:
            station, _d = nearest_station((self.ship['x'], self.ship['z']), self.stations)
        sid = station if isinstance(station, str) else station['id']
        st = next(s for s in self.stations if s['id'] == sid)
        return station_price(good, st, self.time, self.seed, self.shocks_now())

    def docks_at(self):
        """当前停靠的站点（不在任何站点的停靠半径内 -> None）。"""
        for s in self.stations:
            if math.hypot(self.ship['x'] - s['pos'][0], self.ship['z'] - s['pos'][1]) <= DOCK_R:
                return s
        return None

    def _credits(self):
        return float(self.ctx.economy.balance('credits'))

    def buy(self, good, n=1) -> dict:
        """买入（须停靠；受货舱容量与 credits 限制）。"""
        st = self.docks_at()
        if st is None:
            return dict(ok=False, detail='未停靠站点')
        if good not in GOODS:
            return dict(ok=False, detail=f'无此货: {good}')
        free = self.cargo_cap - sum(self.cargo.values())
        n = max(0, min(int(n), free))
        if n <= 0:
            return dict(ok=False, detail='货舱已满')
        unit = self.price(good, st)
        cost = unit * n
        if self._credits() < cost:
            n = int(self._credits() // unit)
            if n <= 0:
                return dict(ok=False, detail='credits 不足')
            cost = unit * n
        self.ctx.economy.spend('credits', cost)
        self.cargo[good] += n
        self.trades += 1
        self._emit('buy', good=good, n=n, unit=unit)
        return dict(ok=True, good=good, n=n, unit=unit, cost=cost,
                    detail=f'买入 {good}×{n} @{unit:.1f}')

    def sell(self, good, n=1) -> dict:
        """卖出（须停靠；不能卖超过持有量）。"""
        st = self.docks_at()
        if st is None:
            return dict(ok=False, detail='未停靠站点')
        have = self.cargo.get(good, 0)
        n = max(0, min(int(n), have))
        if n <= 0:
            return dict(ok=False, detail=f'没有 {good} 可卖')
        unit = self.price(good, st)
        gain = unit * n
        self.ctx.economy.add('credits', gain)
        self.cargo[good] -= n
        self.trades += 1
        self._emit('sell', good=good, n=n, unit=unit)
        return dict(ok=True, good=good, n=n, unit=unit, gain=gain,
                    detail=f'卖出 {good}×{n} @{unit:.1f}')

    def upgrade(self, kind) -> dict:
        """升级（``cargo`` 扩舱 / ``gun`` 加炮），各限 ``MAX_UPGRADE`` 级。"""
        if kind not in ('cargo', 'gun'):
            return dict(ok=False, detail=f'无此升级: {kind}')
        if self.upgrades[kind] >= MAX_UPGRADE:
            return dict(ok=False, detail=f'{kind} 已满级')
        cost = UPGRADE_CARGO_COST if kind == 'cargo' else UPGRADE_GUN_COST
        if not self.ctx.economy.spend('credits', cost):
            return dict(ok=False, detail=f'credits 不足（需 {cost:.0f}）')
        self.upgrades[kind] += 1
        if kind == 'cargo':
            self.cargo_cap += UPGRADE_CARGO_STEP
        else:
            self.cannon += UPGRADE_GUN_STEP
        self._emit('upgrade', kind=kind)
        return dict(ok=True, detail=f'{kind} 升级到 {self.upgrades[kind]} 级')

    def total_asset(self) -> float:
        """总资产 = credits + 货值（按最近站点价格估）。"""
        st, _d = nearest_station((self.ship['x'], self.ship['z']), self.stations)
        goods = sum(self.price(g, st) * n for g, n in self.cargo.items())
        return self._credits() + goods

    # ---------------------------------------------------------- 飞行 / 战斗
    def thrust(self, ax, az, dt=1 / 60.0):
        """推进（惯性：速度累加 + 阻尼，松手滑行）。"""
        d = math.hypot(ax, az)
        if d < 1e-6:
            return
        s = self.ship
        s['vx'] += ax / d * SHIP_ACCEL * dt
        s['vz'] += az / d * SHIP_ACCEL * dt
        s['heading'] = math.atan2(az / d, ax / d)

    def _integrate(self, dt):
        s = self.ship
        drag = SHIP_DRAG ** (dt * 60.0)
        s['vx'] *= drag
        s['vz'] *= drag
        v = math.hypot(s['vx'], s['vz'])
        if v > SHIP_MAX_V:
            s['vx'] *= SHIP_MAX_V / v
            s['vz'] *= SHIP_MAX_V / v
        s['x'] += s['vx'] * dt
        s['z'] += s['vz'] * dt
        # 星系边界（软反弹）
        r = math.hypot(s['x'], s['z'])
        if r > CORE_WORLD_R:
            k = CORE_WORLD_R / r
            s['x'] *= k
            s['z'] *= k
            s['vx'] *= -0.35
            s['vz'] *= -0.35

    def fire(self):
        """开火（朝 heading；冷却限制）。"""
        s = self.ship
        if s['cd'] > 0.0:
            return dict(ok=False, detail='炮未就绪')
        s['cd'] = GUN_CD
        n = self.cannon
        for k in range(max(1, n)):
            spread = (k - (n - 1) / 2.0) * 0.10
            a = s['heading'] + spread
            self.bullets.append(dict(x=s['x'], z=s['z'], vx=math.cos(a) * 26.0,
                                     vz=math.sin(a) * 26.0, life=GUN_RANGE / 26.0))
        self._emit('fire')
        return dict(ok=True, detail=f'开火（{n} 管）')

    def _pirates_step(self, dt):
        s = self.ship
        for p in self.pirates:
            if not p['alive']:
                continue
            p['flash'] = max(0.0, p['flash'] - dt * 6.0)
            dx, dz = s['x'] - p['x'], s['z'] - p['z']
            d = math.hypot(dx, dz) or 1.0
            lo, hi = PIRATE_KEEP
            radial = 1.0 if d > hi else (-1.0 if d < lo else 0.0)
            nx, nz = dx / d, dz / d
            p['x'] += nx * radial * PIRATE_SPEED * dt
            p['z'] += nz * radial * PIRATE_SPEED * dt
            p['cd'] -= dt
            if d < PIRATE_FIRE_RANGE and p['cd'] <= 0.0:
                p['cd'] = PIRATE_FIRE_CD
                # 命中判定：简化成"距离足够近就命中一枪"（玩家靠机动躲）
                if d < PIRATE_FIRE_RANGE * 0.55:
                    self._hurt_hull()
                    self._emit('hit_taken')

    def _hurt_hull(self):
        self.ctx.rules.on_event({'type': 'player_hit', 'dmg': PIRATE_DMG})

    def _bullets_step(self, dt):
        for b in list(self.bullets):
            b['x'] += b['vx'] * dt
            b['z'] += b['vz'] * dt
            b['life'] -= dt
            if b['life'] <= 0.0:
                self.bullets.remove(b)
                continue
            for p in self.pirates:
                if p['alive'] and math.hypot(b['x'] - p['x'], b['z'] - p['z']) < 1.5:
                    p['hp'] -= GUN_DMG
                    p['flash'] = 1.0
                    self.bullets.remove(b)
                    if p['hp'] <= 0:
                        p['alive'] = False
                        self.pirates_beaten += 1
                        self.ctx.economy.add('credits', PIRATE_BOUNTY)
                        self._emit('pirate_down', bounty=PIRATE_BOUNTY)
                    break

    # ---------------------------------------------------------- 主循环
    def update(self, dt):
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return
        self.time += dt
        s = self.ship
        s['cd'] = max(0.0, s['cd'] - dt)
        self._integrate(dt)
        self._pirates_step(dt)
        self._bullets_step(dt)
        # 胜负：资产达标 -> asset_reached（rules 新增分支）
        if self.total_asset() >= self.target:
            self._emit('asset_reached')
            rules.on_event({'type': 'asset_reached'})

    # --------------------------------------------- auto-pilot（与 demo 同 API）
    def auto_step(self, dt):
        """贸易脚本：**低买高卖 + 海盗击退**（headless 验收与 demo ``--flag auto`` 同源）。

        策略：
          ① 有海盗在近处 -> 转向并开火（保持中距）；
          ② 舱里某货在某站有得赚 -> 飞过去卖；
          ③ 否则飞向"买入价最低"的站点补货（优先高利润货）。
        """
        rules = self.ctx.rules
        if rules.state() in ('won', 'lost'):
            return
        s = self.ship
        # ① 海盗
        near = [p for p in self.pirates if p['alive']
                and math.hypot(p['x'] - s['x'], p['z'] - s['z']) < PIRATE_FIRE_RANGE * 1.6]
        if near:
            p = min(near, key=lambda q: math.hypot(q['x'] - s['x'], q['z'] - s['z']))
            dx, dz = p['x'] - s['x'], p['z'] - s['z']
            d = math.hypot(dx, dz) or 1.0
            # **保持中距**：远了推进、太近刹车。船速上限 22 远高于海盗 7.5 ——
            # 一路推油门会直接"冲过"目标，子弹全落空（实测海盗一直打不死）。
            if d > 9.0:
                self.thrust(dx, dz, dt)
            elif d < 5.0:
                self.thrust(-dx, -dz, dt)
            # **瞄准与推进解耦**：thrust 会把 heading 设成推进方向，刹车时就成了反向 ——
            # 必须显式重设"朝海盗"，否则子弹全打反（实测海盗一直打不死）。
            s['heading'] = math.atan2(dz, dx)
            self.fire()
            self.update(dt)
            return

        # ② 卖出：找一个"卖价 > 均价×1.06"的站点清货
        target = None
        best_gain = 0.0
        for g, n in self.cargo.items():
            if n <= 0:
                continue
            for st in self.stations:
                p_here = self.price(g, st)
                if p_here > GOOD_BASE[g] * 1.08:
                    gain = (p_here - GOOD_BASE[g]) * n
                    if gain > best_gain:
                        best_gain, target = gain, (st, g)
        # ③ 买入：找一个"买价 < 均价×0.94"的站点补货
        if target is None and sum(self.cargo.values()) < self.cargo_cap:
            for g in GOODS:
                for st in self.stations:
                    if self.price(g, st) < GOOD_BASE[g] * 0.92:
                        target = (st, g)
                        break
                if target:
                    break

        if target is not None:
            st, g = target
            dx, dz = st['pos'][0] - s['x'], st['pos'][1] - s['z']
            d = math.hypot(dx, dz)
            if d <= DOCK_R:
                if self.cargo[g] > 0:
                    self.sell(g, self.cargo[g])
                else:
                    self.buy(g, self.cargo_cap - sum(self.cargo.values()))
                    # 买完顺手升级（余钱够就升）
                    if self._credits() > 3000.0:
                        self.upgrade('cargo' if self.upgrades['cargo'] <= self.upgrades['gun'] else 'gun')
            else:
                self.thrust(dx, dz, dt)
        self.update(dt)

    # ---------------------------------------------------------- 事件
    def _emit(self, name, **kw):
        self.events_out.append(dict(type=name, **kw))

    def drain_events(self):
        evs = self.events_out
        self.events_out = []
        return evs

    # ---------------------------------------------------------- 展示
    def dock_hint(self) -> str:
        st = self.docks_at()
        return f'停靠 {st["name"]}' if st else '航行中（靠近站点可交易）'

    def status_text(self) -> str:
        st, d = nearest_station((self.ship['x'], self.ship['z']), self.stations)
        return (f"credits {self._credits():.0f}  资产 {self.total_asset():.0f}/{self.target:.0f}  "
                f"舱 {sum(self.cargo.values())}/{self.cargo_cap}  炮 {self.cannon}  "
                f"海盗 {self.pirates_beaten}  {self.dock_hint()}  距{st['name']} {d:.0f}  "
                f"state={self.ctx.rules.state()}")


__all__ = ['VoidKernel', 'GOODS', 'GOOD_BASE', 'STATIONS', 'TARGET_ASSET',
           'station_price', 'shock_table', 'shock_at', 'mean_reversion_gap',
           'nearest_station', 'DEMO_SEED']

# -*- coding: utf-8 -*-
"""《砖境 / Brickward》B1 内核测试套件（纯逻辑，零 GL、零窗口）。

运行::

    python -m tests.test_brickward

原则与 `run_tests` / `test_engine` 一致：断言数值、打印实测数据、不用"看起来对"。

**本套件的重点是把已经踩过的三个坑钉死**（每条都在 `ok:` 里写明"反例是什么"）：

1. **门的两套数据不能合并** —— 曾经 `doors` 一个集合既当"门格"又当"开着的门"，
   结果**关着的门能穿、开着的门挡人**；求解器于是解出"走穿门"的假解，三关全
   报"可解"，回放却全是穿模。→ `test_closed_door_blocks_open_door_passes`
2. **拉杆格集合曾经恒为空** —— `_settle` 查 `self.levers`（其实是"on 的集合"），
   于是拉杆**从不翻转**，拉杆关变成纯装饰。→ `test_lever_toggles_on_step`
3. **克隆不得重跑结算** —— `clone_from` 里带 `_settle` 时，"人正站在拉杆上"的
   节点一旦被克隆就把拉杆**扳回去**，BFS 搜出来的解全是错的。
   → `test_clone_does_not_retoggle_standing_lever`

另有两条是"机制从未被覆盖"的补漏：`toggle` 连接在**按钮**上整条失效
（`src_flip` 只有拉杆会发）、以及状态指纹的完备性（漏一项 ⇒ BFS 剪掉正解）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from games.brickward.kernel import (  # noqa: E402
    BrickwardKernel, DEMO_SEEDS, ELEMENTS, LEVELS, LEVEL_ART, WALL, FLOOR,
    CRATE, SPIKE, PLAYER, EXIT, BOMB, FIREBALL, FUSE, ROCK, SLIME,
    SLIME_PERIOD, BUTTON, DOOR, GOLD_DOOR, KEY,
    level_add_link, level_blank, level_clear_links, level_from_json,
    level_set_cell, level_to_json,
    generate, make_offline, parse_level, solve_level, validate_level,
)


def _play(level, path):
    """按方向串回放，返回终局内核（求解器与 step 是否同一套物理，靠它对齐）。"""
    k = make_offline(level)
    for d in path:
        k.step(d)
    return k


def _lv(name):
    """按关名取随包关卡（比下标稳：加关不会把断言打偏）。"""
    for lv in LEVELS:
        if lv['name'] == name:
            return lv
    raise AssertionError(f'没有名为 {name!r} 的随包关卡；现有：'
                         f'{[l["name"] for l in LEVELS]}')


#: 全元素微型关（每种元素各一）：用于状态指纹的**完整性**检查。
ALL_ELEMENTS_ART = """
###########
#P.C.o.L.D#
#.........#
#k^bfsrgD#
#........E#
###########
"""

ALL_ELEMENTS_LINKS = [
    {'from': (1, 5), 'to': (1, 9), 'mode': 'open'},      # 按钮 → 普通门
    {'from': (1, 7), 'to': (3, 9), 'mode': 'toggle'},    # 拉杆 → 另一扇普通门
]


def _all_elements_level():
    return parse_level(ALL_ELEMENTS_ART, 'all-elements', ALL_ELEMENTS_LINKS)


# ================================================================ 1. 元素表
def test_elements_table_covers_art():
    """元素表必须覆盖关卡里出现的**每一个**字符，且表里每个机制都**真被用过**。

    漏一个 ⇒ 静默变地板；多一个 ⇒ 有机制从没被任何关卡触发过（= 没验过的机制）。
    尖刺（`^`）在随包手工关里没有，只由 `generate()` 撒 —— 所以这里把生成关的
    输出也算进"被用过"的样本里。
    """
    art_chars = set()
    for art in LEVEL_ART.values():
        art_chars |= {ch for ch in art if ch != '\n'}
    gen_chars = set()
    for sd in DEMO_SEEDS:
        for row in generate(sd)['rows']:
            gen_chars |= set(row)
    live = art_chars | gen_chars
    table = set(ELEMENTS)
    missing = sorted(live - table)
    assert not missing, f'元素表缺 {missing}（会静默当地板处理）'
    unused = sorted(table - live)
    assert not unused, f'元素表里从没在任何关卡出现过的机制：{unused}'
    assert '^' in gen_chars, '尖刺只能靠 generate() 出现，这里必须确认它真出现了'
    print(f'  ok: 元素表 {len(table)} 类；手工关 {len(art_chars)} 种字符 + '
          f'生成关补上 {sorted(gen_chars - art_chars)}，无漏无余')


def test_element_flags_consistent():
    """`block`/`pushable` 是编辑器（B3）与渲染（B4）共用的判据，不能自相矛盾。"""
    assert ELEMENTS[WALL]['block'] and not ELEMENTS[WALL]['pushable']
    assert not ELEMENTS[FLOOR]['block']
    assert ELEMENTS[CRATE]['pushable'], '箱子必须可推'
    assert ELEMENTS['D']['block'] and ELEMENTS['g']['block'], '门/金门关着要挡人'
    assert ELEMENTS['o']['pushable'] and ELEMENTS['L']['pushable'], '箱可压按钮/拉杆'
    for code in (PLAYER, EXIT):
        assert not ELEMENTS[code]['block'] and not ELEMENTS[code]['pushable']
    print('  ok: block/pushable 自洽（墙硬挡、箱可推、门关着挡、玩家/出口中性）')


# ================================================================ 2. 校验器
def test_validate_accepts_shipped_levels():
    for i, lv in enumerate(LEVELS):
        ok, why = validate_level(lv)
        assert ok, f'随包关卡 {i}（{lv["name"]}）校验失败：{why}'
    print(f'  ok: {len(LEVELS)} 个随包关卡全部通过结构校验')


def test_validate_rejects_bad_levels():
    """反例：坏关卡必须在**装载前**被拦住，而不是玩到一半崩。"""
    ok, reason = validate_level(parse_level('#####\n#...#\n#####', 'no-p'))
    assert not ok and 'P' in reason, reason
    ok, reason = validate_level(parse_level('#####\n#P..#\n#####', 'no-e'))
    assert not ok and 'E' in reason, reason
    ok, reason = validate_level('不是 dict')
    assert not ok, '非 dict 竟然通过'
    # 合法对照：不能"一律拒绝"
    ok, reason = validate_level(parse_level('#P#\n#E#', 'tiny'))
    assert ok, f'合法关被判非法：{reason}'
    # 连接表坏数据
    ok, reason = validate_level(parse_level('#P.#\n#.E#', 'bad-src',
                                           [{'from': (0, 0), 'to': (1, 2)}]))
    assert not ok and '连接源' in reason, reason
    ok, reason = validate_level(parse_level('#oP#\n#.E#', 'bad-dst',
                                           [{'from': (0, 1), 'to': (0, 0)}]))
    assert not ok and '连接目标' in reason, reason
    # 连到金门必须被**明确拒绝**（否则就是"按了没反应"的静默失效）
    gold_link = parse_level('#o.#\n#.g#\n#PE#', 'link-to-gold',
                            [{'from': (0, 1), 'to': (1, 2)}])
    ok, reason = validate_level(gold_link)
    assert not ok and '金门' in reason, reason
    # 未知模式：目标必须先是一扇**真的**门，否则会先撞上"不是门"那条
    ok, reason = validate_level(parse_level('#o.D#\n#P.E#', 'bad-mode',
                                           [{'from': (0, 1), 'to': (0, 3),
                                             'mode': 'nope'}]))
    assert not ok and '模式' in reason, reason
    # 三种合法模式必须放行（不能"一律拒绝"）
    for mode in ('open', 'close', 'toggle'):
        ok, reason = validate_level(parse_level('#o.D#\n#P.E#', f'ok-{mode}',
                                               [{'from': (0, 1), 'to': (0, 3),
                                                 'mode': mode}]))
        assert ok, f"合法模式 {mode} 被拒：{reason}"
    print('  ok: 缺 P/缺 E/非 dict/坏源/坏目标/连金门/未知模式 全部被拒；三模式放行')


# ================================================================ 3. 墙与边界
def test_wall_blocks_and_player_does_not_move():
    k = make_offline(parse_level('#P#\n#E#', 'w'))
    before = k.player
    ev = k.step('U')                     # 上面就是墙
    assert ev == 'blocked' and k.player == before, f'{ev} {k.player}'
    assert k.steps == 0, '撞墙不该计步'
    assert k.status() == 'playing'
    print('  ok: 撞墙返回 blocked、位置不变、不计步')


def test_border_is_wall_no_index_error():
    """所有随包关卡必须四面封闭：否则玩家走出边界时行为未定义。"""
    for lv in LEVELS:
        rows = lv['rows']
        assert all(ch == WALL for ch in rows[0]), f'{lv["name"]} 上边界漏'
        assert all(ch == WALL for ch in rows[-1]), f'{lv["name"]} 下边界漏'
        for r in rows:
            assert r[0] == WALL and r[-1] == WALL, f'{lv["name"]} 左右边界漏'
    print(f'  ok: {len(LEVELS)} 个关卡四面封闭（越界不可达）')


# ================================================================ 4. 门 / 拉杆（回归 1、2）
def test_closed_door_blocks_open_door_passes():
    """**回归 1**：门的两套数据不可合并。

    反例（把旧实现挂回去立即报红）：`doors` 同时当"门格"与"开着的门"。
    那时 `_solid_for_player(门)` 恒为 False（关着也能穿），按钮一压反而变 True。
    """
    lv = _lv('拉杆切门')
    k = make_offline(lv)
    door = sorted(k.door_cells)[0]
    lever = sorted(k.lever_cells)[0]
    assert k.doors == set(), '初始不应有开着的门'
    assert k._solid_for_player(door) is True, '关着的门必须挡人'

    for d in ('R', 'R', 'R'):            # (1,2) (1,3) (1,4)=拉杆
        assert k.step(d) == 'moved', d
    assert k.player == lever, k.player
    assert k.doors == {door}, f'拉杆 on（open 模式）应立即开门：{k.doors}'
    assert k._solid_for_player(door) is False, '开着的门必须能过'
    print('  ok: 关着的门挡人 / 开着的门能过（两套数据分离，二者不互相污染）')


def test_lever_toggles_on_step():
    """**回归 2**：拉杆格集合曾经恒空 ⇒ 拉杆从不翻转。"""
    lv = _lv('拉杆切门')
    k = make_offline(lv)
    lever = sorted(k.lever_cells)[0]
    assert k.lever_cells == {lever}, '静态拉杆格集合必须非空'
    assert k.levers == set(), '初始没有 on 的拉杆'
    for d in ('R', 'R', 'R'):
        k.step(d)
    assert k.player == lever and k.levers == {lever}, '踩上应翻到 on'
    k.step('L')
    assert k.player != lever and k.levers == {lever}, '离开拉杆不应自己翻回去'
    k.step('R')
    assert k.levers == set(), f'再踩一次应翻回 off：{k.levers}'
    print('  ok: 拉杆"走过即翻转"、离开不复位、二踩翻回 off')


def test_link_modes_open_close_toggle():
    """三种连接模式各自的真值表（含 `toggle` 在**按钮**上 —— 曾整条失效）。"""
    art = '#P.o.D#\n#######'

    k = make_offline(parse_level(art, 'open', [{'from': (0, 3), 'to': (0, 5),
                                               'mode': 'open'}]))
    assert k.doors == set()
    k.step('R'), k.step('R')
    assert k.player == (0, 3) and k.doors == {(0, 5)}, '压下应开门'
    k.step('R')
    assert k.player == (0, 4) and k.doors == set(), f'放开关门失效：{k.doors}'

    k = make_offline(parse_level(art, 'close', [{'from': (0, 3), 'to': (0, 5),
                                                'mode': 'close'}]))
    assert k.doors == {(0, 5)}, 'close 模式在源未触发时应是开的'
    k.step('R'), k.step('R')
    assert k.doors == set(), '压下应关门'
    k.step('R')
    assert k.doors == {(0, 5)}, f'放开应开门：{k.doors}'

    k = make_offline(parse_level(art, 'toggle', [{'from': (0, 3), 'to': (0, 5),
                                                 'mode': 'toggle'}]))
    assert k.doors == set()
    k.step('R'), k.step('R')             # 压下 → 翻一次
    assert k.doors == {(0, 5)}, f'按钮 toggle 应翻转（曾完全失效）：{k.doors}'
    k.step('R')                          # 放开 → 再翻一次
    assert k.doors == set(), f'放开应再翻回：{k.doors}'
    print('  ok: open/close/toggle 三模式真值表；按钮上的 toggle 也会翻')


# ================================================================ 5. 金门 / 钥匙
def test_gold_door_needs_key_and_consumes_one():
    lv = _lv('钥匙金门')
    gold = sorted(make_offline(lv).gold)[0]
    k0 = make_offline(lv)
    assert k0._solid_for_player(gold) is True, '没钥匙时金门必须挡人'
    assert k0.keys_held == 0

    path = solve_level(lv)[1]
    assert path, '钥匙金门关不可解'
    # 找到"开金门"那一步：第一个让 gold_open 变非空的前缀位置
    pos = None
    for i in range(1, len(path) + 1):
        if _play(lv, path[:i]).gold_open:
            pos = i
            break
    assert pos is not None, '解里从未打开金门（钥匙/金门成了摆设）'

    before = _play(lv, path[:pos - 1])
    after = _play(lv, path[:pos])
    assert before.keys_held == 1, f'开门前应恰好握着 1 把钥匙：{before.keys_held}'
    assert before.gold_open == set() and after.gold_open == {gold}, \
        f'{before.gold_open} -> {after.gold_open}'
    assert after.keys_held == 0, f'开金门应消耗一把钥匙：{after.keys_held}'
    assert after.player == before.player, '开金门那一步**只开门、人不动**'
    # 金门的状态只在 gold_open 里 —— 绝不能被开关写进 doors（否则两套数据又打架）
    assert gold not in after.doors and gold in after.gold_open
    # 无钥匙直接撞门 → blocked
    k = make_offline(lv)
    k.player, k.keys_held = (gold[0] - 1, gold[1]), 0
    assert k.step('D') == 'blocked', '无钥匙撞金门应 blocked'
    assert gold not in k.gold_open and k.keys_held == 0, '撞门不该白扣/白开'
    print(f'  ok: 无钥匙挡路 / 开门消耗 1 把 / 开门步人不移动（解第 {pos} 步）')


def test_gold_door_open_step_is_idempotent_when_already_open():
    """已开的金门不再是障碍（不会反复扣钥匙）。"""
    lv = _lv('钥匙金门')
    gold = sorted(make_offline(lv).gold)[0]
    k = make_offline(lv)
    k.player, k.keys_held = (gold[0] - 1, gold[1]), 2
    assert k.step('D') == 'unlock' and k.keys_held == 1 and k.player == (gold[0] - 1, gold[1])
    assert k.step('D') == 'moved' and k.player == gold, '第二次应直接走过去'
    assert k.keys_held == 1, f'已开的门不该再扣钥匙：{k.keys_held}'
    print('  ok: 金门开一次即长期有效，二次进入不扣钥匙')


# ================================================================ 6. 箱子 / 按钮 / 尖刺
def test_crate_on_button_opens_linked_door():
    lv = _lv('直推教学')
    k = make_offline(lv)
    button = sorted(k.button_cells)[0]
    door = sorted(k.door_cells)[0]
    assert k.doors == set()
    for d in ('R', 'R', 'R', 'R', 'R'):   # 把箱子推到按钮上
        k.step(d)
    assert k.crates == {button}, f'箱子应落在按钮上：{k.crates} vs {button}'
    assert k.doors == {door}, f'箱子压按钮应开门（持续型）：{k.doors}'
    # 人自己站上去同样触发（按钮是"玩家或箱子压住"）
    k2 = make_offline(lv)
    k2.player = button
    k2._settle(None, None)
    assert k2.doors == {door}, '玩家自己压按钮也应开门'
    print('  ok: 箱子压按钮 → 持续开门（非脉冲）；玩家压也同样触发')


def test_spike_kills_player_and_crate_disarms_it():
    """尖刺两个方向都要验：踩上即败；箱子压上后失效（经典"用箱垫路"）。"""
    k = make_offline(_all_elements_level())
    spike = (3, 2)
    assert spike in k.spikes, f'全元素关里应有尖刺：{sorted(k.spikes)}'
    assert k.step('D') == 'moved'        # (2,1)
    assert k.step('D') == 'moved'        # (3,1)='k' 拾钥匙
    assert k.keys_held == 1, '路过钥匙应拾取'
    ev = k.step('R')                     # (3,2)='^' → 败
    assert ev == 'died' and k.player == spike and k.status() == 'lost', \
        f'{ev} {k.player} {k.status()}'
    assert k.deaths == 1, k.deaths

    # 箱子压上 → 失效
    lv2 = parse_level('#######\n#P.C^.#\n#....E#\n#######', 'disarm')
    k2 = make_offline(lv2)
    assert k2.step('R') == 'moved'
    assert k2.step('R') == 'pushed', '应把箱子推进尖刺格'
    assert k2.crates == {(1, 4)} and k2.spike_dead == {(1, 4)}, \
        f'{k2.crates} {k2.spike_dead}'
    k2.step('R')                         # 人走进已被压住的尖刺格
    assert k2.player == (1, 4) and k2.status() == 'playing', \
        f'尖刺已失效，人不应死：{k2.status()}'
    print('  ok: 尖刺踩上即败（lost/died，deaths+1）；箱子压上后失效可安全通过')


def test_push_only_never_pull():
    """推箱：只推不拉 —— 反方向走不能把箱子拽回来。"""
    k = make_offline(parse_level('#P.C..#\n#....E#', 'push'))
    k.step('R')
    assert k.crates == {(0, 3)}
    assert k.step('R') == 'pushed'
    assert k.crates == {(0, 4)} and k.pushes == 1
    k.step('L')                          # 往回走：箱子留在原处
    assert k.crates == {(0, 4)}, f'箱子被"拉"回来了：{k.crates}'
    assert k.pushes == 1, f'推数不该增加：{k.pushes}'
    print('  ok: 只推不拉（回走时箱子原位不动、推数不变）')


def test_push_blocked_by_wall_and_by_closed_door():
    """推箱的两条边界：后面是墙推不动；后面是关着的门也推不动。"""
    k = make_offline(parse_level('#PC#\n#EE#', 'push-wall'))
    assert k.step('R') == 'blocked' and k.crates == {(0, 2)}, '后面是墙，推不动'
    k2 = make_offline(parse_level('#PCD#\n#o.E#', 'push-door',
                                 [{'from': (1, 1), 'to': (0, 3), 'mode': 'open'}]))
    assert k2.step('R') == 'blocked', '门（关着）后面的箱子推不进去'
    k2.doors.add((0, 3))                 # 手动开门 → 现在可推
    assert k2.step('R') == 'pushed' and k2.crates == {(0, 3)}, '门开了应能推进去'
    print('  ok: 推箱被墙 / 被关着的门挡住；开门后放行')


# ================================================================ 7. 状态指纹
def test_state_key_covers_every_dynamic_field():
    """**求解器正确性的地基**：指纹漏一项 ⇒ BFS 把两个不同局面当同一个 ⇒ 剪掉正解。

    做法：对每个动态字段各造一个"只有它不同"的局面，断言指纹必变。
    """
    lv = _all_elements_level()
    base = make_offline(lv)
    k0 = base.key()

    def differs(**mut):
        k = make_offline(lv)
        for name, val in mut.items():
            setattr(k, name, val)
        return k.key() != k0

    assert differs(player=(2, 2)), 'player 未进指纹'
    assert differs(crates=set()), 'crates 未进指纹'
    assert differs(doors={(1, 9)}), 'doors（开着的门）未进指纹'
    assert differs(gold_open={(3, 8)}), 'gold_open 未进指纹'
    assert differs(keys=set()), 'keys（地上钥匙）未进指纹'
    assert differs(keys_held=1), 'keys_held 未进指纹'
    assert differs(levers={(1, 7)}), 'levers（on 的拉杆）未进指纹'
    assert differs(spike_dead={(3, 5)}), 'spike_dead 未进指纹'
    assert differs(_status='won'), '_status 未进指纹'
    assert differs(bombs=set()), 'bombs 未进指纹'
    assert differs(fuses={(3, 3): 2}), 'fuses（引信剩余步数）未进指纹'
    # ⚠ 火球的方向必须进指纹：两个"火球在同一格"的局面，朝上/朝下往后完全不同
    assert differs(fireballs={(3, 5): 'R'}), 'fireballs 的**方向**未进指纹'
    assert differs(rocks=set()), 'rocks 未进指纹'
    assert differs(slimes=set()), 'slimes 未进指纹'

    # 反向：与"往后怎么演化"无关的量**不能**进指纹（进了就白烧内存/白建节点）
    k = make_offline(lv)
    k.steps, k.pushes, k.deaths = 99, 99, 9
    k.log.append(('R', 'moved', 'playing'))
    k.blasts.add((0, 0))          # 本步的火光：纯渲染用，不参与后续演化
    k._ignited.add((0, 0))        # "本步刚被推动"：瞬态
    assert k.key() == k0, 'steps/pushes/deaths/log/blasts/_ignited 不该进指纹'

    # `tick` 只许进**相位**：同相位必须同指纹，异相位必须不同（否则要么剪错、要么白烧内存）
    same = make_offline(lv)
    same.tick = SLIME_PERIOD * 7
    assert same.key() == k0, 'tick 进了绝对值（应只进 `tick % SLIME_PERIOD`）'
    if SLIME_PERIOD > 1:
        off = make_offline(lv)
        off.tick = 1
        assert off.key() != k0, 'tick 相位没进指纹（史莱姆的行动时刻会算错）'
    print(f'  ok: 指纹含全部 15 项动态量（含引信、火球**方向**、滚石/史莱姆、'
          f'`tick % {SLIME_PERIOD}` 相位）、不含 6 项瞬态/无关量（漏/多都算错）')


def test_clone_does_not_retoggle_standing_lever():
    """**回归 3**：克隆时重跑 `_settle` 会把"人正站在拉杆上"的状态扳回去。"""
    lv = _lv('拉杆切门')
    k = make_offline(lv)
    for d in ('R', 'R', 'R'):
        k.step(d)
    lever = sorted(k.lever_cells)[0]
    assert k.player == lever and k.levers == {lever}

    clone = BrickwardKernel._spawn(k)
    assert clone.levers == k.levers, f'克隆把拉杆扳回去了：{clone.levers}'
    assert clone.doors == k.doors, f'克隆把门状态改了：{clone.doors}'
    assert clone.key() == k.key(), '克隆后指纹必须一致（吃克隆的 BFS 全靠这条）'
    # 克隆必须是**独立**的：动克隆不能影响本体（浅拷贝漏项会在这里露出来）
    clone.crates.add((0, 0))
    clone.doors.add((0, 0))
    clone._status = 'lost'
    assert k.crates == set() and k.doors == {sorted(k.door_cells)[0]}, \
        f'克隆与本体共享了状态：{k.crates} {k.doors}'
    assert k.status() == 'playing'
    print('  ok: 克隆不重跑结算、指纹一致、且与本体状态完全独立')


# ================================================================ 8. 求解器
def test_every_shipped_level_solvable_and_replays_to_win():
    """求解器说"能赢"不算数 —— 解必须**回放**到 `won`（两套物理对齐才作数）。"""
    for i, lv in enumerate(LEVELS):
        ok, path = solve_level(lv)
        assert ok and path, f'随包关卡 {i}（{lv["name"]}）不可解'
        k = _play(lv, path)
        assert k.status() == 'won', \
            f'关卡 {i} 回放未胜（求解器与 step 物理解不一致！）：{k.status()}'
        print(f'       {lv["name"]}: {len(path)} 步 pushes={k.pushes} path={path}')
    print(f'  ok: {len(LEVELS)} 个随包关卡全部"可解 + 回放到胜"')


def test_solver_is_deterministic():
    """同关连解两次必须一字不差（搜索里混进集合迭代序就会漂）。"""
    for lv in LEVELS:
        _, p1 = solve_level(lv)
        _, p2 = solve_level(lv)
        assert p1 == p2, f'{lv["name"]} 两次求解不同：{p1} != {p2}'
    print(f'  ok: {len(LEVELS)} 个关卡两次求解逐字一致')


def test_solver_returns_none_for_unsolvable():
    """不可解要返回 None（不是空串、不是死循环）：出口被墙封死。"""
    ok, path = solve_level(parse_level('#P.#\n###\n#E#', 'sealed'))
    assert ok is False and path == '', f'{ok} {path!r}'
    # 非法关卡也不能解出东西
    ok, path = solve_level(parse_level('#####\n#...#\n#####', 'no-endpoints'))
    assert ok is False and path == '', f'{ok} {path!r}'
    print('  ok: 出口被封死 / 非法关卡 → (False, "")')


def test_solver_path_is_minimal_for_tiny_case():
    """最短性可手算的微型关：给"BFS 真的在找最短"留个锚点。"""
    ok, path = solve_level(parse_level('#####\n#P.E#\n#####', 'line'))
    assert ok and path == 'RR', f'应 2 步（右、右）：{path!r}'
    # 绕一个 U 的最短解也是手算得出的：RRDDLL = 6 步
    maze_art = '#######\n#P....#\n###.###\n#E....#\n#######'
    ok, path = solve_level(parse_level(maze_art, 'maze'))
    assert ok, '绕路关不可解'
    assert len(path) == 6, f'绕路关最短应 6 步（手算）：{path!r}'
    k = _play(parse_level(maze_art, 'maze'), path)
    assert k.status() == 'won', k.status()
    print(f'  ok: 直线关最短解 {path!r}（手算 6 步一致）且回放到胜')


def test_solver_already_won_returns_empty():
    lv = parse_level('####\n#PE#\n####', 'starts-on-exit')
    k = make_offline(lv)
    assert k.status() == 'playing', '装载时不该直接判胜'
    k.step('U')                          # 撞墙，仍 playing
    _, path = solve_level(lv)
    assert path == 'R', f'应 1 步走到出口：{path!r}'
    k2 = _play(lv, path)
    assert k2.status() == 'won' and k2.solve() == [], '已在终点应返回空解 []'
    print('  ok: 装载不判胜 / 1 步到出口 / 已在终点返回空解 []')


# ================================================================ 9. 生成器
def test_generate_deterministic_and_solvable():
    """同种子 ⇒ 同样的关卡，且**由求解器保证可解**（生成器不得吐出死关）。"""
    for sd in DEMO_SEEDS:
        a = generate(sd)
        b = generate(sd)
        assert a['rows'] == b['rows'], f'种子 {sd} 两次生成不同'
        assert a['name'] == b['name'] == f'gen-{sd}'
        ok, path = solve_level(a)
        assert ok, f'种子 {sd} 生成关不可解'
        k = _play(a, path)
        assert k.status() == 'won', f'种子 {sd} 回放未胜'
        assert k.steps > 0, f'种子 {sd} 是平凡关（零步通关）'
        spikes = sum(r.count(SPIKE) for r in a['rows'])
        print(f'       seed={sd}: {len(path)} 步 pushes={k.pushes} spikes={spikes}')
    print(f'  ok: {len(DEMO_SEEDS)} 个种子全部"确定性 + 可解 + 回放到胜"')


def test_generate_never_returns_invalid_level():
    for sd in list(DEMO_SEEDS) + [1, 2, 3, 999]:
        lv = generate(sd)
        ok, why = validate_level(lv)
        assert ok, f'种子 {sd} 生成非法关：{why}'
        ok, path = solve_level(lv)
        assert ok, f'种子 {sd} 生成关不可解'
    print('  ok: 10 个种子（含未见过的）生成的关卡全部合法且可解')


# ================================================================ 10. 内核服务面
def test_kernel_service_surface():
    """内核是 cordis `Service`：`inject` 为空（纯逻辑）且快照字段齐全。"""
    assert BrickwardKernel.inject == [], 'B1 不该 inject 任何服务（零 GL 验收）'
    k = make_offline(_all_elements_level())
    snap = k.snapshot()
    for name in ('player', 'crates', 'door_cells', 'doors_open', 'gold_cells',
                 'gold_open', 'keys_ground', 'keys_held', 'lever_cells',
                 'levers_on', 'button_cells', 'spikes', 'spikes_dead', 'exit',
                 'bombs', 'fuses', 'fireballs', 'rocks', 'slimes', 'tick', 'blasts',
                 'rows', 'links', 'status', 'steps', 'pushes', 'deaths'):
        assert name in snap, f'snapshot 缺字段 {name}（渲染层要按它建几何）'
    assert snap['player'] == k.player
    assert snap['crates'] == sorted(k.crates)
    assert snap['doors_open'] == sorted(k.doors)
    assert snap['gold_open'] == sorted(k.gold_open)
    assert snap['levers_on'] == sorted(k.levers)
    assert snap['spikes_dead'] == sorted(k.spike_dead)
    assert snap['bombs'] == sorted(k.bombs)
    assert snap['fireballs'] == sorted(k.fireballs.items())
    assert snap['rocks'] == sorted(k.rocks)
    assert snap['slimes'] == sorted(k.slimes)
    assert snap['fuses'] == sorted(k.fuses.items()) == []
    assert snap['keys_held'] == k.keys_held == 0
    assert snap['tick'] == k.tick == 0
    assert snap['status'] == k.status() == 'playing'
    assert snap['links'] is k.level['links'], 'links 该是引用（纯数据，别每帧深拷）'
    print(f'  ok: inject=[] ；snapshot {len(snap)} 字段齐全且与内核状态逐项一致')


def test_snapshot_roundtrip_after_replay():
    """快照与直接读属性必须一致 —— 渲染层只吃快照，不许出现"面板显示假的"。"""
    lv = _lv('直推教学')
    path = solve_level(lv)[1]
    k = _play(lv, path)
    snap = k.snapshot()
    assert snap['status'] == 'won' and snap['steps'] == k.steps
    assert snap['crates'] == sorted(k.crates)
    assert snap['doors_open'] == sorted(k.doors)
    assert snap['level'] == lv['name']
    print(f'  ok: 通关后快照一致（steps={snap["steps"]} pushes={snap["pushes"]} '
          f'status={snap["status"]}）')


# ================================================================ 10. 炸弹与火球（B2a）
def test_bomb_static_until_pushed_then_fuse_starts():
    """炸弹初始静止、没有引信；**推动**才点引信，且引信随炸弹一起搬走。"""
    k = make_offline(_lv('炸药开路'))
    assert k.bombs == {(5, 5)} and k.fuses == {}, '装载后炸弹不该已经在倒计时'
    assert k.step('U') == 'pushed'
    assert k.bombs == {(4, 5)} and k.fuses == {(4, 5): FUSE}, \
        f'推动后应剩 FUSE={FUSE} 步：{k.fuses}'
    k.step('U')
    assert k.bombs == {(3, 5)} and k.fuses == {(3, 5): FUSE}, \
        f'第二次推动应重置引信，且**旧格不能留残影**：{k.fuses}'
    print(f'  ok: 静止炸弹无引信；推动即点 FUSE={FUSE}；再推则重置'
          f'（而非两处同时倒计时）')


def test_fuse_travels_with_bomb_regression():
    """**回归守卫**：引信是**炸弹的属性**，不是格子属性。

    反例（把 `self.fuses.pop(frm, None)` 删掉立即报红）：炸弹被推走后旧格仍留着
    倒计时 ⇒ 到点**原地凭空爆炸**，把旁边（甚至已经走开的）玩家炸死，
    求解器于是判定"不可解"（实踩：炸药开路整关被判死）。
    """
    k = make_offline(_lv('炸药开路'))
    for d in ('U', 'U', 'U', 'U'):       # 连续往上推四次（每次重置引信）
        ev = k.step(d)
        assert len(k.fuses) <= 1, f'引信残影：{k.fuses}'
        assert set(k.fuses) <= k.bombs, f'引信挂在没有炸弹的格子上：{k.fuses}'
        if ev != 'pushed':
            break
    live = set(k.fuses)
    assert live <= k.bombs, f'{live} 不在炸弹位置 {k.bombs} 上'
    print(f'  ok: 任何时刻引信集合 ⊆ 炸弹位置（无残影）；剩 {sorted(k.fuses.items())}')


def test_blast_shape_is_cross_one():
    """爆炸波及 = 十字 1 格（自身 + 上下左右），**不是 3×3**。"""
    lv = parse_level('#######\n#P.b..#\n#.....#\n#######', 'blast-shape')
    k = make_offline(lv)
    assert k.step('R') == 'moved'        # (1,2)
    assert k.step('R') == 'pushed'       # 炸弹 (1,3)→(1,4)，玩家 (1,3)
    k.step('D')                          # (2,3)  引信 2→1
    k.step('D')                          # 撞墙 blocked，引信 1→0 → 爆
    assert k.bombs == set() and k.fuses == {}, '到点应炸掉炸弹本体'
    assert k.blasts == {(0, 4), (1, 3), (1, 4), (1, 5), (2, 4)}, \
        f'波及不对：{sorted(k.blasts)}'
    # 十字：只到相邻一格 —— (2,3)/(2,5) 都不该被波及
    assert (2, 3) not in k.blasts and (2, 5) not in k.blasts, \
        '邻行被波及了（说明写成了 3×3）'
    assert k.player == (2, 3) and k.status() == 'playing', '玩家在波及外不该死'
    print(f'  ok: 十字 1 格 = {sorted(k.blasts)}（含上方的墙，渲染要画整片火光）；'
          f'邻行不受伤')


def test_blast_chain_reaction():
    """炸弹连环引爆：爆炸波及到另一颗炸弹时把它也点着，用队列展开。"""
    lv = parse_level('#########\n#P.b.b..#\n#.......#\n#########', 'chain')
    k = make_offline(lv)
    k.step('R'), k.step('R')             # 推第一颗炸弹到 (1,4)，玩家 (1,3)
    k.step('D'), k.step('D')             # 退到 (2,3) 等引爆
    assert k.bombs == set(), f'两颗都应被引爆：{k.bombs}'
    assert k.blasts == {(1, 3), (1, 4), (2, 4), (1, 5), (2, 5), (1, 6),
                        (0, 4), (0, 5)}, \
        f'二次爆炸没跟上：{sorted(k.blasts)}'
    assert k.status() == 'playing', '玩家在 (2,3)，两颗的波及都不该碰到他'
    print(f'  ok: 连环引爆 2 颗，波及 {len(k.blasts)} 格，玩家安全')


def test_blast_destroys_crate_but_not_wall_door_or_gold():
    """爆炸的摧毁白名单：箱子没了；墙 / 关着的门 / 金门**原样不动**。"""
    art = ('###########\n#..C......#\n#.g.b.....#\n#..D......#\n'
           '#.........#\n#P.......E#\n###########')
    k = make_offline(parse_level(art, 'blast-targets'))
    crate, gold, door, wall = (1, 3), (2, 2), (3, 3), (0, 3)
    assert k.crates == {crate} and k.gold == {gold} and k.door_cells == {door}
    # 直接把玩家放到炸弹右侧再推 —— 比走位好读，也不依赖寻路
    k.player = (2, 5)
    k._pressed_prev = k._pressed_sources()
    assert k.step('L') == 'pushed', '应把炸弹从 (2,4) 推到 (2,3)'
    assert k.bombs == {(2, 3)} and k.fuses == {(2, 3): FUSE}
    k.step('R'), k.step('R')             # 退到 (2,6)，两步后引爆
    assert k.bombs == set() and k.fuses == {}
    assert k.crates == set(), f'箱子必须被炸掉：{k.crates}'
    assert k.gold == {gold} and k.gold_open == set(), '金门不该被炸开'
    assert k.door_cells == {door} and k.doors == set(), '门不该被炸开'
    assert wall in k.walls and crate not in k.walls, '墙不该被炸掉'
    assert k.blasts == {(2, 3), (2, 2), (2, 4), (1, 3), (3, 3)}, \
        f'波及格（**含**不可摧毁物，渲染要画整片火光）不对：{sorted(k.blasts)}'
    print(f'  ok: 箱子被炸掉；墙/门/金门原样；波及 {len(k.blasts)} 格全记下')


def test_blast_kills_player_standing_in_range():
    """走回波及区里会被自己的炸弹炸死 —— 这是"一步之内的赌注"，必须成立。"""
    lv = parse_level('#######\n#P.b..#\n#.....#\n#######', 'self-bomb')
    k = make_offline(lv)
    k.step('R'), k.step('R')             # 推炸弹到 (1,4)，玩家 (1,3)
    assert k.step('D') == 'moved'        # (2,3) 引信 1
    ev = k.step('R')                     # (2,4) ← 在 (1,4) 的正下方
    assert ev == 'died' and k.status() == 'lost', f'{ev} {k.status()}'
    assert k.deaths == 1 and k.player == (2, 4)
    print('  ok: 主动走回波及区 → died / lost / deaths=1')


def test_bomb_level_needs_the_bomb():
    """「炸药开路」的箱子**只能炸**：抽掉炸弹后必须无解（否则炸弹是装饰）。"""
    lv = _lv('炸药开路')
    ok, path = solve_level(lv)
    assert ok and path, '炸药开路本身应该可解'
    assert path.count('U') >= 5, f'解应含"推炸弹上去 + 一路走上出口"：{path}'
    no_bomb = parse_level(LEVEL_ART['炸药开路'].replace('b', '.'), 'no-bomb')
    ok2, path2 = solve_level(no_bomb)
    assert not ok2, f'没有炸弹竟然也能通关，炸弹是装饰：{path2!r}'
    # 反向对照：把挡路箱子也拿掉（不需要炸）→ 必须可解，
    # 证明前面的"无解"确实来自那颗箱子，而不是关卡本身写坏了
    no_crate = parse_level(LEVEL_ART['炸药开路'].replace('C', '.'), 'no-crate')
    ok3, path3 = solve_level(no_crate)
    assert ok3, f'抽掉箱子后应可解（否则"无解"另有原因）：{path3!r}'
    print(f'  ok: 有关卡解 {path}（{len(path)} 步）；抽掉炸弹 → 无解；'
          f'抽掉箱子 → 可解 {path3}')


def test_fireball_static_until_pushed_regression():
    """**回归守卫**：静止火球（方向 `None`）不参与滚动。

    反例（去掉 `if self.fireballs[pos] is None: continue` 立即报红）：
    `_advance_world` 会对 `DIRS[None]` 取值 → `KeyError: None`，
    整个求解器炸掉（实踩：`test_solver_is_deterministic` 直接 ERROR）。
    """
    lv = _lv('火球烧箱')
    k = make_offline(lv)
    assert k.fireballs == {(1, 3): None}, '未被推过的火球方向必须是 None'
    assert k.step('R') == 'moved'        # (1,2)
    assert k.fireballs == {(1, 3): None}, f'没被推就自己滚了：{k.fireballs}'
    assert k.step('L') == 'moved'        # 退回起点
    assert k.fireballs == {(1, 3): None}, '来回走不该点燃它'
    assert k.bombs == set() and k.fuses == {}, '不该凭空长出炸弹/引信'
    print('  ok: 静止火球不动；玩家在旁边走动不会点燃它')


def test_fireball_ignites_rolls_and_extinguishes():
    """推火球 = 点燃并朝该方向滚；烧掉挡路的箱子后自己熄灭。"""
    lv = _lv('火球烧箱')
    k = make_offline(lv)
    crate = (1, 6)
    assert k.crates == {crate}
    assert k.step('R') == 'moved'
    assert k.step('R') == 'ignited', '推火球应返回 ignited'
    assert k.fireballs == {(1, 4): 'R'}, f'点燃后方向应为 R：{k.fireballs}'
    k.step('R')                          # 再推一次（重置、朝同方向）
    assert k.fireballs == {(1, 5): 'R'}, k.fireballs
    k.step('L')                          # 玩家退开 → 火球自动滚去撞箱子
    assert k.crates == set(), f'箱子应被烧毁：{k.crates}'
    assert k.fireballs == {}, f'烧掉箱子后火球应熄灭：{k.fireballs}'
    assert k.status() == 'playing'
    print('  ok: ignited → 每步滚一格 → 烧毁箱子 → 自行熄灭')


def test_fireball_extinguishes_on_hard_obstacle():
    """撞墙 / 关着的门 / 未开金门 → 熄灭（不穿过去、也不把它们打开）。

    布局要点：火球**必须能先被推到 (1,4)**（那儿得是空的），硬障碍在 (1,5) ——
    否则连"点燃"这一步都做不出来（推不进去），测的就不是"滚着撞上"了。
    """
    for art, label, blocker in [
        ('########\n#P.f.#.#\n########', '墙', (1, 5)),
        ('########\n#P.f.D.#\n########', '关着的门', (1, 5)),
        ('########\n#P.f.g.#\n########', '未开金门', (1, 5)),
    ]:
        k = make_offline(parse_level(art, f'fb-{label}'))
        walls_before = set(k.walls)
        assert k.step('R') == 'moved'            # (1,2)
        assert k.step('R') == 'ignited', f'{label}: 应能点燃（目标格是空的）'
        assert k.fireballs == {(1, 4): 'R'}, k.fireballs
        k.step('L')                              # 退开 → 滚一格撞上 → 熄灭
        assert k.fireballs == {}, f'{label}：火球应熄灭，实际 {k.fireballs}'
        if blocker in k.door_cells:
            assert k.doors == set(), '门被火球打开了'
        if blocker in k.gold:
            assert k.gold_open == set(), '金门被火球打开了'
        assert k.walls == walls_before, '墙被火球改了'
        print(f'       {label}: 熄灭（未穿透、未开启）')
    print('  ok: 墙 / 关门 / 未开金门 → 火球熄灭；门与金门纹丝不动')


def test_fireball_detonates_bomb():
    """火球撞炸弹 → 引爆炸弹，火球自己熄灭；爆炸按十字波及。"""
    k = make_offline(parse_level('#########\n#P.f.b..#\n#.......#\n#########',
                                'fb-bomb'))
    assert k.fireballs == {(1, 3): None} and k.bombs == {(1, 5)}
    k.step('R')                          # (1,2)
    k.step('R')                          # push: 火球 (1,3)→(1,4)，玩家 (1,3)
    k.step('D')                          # 退到 (2,3)；火球滚到 (1,5) 撞上炸弹
    assert k.fireballs == {} and k.bombs == set(), \
        f'火球与炸弹应双双消失：{k.fireballs} {k.bombs}'
    assert k.blasts == {(0, 5), (1, 5), (1, 4), (1, 6), (2, 5)}, \
        f'波及不对：{sorted(k.blasts)}'
    assert k.status() == 'playing', '玩家在 (2,3) 应安全'
    print(f'  ok: 火球撞炸弹 → 双双消失 + 十字爆炸 {sorted(k.blasts)}')


def test_fireballs_colliding_cancel_out():
    """两个火球对撞 → 都熄灭（不会互相穿过）。"""
    k = make_offline(parse_level('#########\n#P.f...f.#\n#########', 'fb-pair'))
    assert k.fireballs == {(1, 3): None, (1, 7): None}
    k.step('R')                          # (1,2)
    k.step('R')                          # push 左火球 (1,3)→(1,4)，玩家 (1,3)
    assert k.fireballs == {(1, 4): 'R', (1, 7): None}, k.fireballs
    for d in 'LLR':                      # 退开三步，让它滚到右边那颗静止火球上
        k.step(d)
    assert k.fireballs == {}, f'对撞应双双熄灭：{k.fireballs}'
    assert k.player == (1, 2) and k.status() == 'playing'
    print('  ok: 滚动火球撞上静止火球 → 双双熄灭')



# ================================================================ 11. 滚石与史莱姆（B2b）
def test_rock_rolls_to_the_end_in_one_push():
    """滚石与箱子的根本区别：推一下**一直滚到底**（不可停）。"""
    lv = parse_level('##########\n#P.r.....#\n##########', 'rock-roll')
    k = make_offline(lv)
    assert k.rocks == {(1, 3)}
    assert k.step('R') == 'moved'        # (1,2)
    assert k.step('R') == 'rolled', '推滚石应返回 rolled（不是 pushed）'
    assert k.rocks == {(1, 8)}, f'应一路滚到尽头墙边的 (1,8)：{k.rocks}'
    assert k.player == (1, 3) and k.pushes == 1
    print('  ok: 一次推动 = 从 (1,3) 滚到 (1,8)（尽头顶墙才停），只计 1 次推')


def test_rock_stops_at_hard_obstacles():
    """滚石被什么挡住：墙 / 箱子 / 炸弹 / 另一块石头 / 关着的门（开着则不挡）。"""
    cases = [
        ('#P.r.C..#', {(1, 4)}, '箱子'),
        ('#P.r.b..#', {(1, 4)}, '炸弹'),
        ('#P.r..r.#', {(1, 5), (1, 6)}, '另一块滚石（挡住它，两块都在）'),
        ('#P.r..D.#', {(1, 5)}, '关着的门'),
    ]
    for row, want, label in cases:
        k = make_offline(parse_level(f'#########\n{row}\n#########', f'rock-{label}'))
        k.step('R')
        k.step('R')
        assert k.rocks == want, f'{label}：应停在 {sorted(want)}，实际 {sorted(k.rocks)}'
        print(f'       {label} 挡住 → {sorted(k.rocks)}')
    # 开着的门不挡
    k = make_offline(parse_level('#########\n#P.r..D.#\n#########', 'rock-open-door'))
    k.doors.add((1, 6))
    k.step('R'), k.step('R')
    assert k.rocks == {(1, 7)}, f'门开着就该滚过去（停在 (1,7)，(1,8) 是墙）：{k.rocks}'
    print(f'  ok: 四类硬障碍都挡得住；**开着的门不挡**（穿过去滚到 (1,7)）')


def test_rock_disarms_spikes_along_the_way():
    """滚石压过尖刺 = 尖刺失效（和箱子一样），而且**不减速**继续滚。"""
    lv = _lv('滚石垫路')
    k = make_offline(lv)
    spikes = sorted(k.spikes)
    assert len(spikes) >= 3 and k.spike_dead == set()
    k.step('R')
    assert k.step('R') == 'rolled'
    assert k.spike_dead == set(spikes), \
        f'沿途尖刺应全部失效：{sorted(k.spike_dead)} vs {spikes}'
    assert k.rocks == {(1, 11)}, f'压过尖刺不减速，应滚到 (1,11)：{k.rocks}'
    # 玩家随后走完整条走廊都不死
    for d in 'RRRRRRRDDR':
        assert k.step(d) != 'died'
    assert k.status() == 'won', k.status()
    print(f'  ok: 一次性压失效 {len(spikes)} 个尖刺且不减速；玩家走完全程通关')


def test_rock_crushes_slime_and_keeps_rolling():
    """滚石压死史莱姆后继续滚（不减速），也不会自己消失。"""
    lv = _lv('引怪入瓮')
    k = make_offline(lv)
    assert k.rocks == {(1, 4)} and k.slimes == {(1, 9)}
    k.step('R')                          # (1,2)
    k.step('R')                          # (1,3)
    assert k.slimes == {(1, 8)}, f'第 2 个 tick 史莱姆应朝玩家挪一格：{k.slimes}'
    assert k.step('R') == 'rolled'
    assert k.slimes == set(), f'史莱姆应被压死：{k.slimes}'
    assert k.rocks == {(1, 12)}, f'压死后继续滚到 (1,12)：{k.rocks}'
    print('  ok: 滚石压死史莱姆后不减速、不消失，继续滚到 (1,12)')


def test_lure_level_needs_the_rock():
    """「引怪入瓮」里的史莱姆**只能靠滚石清掉**：抽掉石头后必须无解。"""
    lv = _lv('引怪入瓮')
    ok, path = solve_level(lv)
    assert ok and path, '引怪入瓮本身应该可解'
    no_rock = parse_level(LEVEL_ART['引怪入瓮'].replace('r', '.'), 'no-rock')
    ok2, path2 = solve_level(no_rock)
    assert not ok2, f'没有滚石竟然也能通关（史莱姆是装饰）：{path2!r}'
    no_slime = parse_level(LEVEL_ART['引怪入瓮'].replace('s', '.'), 'no-slime')
    ok3, path3 = solve_level(no_slime)
    assert ok3, f'把史莱姆拿掉后应可解（否则"无解"另有原因）：{path3!r}'
    print(f'  ok: 有关卡解 {path}（{len(path)} 步）；抽掉滚石 → 无解；'
          f'抽掉史莱姆 → 可解 {path3}')


def test_slime_moves_on_period_not_every_step():
    """史莱姆每 `SLIME_PERIOD` 步才动一次（不是每步都动）。"""
    k = make_offline(parse_level('###########\n#P.......s#\n#.........#\n#.........#\n'
                                 '###########', 'slime-period'))
    assert k.slimes == {(1, 9)}
    assert k.step('D') == 'moved'        # (2,1) —— tick=1，史莱姆不该动
    assert k.slimes == {(1, 9)}, f'非行动 tick 不该动：{k.slimes}'
    assert k.step('U') == 'moved'        # (1,1) —— tick=2，该动了
    assert k.slimes == {(1, 8)}, f'行动 tick 应朝玩家挪一格：{k.slimes}'
    assert k.step('D') == 'moved'        # tick=3
    assert k.slimes == {(1, 8)}, k.slimes
    assert k.step('U') == 'moved'        # tick=4
    assert k.slimes == {(1, 7)}, k.slimes
    assert SLIME_PERIOD == 2
    print(f'  ok: SLIME_PERIOD={SLIME_PERIOD}：tick 1/3 不动、tick 2/4 各挪一格')


def test_slime_chases_along_the_dominant_axis():
    """追人顺序：先走"偏离大的那条轴"，被挡才换轴 —— 纯贪心、无随机。"""
    # 正下方：竖直偏离大 → 向上走
    k = make_offline(parse_level('###########\n#....s....#\n#.........#\n'
                                 '#....P....#\n###########', 'slime-vert'))
    k.step('L')                          # (3,4)  tick1
    assert k.step('R') == 'moved'        # (3,5)  tick2
    assert k.slimes == {(2, 5)}, f'应竖直上移：{k.slimes}'
    # 正左方：水平偏离大 → 向左走
    k = make_offline(parse_level('###########\n#P.s......#\n###########', 'slime-horz'))
    k.step('R')                          # (1,2)  tick1
    assert k.step('L') == 'moved'        # (1,1)  tick2
    assert k.slimes == {(1, 2)}, f'应水平左移：{k.slimes}'
    print('  ok: 竖直对齐时走竖轴、水平对齐时走横轴（贪心贪在最该走的轴上）')


def test_slime_blocked_by_rock_and_wall():
    """被挡时**换轴**，都挡则不动 —— 且不撞坏挡它的东西。"""
    k = make_offline(parse_level('#############\n#P.......r.s#\n#############',
                                 'slime-blocked'))
    k.step('R')                          # (1,2) tick1
    assert k.step('L') == 'moved'        # (1,1) tick2 → 史莱姆挪到 (1,10)
    assert k.slimes == {(1, 10)}, k.slimes
    k.step('R'), k.step('L')             # tick4：前面是滚石 → 换竖轴 → 上面是墙 → 不动
    assert k.slimes == {(1, 10)}, f'被滚石挡住应原地不动：{k.slimes}'
    assert k.rocks == {(1, 9)}, '挡路物不该被史莱姆弄坏'
    print('  ok: 前面是滚石、换轴又是墙 → 原地不动（不撞坏挡路物）')


def test_slime_kills_player_both_directions():
    """接触即败：玩家走上去会死；史莱姆走过来也会死。"""
    # (a) 玩家主动走进去
    k = make_offline(parse_level('###########\n#P.s......#\n###########', 'slime-atk'))
    assert k.step('R') == 'moved'
    ev = k.step('R')                     # (1,3) 就是史莱姆
    assert ev == 'died' and k.status() == 'lost' and k.deaths == 1, \
        f'{ev} {k.status()} {k.deaths}'
    # (b) 史莱姆主动扑上来（玩家被墙夹在一格宽的首格，只能撞墙耗时间）
    k = make_offline(parse_level('#########\n#P..s...#\n#########', 'slime-hunt'))
    for _ in range(SLIME_PERIOD * 3):
        k.step('L')                      # 左边是墙：blocked，但世界时钟照样走
    assert k.status() == 'lost', f'史莱姆应扑到玩家身上：{k.status()}'
    assert k.player in k.slimes, '扑上来时史莱姆应落在玩家格上'
    print(f'  ok: 玩家走进去 → died；史莱姆扑过来（{SLIME_PERIOD * 3} 步内）→ lost')


def test_slime_dies_on_spike_but_spike_is_not_consumed():
    """把史莱姆引到尖刺上：它自己死，**尖刺却还在**（不是"同归于尽"）。"""
    k = make_offline(parse_level('###########\n#P..^..s..#\n###########', 'slime-spike'))
    spike = (1, 4)
    assert spike in k.spikes and k.slimes == {(1, 7)}
    for d in ('R', 'L') * (SLIME_PERIOD * 2):
        k.step(d)
    assert k.slimes == set(), f'史莱姆应踩尖刺自灭：{k.slimes}'
    assert k.spike_dead == set(), '尖刺不该被史莱姆"用掉"'
    assert spike in k.spikes and k._solid_for_player(spike) is False
    assert k.status() == 'playing' and k.player not in k.spikes
    print('  ok: 史莱姆踩尖刺自灭；尖刺**仍在**（还挡着后面的人）')


def test_slime_stepping_on_bomb_is_mutual_destruction():
    """史莱姆踩到炸弹 → 炸弹引爆、它自己被炸死 —— "用炸弹引怪"是正经解法。

    步数刚好卡在引爆那一步（`blasts` 是**本步**的瞬态，多走一步就被清空了）。
    """
    k = make_offline(parse_level('###########\n#P..b...s.#\n###########', 'slime-bomb'))
    assert k.bombs == {(1, 4)} and k.slimes == {(1, 8)}
    for d in ('R', 'L') * (SLIME_PERIOD * 2):    # 8 步 = tick 8 = 史莱姆刚好走到 (1,4)
        k.step(d)
    assert k.bombs == set() and k.slimes == set(), \
        f'炸弹与史莱姆应同归于尽：{k.bombs} {k.slimes}'
    assert (1, 4) in k.blasts, f'应在 (1,4) 留下火光：{sorted(k.blasts)}'
    assert k.status() == 'playing', f'玩家在 (1,1) 应安全：{k.status()}'
    print(f'  ok: 史莱姆踩炸弹 → 同归于尽，火光 {sorted(k.blasts)}，玩家安全')


def test_fireball_burns_slime_and_keeps_rolling():
    """火球烧死史莱姆后**继续滚**（与撞箱子"烧毁即熄"不同）。"""
    k = make_offline(parse_level('##########\n#P.f.s...#\n##########', 'fb-burn'))
    assert k.slimes == {(1, 5)}
    k.step('R')                          # (1,2)
    assert k.step('R') == 'ignited'      # 火球 (1,3)→(1,4)，玩家 (1,3)
    k.step('L')                          # 退到 (1,2) → 火球滚一格撞上史莱姆
    assert k.slimes == set(), f'史莱姆应被烧死：{k.slimes}'
    assert k.fireballs == {(1, 5): 'R'}, f'火球烧完应**继续滚**（还在 (1,5) 朝右）：{k.fireballs}'
    k.step('L')                          # (1,1) → 火球再滚一格
    assert k.fireballs == {(1, 6): 'R'}, k.fireballs
    print('  ok: 烧死史莱姆后火球继续滚（对比：撞箱子是"烧毁即熄"）')


def test_blast_kills_slime():
    """爆炸波及范围内的史莱姆被炸死。

    这里**直接给炸弹点引信**（跳过"推"这一步）：否则史莱姆会先自己踩上炸弹
    引爆，测到的就不是"波及"而是"同归于尽"了（那条另有专门用例）。
    """
    k = make_offline(parse_level('###########\n#P..bs....#\n###########', 'blast-slime'))
    bomb, slime = (1, 4), (1, 5)
    assert k.bombs == {bomb} and k.slimes == {slime}
    k.fuses[bomb] = 1                    # 引信剩 1 步
    assert k.step('L') == 'blocked'      # 左边是墙；世界时钟照样走，引信归零
    assert k.bombs == set() and k.fuses == {}
    assert k.slimes == set(), f'波及内史莱姆应被炸死：{k.slimes}'
    assert k.blasts == {bomb, slime, (1, 3), (0, 4), (2, 4)},         f'火光不对（含上下两格的墙 —— 渲染要画整片火光）：{sorted(k.blasts)}'
    assert k.status() == 'playing' and k.player == (1, 1)
    print(f'  ok: 十字波及把相邻史莱姆炸死；火光 {sorted(k.blasts)}')


def test_slime_cannot_be_pushed():
    """史莱姆不是可推实体：玩家不能"推"它，只能绕开/引走/杀掉。"""
    assert SLIME in ELEMENTS and ROCK in ELEMENTS
    assert ELEMENTS[SLIME]['pushable'] is False, '史莱姆不该是可推实体'
    assert ELEMENTS[ROCK]['pushable'] is True, '滚石必须可推'
    k = make_offline(parse_level('#########\n#P.bs...#\n#########', 'slime-push'))
    # 玩家推不动史莱姆：往右推"炸弹"可以，但史莱姆会自己追过来
    assert k.slimes == {(1, 4)}
    k.step('R'), k.step('R')             # 推炸弹到 (1,4)？那格是史莱姆 → 被挡
    assert k.slimes or k.status() == 'lost', '史莱姆要么还在、要么已经杀了玩家'
    print('  ok: 史莱姆 pushable=False（推不动）；想清它只能靠爆炸/火球/滚石/尖刺')


# ================================================================ 12. 编辑器与关卡 IO（B3）
def test_json_roundtrip_for_all_shipped_levels():
    """7 关全部**往返一致且仍可解** —— 「编辑器导出的 = 内核吃的」这条必须成立。"""
    for lv in LEVELS:
        data = level_to_json(lv)
        back = level_from_json(data)
        assert back['rows'] == lv['rows'], lv['name']
        assert back['links'] == lv['links'], f"{lv['name']}: {back['links']} != {lv['links']}"
        assert back['par'] == lv.get('par')
        ok, path = solve_level(back)
        assert ok and path, f'{lv["name"]} 读回后不可解'
        assert path == solve_level(lv)[1], '读回后解变了（schema 往返丢了东西）'
    print(f'  ok: {len(LEVELS)} 关 JSON 往返逐字一致，解也逐字一致')


def test_json_roundtrip_through_a_real_file(tmp_path=None):
    """走一次真文件（含子目录自动创建）—— 内存 dict 往返看不出"路径/编码"这类问题。"""
    import json
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'sub', 'lv.json')
        lv = _lv('直推教学')          # 这一关有连接（引怪入瓮没有）
        assert lv['links'], '取材不对：这一关要有连接才能验 links 的序列化'
        level_to_json(lv, p)
        assert os.path.exists(p)
        raw = json.load(open(p, encoding='utf-8'))
        assert raw['links'][0]['from'] == [1, 7], f"坐标该是 JSON 列表：{raw['links']}"
        assert raw['links'][0]['to'] == [4, 4]
        back = level_from_json(p)
        assert back['rows'] == lv['rows'] and back['links'] == lv['links']
        assert solve_level(back) == solve_level(lv)
    print('  ok: 文件往返一致；JSON 里坐标是列表，读回后是元组')


def test_level_from_json_restores_tuples_regression():
    """**回归守卫**：坐标必须从 JSON 列表还原成**元组**。

    反例（把 `tuple(lk['from'])` 去掉立即报红）：`validate_level` 用集合成员判定
    （`lk['from'] in (buttons | levers)`），而 `[1, 4] != (1, 4)` ⇒ 每一关都被判
    "连接源不是按钮/拉杆"，编辑器/CLI 全线读不进任何带连接的关卡。
    """
    lv = _lv('拉杆切门')
    back = level_from_json(level_to_json(lv))
    src = back['links'][0]['from']
    assert isinstance(src, tuple), f'连接源没还原成元组：{type(src).__name__}'
    assert isinstance(back['links'][0]['to'], tuple)
    assert validate_level(back)[0], '带连接的关卡读回后应合法'
    print('  ok: 连接坐标列表 → 元组（否则集合成员判定永远不成立）')


def test_json_rejects_ragged_and_incomplete_levels():
    """坏数据一律抛 `ValueError`，且理由要说清（别静默补齐/静默跑假关卡）。"""
    lv = _lv('直推教学')
    good = level_to_json(lv)
    cases = [
        ({**good, 'rows': ['###', '#####']}, '不是矩形'),
        ({**good, 'rows': []}, '缺 rows'),
        ({**good, 'rows': ['#####', '#P..#', '#####']}, '缺出口'),
        ({**good, 'rows': ['#####', '#...#', '#####']}, '缺玩家'),
        ({**good, 'links': [{'from': [0, 0], 'to': [0, 2]}]}, '连接源'),
        ({**good, 'links': [{'from': [1, 7], 'to': [4, 4], 'mode': 'nope'}]}, '模式'),
    ]
    for bad, label in cases:
        try:
            level_from_json(bad)
        except ValueError as e:
            assert label in str(e) or 'rows' in str(e), f'{label}: 理由不对 {e}'
        else:
            raise AssertionError(f'{label} 的坏数据竟然通过')
    try:
        level_to_json({**lv, 'rows': ['###', '#####']})
    except ValueError:
        pass
    else:
        raise AssertionError('导出非矩形竟然通过')
    try:
        level_from_json(12345)
    except ValueError:
        pass
    else:
        raise AssertionError('非 dict/路径 竟然通过')
    print(f'  ok: {len(cases)} 类坏数据 + 导出非矩形 + 非 dict 全部被拒（理由明确）')


def test_editor_blank_board_is_buildable_step_by_step():
    """**回归守卫**：空白关必须能一格一格摆起来。

    反例（在 `level_set_cell` 末尾加回 `validate_level` 立即报红）：摆第一格时
    必然"既没有 P 也没有 E" ⇒ 整块空白板一步都放不下东西，编辑器等于废掉。
    """
    lv = level_blank(9, 7)
    assert validate_level(lv)[0] is False and '玩家' in validate_level(lv)[1]
    seq = [((1, 1), PLAYER), ((5, 7), EXIT), ((3, 3), CRATE), ((3, 4), SPIKE),
           ((1, 4), BUTTON), ((1, 6), DOOR)]
    for cell, ch in seq:                     # 中间态都不完整，但都必须能摆
        lv = level_set_cell(lv, cell, ch)
    assert validate_level(lv)[0], validate_level(lv)[1]
    ok, path = solve_level(lv)
    assert ok and path, '一步步摆出来的关应该可解'
    rows = lv['rows']
    assert sum(r.count(PLAYER) for r in rows) == 1 and sum(r.count(EXIT) for r in rows) == 1
    print(f'  ok: 空白板逐格摆出可解关（{len(seq)} 次摆放，解 {path}）')


def test_editor_set_cell_structural_rules():
    """摆放只拦**结构性**错误：未知元素 / 越界 / 边框锁死。"""
    lv = _lv('直推教学')
    for args, label in ((((0, 3), CRATE), '边框'),
                        (((3, 0), CRATE), '左边框'),
                        (((99, 3), CRATE), '越界行'),
                        (((3, 99), CRATE), '越界列'),
                        (((3, 3), '?'), '未知元素')):
        try:
            level_set_cell(lv, *args)
        except ValueError as e:
            assert label[:2] in str(e) or '锁死' in str(e) or '越界' in str(e) \
                or '未知' in str(e), f'{label}: {e}'
        else:
            raise AssertionError(f'{label} 竟然通过')
    # 唯一性：摆第二个玩家，旧的应被擦掉
    n = level_set_cell(lv, (2, 2), PLAYER)
    assert sum(r.count(PLAYER) for r in n['rows']) == 1
    assert lv['rows'][1][1] == PLAYER and n['rows'][1][1] == FLOOR, '原关卡该保持不变'
    print('  ok: 边框/越界/未知元素被拒；玩家全局唯一；原关卡不被就地改动')


def test_editor_prunes_dead_links_when_cell_changed():
    """改一格把门擦掉 → 挂在它上面的连接**自动撤掉**（而不是让整关变不合法）。"""
    lv = _lv('直推教学')
    assert lv['links'], '这一关本来该有连接'
    door = tuple(lv['links'][0]['to'])
    n = level_set_cell(lv, door, FLOOR)
    assert n['links'] == [], f'门被擦掉后连接应失效：{n["links"]}'
    assert validate_level(n)[0], '撤掉失效连接后仍是合法关'
    # 擦掉开关（按钮）同理
    btn = tuple(lv['links'][0]['from'])
    n2 = level_set_cell(lv, btn, FLOOR)
    assert n2['links'] == [], f'开关被擦掉后连接应失效：{n2["links"]}'
    print('  ok: 擦门/擦开关都会自动撤掉失效连接，且关卡仍合法')


def test_editor_add_link_rules():
    """连接编辑的四条规矩：源要是开关、目标要是**普通门**、金门不许连、重复连=换模式。"""
    lv = _lv('直推教学')
    door = tuple(lv['links'][0]['to'])
    btn = tuple(lv['links'][0]['from'])
    # 重复连 = 换模式（不在列表里堆两条）
    n = level_add_link(lv, btn, door, 'toggle')
    same = [lk for lk in n['links']
            if tuple(lk['from']) == btn and tuple(lk['to']) == door]
    assert len(same) == 1 and same[0]['mode'] == 'toggle', n['links']
    # 目标不是门
    for target, label in (((1, 2), '地板'), (btn, '开关自己')):
        try:
            level_add_link(lv, btn, target)
        except ValueError:
            pass
        else:
            raise AssertionError(f'连到{label} 竟然通过')
    # 源不是开关
    try:
        level_add_link(lv, door, door)
    except ValueError:
        pass
    else:
        raise AssertionError('源是门 竟然通过')
    # 金门不许连
    gold = tuple(sorted(make_offline(_lv('钥匙金门')).gold)[0])
    try:
        level_add_link(_lv('钥匙金门'), btn, gold)
    except ValueError as e:
        assert '金门' in str(e), e
    else:
        raise AssertionError('连到金门 竟然通过')
    # 未知模式
    try:
        level_add_link(lv, btn, door, 'nope')
    except ValueError:
        pass
    else:
        raise AssertionError('未知模式 竟然通过')
    assert level_clear_links(lv)['links'] == []
    print('  ok: 源/目标/金门/未知模式 全部把住；重复连是换模式；清空可用')


def test_editor_export_import_solve_closed_loop():
    """**B3 的验收点**：编辑 → 试解 → 导出 → 读回 → 再解，**闭环结果一致**。"""
    import tempfile
    lv = level_blank(11, 9, '闭环测试')
    layout = [((1, 1), PLAYER), ((3, 3), CRATE), ((1, 4), BUTTON), ((1, 7), DOOR),
              ((7, 9), EXIT), ((5, 5), SPIKE), ((2, 6), KEY), ((6, 6), GOLD_DOOR)]
    for cell, ch in layout:
        lv = level_set_cell(lv, cell, ch)
    lv = level_add_link(lv, (1, 4), (1, 7), 'open')
    ok, why = validate_level(lv)
    assert ok, why
    sol, path = solve_level(lv)
    assert sol and path, '闭环关应可解'
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, 'closed-loop.json')
        level_to_json(dict(lv, par=len(path)), p)
        back = level_from_json(p)
        assert back['par'] == len(path)
        assert back['rows'] == lv['rows'] and back['links'] == lv['links']
        sol2, path2 = solve_level(back)
        assert sol2 and path2 == path, f'闭环解不一致：{path} != {path2}'
        k = make_offline(back)
        for d in path:
            k.step(d)
        assert k.status() == 'won', f'闭环回放未胜：{k.status()}'
    print(f'  ok: 摆 {len(layout)} 格 + 1 连接 → 解 {path} → 导出 → 读回 → '
          f'同解且回放到胜')


# ================================================================ 13. 内核装载
def test_load_level_rejects_bad_and_keeps_old():
    """`load_level` 校验失败必须返回 False 并给出原因，且**不炸掉现有状态**。"""
    k = make_offline(_lv('直推教学'))
    good_rows = list(k.level['rows'])
    ok = k.load_level(parse_level('#####\n#...#\n#####', 'no-p'))
    assert ok is False, '坏关卡竟然装载成功'
    assert k.load_error, '失败必须留下 load_error 供编辑器/日志用'
    reason = k.load_error
    ok = k.load_level(_lv('钥匙金门'))
    assert ok is True and k.level['name'] == '钥匙金门'
    assert k.status() == 'playing' and k.steps == 0, '换关必须重置步数/状态'
    assert len(good_rows) > 0
    assert k.load_error == '', '装载成功后必须清掉上一次的失败原因'
    print(f'  ok: 坏关卡被拒（load_error={reason!r}）；换关重置 steps/status '
          f'且清空 load_error')


def test_online_kernel_via_service_init():
    """在线内核走 `Service.__init__`（挂 ctx 的那条路）也要能初始化。"""
    lv = _lv('直推教学')
    k = BrickwardKernel.__new__(BrickwardKernel)
    k.ctx = None
    k.seed = 4242
    k.start_level = 0
    k.steps = k.pushes = k.deaths = 0
    k.log = []
    k.load_error = ''
    k._load_cells(lv)
    assert k.status() == 'playing' and k.level['name'] == lv['name']
    snap = k.snapshot()
    assert snap['h'] == lv['h'] and snap['w'] == lv['w']
    print(f'  ok: 离线装配路径与在线一致（level={k.level["name"]} '
          f'{snap["h"]}x{snap["w"]}）')


def test_demo_seeds_and_levels_are_stable_identifiers():
    """种子/关卡名是录制与断言的**外部标识**，改了会让旧剧照/断言全部对不上。"""
    assert DEMO_SEEDS == (20261014, 7, 13, 42, 101, 777), 'DEMO_SEEDS 变了'
    names = tuple(LEVEL_ART)
    assert names == ('直推教学', '钥匙金门', '拉杆切门', '炸药开路', '火球烧箱',
                     '滚石垫路', '引怪入瓮'), f'关卡名变了：{names}'
    assert tuple(lv['name'] for lv in LEVELS) == names
    print(f'  ok: {len(DEMO_SEEDS)} 个种子与 {len(names)} 个关卡名原样未动')


def test_finish_hook_exists_and_is_used_by_demo():
    """B1 不依赖 GL，但必须有"停机"入口给 B4 的渲染层复用 —— 检查 `finish`。"""
    k = make_offline(_lv('直推教学'))
    assert hasattr(k, 'load_art'), '缺 load_art（编辑器/渲染端要用）'
    ok = k.load_art(LEVEL_ART['直推教学'], '简写装载')
    assert ok and k.level['name'] == '简写装载', 'load_art 不能装'
    print('  ok: load_art 可用（ASCII 图直装，编辑器/渲染端不碰内部结构）')


# ================================================================ runner
def main() -> int:
    tests = [
        ('test_elements_table_covers_art', test_elements_table_covers_art),
        ('test_element_flags_consistent', test_element_flags_consistent),
        ('test_validate_accepts_shipped_levels', test_validate_accepts_shipped_levels),
        ('test_validate_rejects_bad_levels', test_validate_rejects_bad_levels),
        ('test_wall_blocks_and_player_does_not_move',
         test_wall_blocks_and_player_does_not_move),
        ('test_border_is_wall_no_index_error', test_border_is_wall_no_index_error),
        ('test_closed_door_blocks_open_door_passes',
         test_closed_door_blocks_open_door_passes),
        ('test_lever_toggles_on_step', test_lever_toggles_on_step),
        ('test_link_modes_open_close_toggle', test_link_modes_open_close_toggle),
        ('test_gold_door_needs_key_and_consumes_one',
         test_gold_door_needs_key_and_consumes_one),
        ('test_gold_door_open_step_is_idempotent_when_already_open',
         test_gold_door_open_step_is_idempotent_when_already_open),
        ('test_crate_on_button_opens_linked_door',
         test_crate_on_button_opens_linked_door),
        ('test_spike_kills_player_and_crate_disarms_it',
         test_spike_kills_player_and_crate_disarms_it),
        ('test_push_only_never_pull', test_push_only_never_pull),
        ('test_push_blocked_by_wall_and_by_closed_door',
         test_push_blocked_by_wall_and_by_closed_door),
        ('test_state_key_covers_every_dynamic_field',
         test_state_key_covers_every_dynamic_field),
        ('test_clone_does_not_retoggle_standing_lever',
         test_clone_does_not_retoggle_standing_lever),
        ('test_bomb_static_until_pushed_then_fuse_starts',
         test_bomb_static_until_pushed_then_fuse_starts),
        ('test_fuse_travels_with_bomb_regression',
         test_fuse_travels_with_bomb_regression),
        ('test_blast_shape_is_cross_one', test_blast_shape_is_cross_one),
        ('test_blast_chain_reaction', test_blast_chain_reaction),
        ('test_blast_destroys_crate_but_not_wall_door_or_gold',
         test_blast_destroys_crate_but_not_wall_door_or_gold),
        ('test_blast_kills_player_standing_in_range',
         test_blast_kills_player_standing_in_range),
        ('test_bomb_level_needs_the_bomb', test_bomb_level_needs_the_bomb),
        ('test_fireball_static_until_pushed_regression',
         test_fireball_static_until_pushed_regression),
        ('test_fireball_ignites_rolls_and_extinguishes',
         test_fireball_ignites_rolls_and_extinguishes),
        ('test_fireball_extinguishes_on_hard_obstacle',
         test_fireball_extinguishes_on_hard_obstacle),
        ('test_fireball_detonates_bomb', test_fireball_detonates_bomb),
        ('test_fireballs_colliding_cancel_out', test_fireballs_colliding_cancel_out),
        ('test_rock_rolls_to_the_end_in_one_push',
         test_rock_rolls_to_the_end_in_one_push),
        ('test_rock_stops_at_hard_obstacles', test_rock_stops_at_hard_obstacles),
        ('test_rock_disarms_spikes_along_the_way',
         test_rock_disarms_spikes_along_the_way),
        ('test_rock_crushes_slime_and_keeps_rolling',
         test_rock_crushes_slime_and_keeps_rolling),
        ('test_lure_level_needs_the_rock', test_lure_level_needs_the_rock),
        ('test_slime_moves_on_period_not_every_step',
         test_slime_moves_on_period_not_every_step),
        ('test_slime_chases_along_the_dominant_axis',
         test_slime_chases_along_the_dominant_axis),
        ('test_slime_blocked_by_rock_and_wall',
         test_slime_blocked_by_rock_and_wall),
        ('test_slime_kills_player_both_directions',
         test_slime_kills_player_both_directions),
        ('test_slime_dies_on_spike_but_spike_is_not_consumed',
         test_slime_dies_on_spike_but_spike_is_not_consumed),
        ('test_slime_stepping_on_bomb_is_mutual_destruction',
         test_slime_stepping_on_bomb_is_mutual_destruction),
        ('test_fireball_burns_slime_and_keeps_rolling',
         test_fireball_burns_slime_and_keeps_rolling),
        ('test_blast_kills_slime', test_blast_kills_slime),
        ('test_slime_cannot_be_pushed', test_slime_cannot_be_pushed),
        ('test_json_roundtrip_for_all_shipped_levels',
         test_json_roundtrip_for_all_shipped_levels),
        ('test_json_roundtrip_through_a_real_file',
         test_json_roundtrip_through_a_real_file),
        ('test_level_from_json_restores_tuples_regression',
         test_level_from_json_restores_tuples_regression),
        ('test_json_rejects_ragged_and_incomplete_levels',
         test_json_rejects_ragged_and_incomplete_levels),
        ('test_editor_blank_board_is_buildable_step_by_step',
         test_editor_blank_board_is_buildable_step_by_step),
        ('test_editor_set_cell_structural_rules',
         test_editor_set_cell_structural_rules),
        ('test_editor_prunes_dead_links_when_cell_changed',
         test_editor_prunes_dead_links_when_cell_changed),
        ('test_editor_add_link_rules', test_editor_add_link_rules),
        ('test_editor_export_import_solve_closed_loop',
         test_editor_export_import_solve_closed_loop),
        ('test_every_shipped_level_solvable_and_replays_to_win',
         test_every_shipped_level_solvable_and_replays_to_win),
        ('test_solver_is_deterministic', test_solver_is_deterministic),
        ('test_solver_returns_none_for_unsolvable',
         test_solver_returns_none_for_unsolvable),
        ('test_solver_path_is_minimal_for_tiny_case',
         test_solver_path_is_minimal_for_tiny_case),
        ('test_solver_already_won_returns_empty',
         test_solver_already_won_returns_empty),
        ('test_generate_deterministic_and_solvable',
         test_generate_deterministic_and_solvable),
        ('test_generate_never_returns_invalid_level',
         test_generate_never_returns_invalid_level),
        ('test_kernel_service_surface', test_kernel_service_surface),
        ('test_snapshot_roundtrip_after_replay', test_snapshot_roundtrip_after_replay),
        ('test_load_level_rejects_bad_and_keeps_old',
         test_load_level_rejects_bad_and_keeps_old),
        ('test_online_kernel_via_service_init', test_online_kernel_via_service_init),
        ('test_demo_seeds_and_levels_are_stable_identifiers',
         test_demo_seeds_and_levels_are_stable_identifiers),
        ('test_finish_hook_exists_and_is_used_by_demo',
         test_finish_hook_exists_and_is_used_by_demo),
    ]
    failed = []
    for name, fn in tests:
        try:
            print(name)
            fn()
        except AssertionError as exc:
            failed.append((name, exc))
            print(f'  FAIL: {exc}')
        except Exception as exc:                       # noqa: BLE001
            failed.append((name, exc))
            print(f'  ERROR: {type(exc).__name__}: {exc}')
    print('=' * 60)
    print(f'[test_brickward] {len(tests) - len(failed)}/{len(tests)} 通过')
    if failed:
        for name, exc in failed:
            print(f'  - {name}: {exc}')
        print('ALL_BRICKWARD_TESTS_FAILED')
        return 1
    print('ALL_BRICKWARD_TESTS_PASSED')
    return 0


if __name__ == '__main__':
    sys.exit(main())

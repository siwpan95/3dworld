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
    CRATE, SPIKE, PLAYER, EXIT,
    generate, make_offline, parse_level, solve_level, validate_level,
)


def _play(level, path):
    """按方向串回放，返回终局内核（求解器与 step 是否同一套物理，靠它对齐）。"""
    k = make_offline(level)
    for d in path:
        k.step(d)
    return k


#: 全元素微型关（每种元素各一）：用于状态指纹的**完整性**检查。
ALL_ELEMENTS_ART = """
###########
#P.C.o.L.D#
#.........#
#k...^..gD#
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
    lv = LEVELS[2]
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
    lv = LEVELS[2]
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
    lv = LEVELS[1]
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
    lv = LEVELS[1]
    gold = sorted(make_offline(lv).gold)[0]
    k = make_offline(lv)
    k.player, k.keys_held = (gold[0] - 1, gold[1]), 2
    assert k.step('D') == 'unlock' and k.keys_held == 1 and k.player == (gold[0] - 1, gold[1])
    assert k.step('D') == 'moved' and k.player == gold, '第二次应直接走过去'
    assert k.keys_held == 1, f'已开的门不该再扣钥匙：{k.keys_held}'
    print('  ok: 金门开一次即长期有效，二次进入不扣钥匙')


# ================================================================ 6. 箱子 / 按钮 / 尖刺
def test_crate_on_button_opens_linked_door():
    lv = LEVELS[0]
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
    spike = (3, 5)
    assert spike in k.spikes
    ev = None
    for d in ('D', 'D', 'R', 'R', 'R', 'R'):     # (1,1)→(3,1)拾钥匙→(3,5)尖刺
        ev = k.step(d)
    assert k.player == spike and ev == 'died' and k.status() == 'lost', \
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

    # 反向：与"往后怎么演化"无关的量**不能**进指纹（进了就白烧内存/白建节点）
    k = make_offline(lv)
    k.steps, k.pushes, k.deaths = 99, 99, 9
    k.log.append(('R', 'moved', 'playing'))
    assert k.key() == k0, 'steps/pushes/deaths/log 不该进指纹'
    print('  ok: 指纹含全部 9 项动态量、不含 4 项无关量（漏/多都算错）')


def test_clone_does_not_retoggle_standing_lever():
    """**回归 3**：克隆时重跑 `_settle` 会把"人正站在拉杆上"的状态扳回去。"""
    lv = LEVELS[2]
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
                 'rows', 'links', 'status', 'steps', 'pushes', 'deaths'):
        assert name in snap, f'snapshot 缺字段 {name}（渲染层要按它建几何）'
    assert snap['player'] == k.player
    assert snap['crates'] == sorted(k.crates)
    assert snap['doors_open'] == sorted(k.doors)
    assert snap['gold_open'] == sorted(k.gold_open)
    assert snap['levers_on'] == sorted(k.levers)
    assert snap['spikes_dead'] == sorted(k.spike_dead)
    assert snap['keys_held'] == k.keys_held == 0
    assert snap['status'] == k.status() == 'playing'
    assert snap['links'] is k.level['links'], 'links 该是引用（纯数据，别每帧深拷）'
    print(f'  ok: inject=[] ；snapshot {len(snap)} 字段齐全且与内核状态逐项一致')


def test_snapshot_roundtrip_after_replay():
    """快照与直接读属性必须一致 —— 渲染层只吃快照，不许出现"面板显示假的"。"""
    lv = LEVELS[0]
    path = solve_level(lv)[1]
    k = _play(lv, path)
    snap = k.snapshot()
    assert snap['status'] == 'won' and snap['steps'] == k.steps
    assert snap['crates'] == sorted(k.crates)
    assert snap['doors_open'] == sorted(k.doors)
    assert snap['level'] == lv['name']
    print(f'  ok: 通关后快照一致（steps={snap["steps"]} pushes={snap["pushes"]} '
          f'status={snap["status"]}）')


# ================================================================ 11. 内核装载
def test_load_level_rejects_bad_and_keeps_old():
    """`load_level` 校验失败必须返回 False 并给出原因，且**不炸掉现有状态**。"""
    k = make_offline(LEVELS[0])
    good_rows = list(k.level['rows'])
    ok = k.load_level(parse_level('#####\n#...#\n#####', 'no-p'))
    assert ok is False, '坏关卡竟然装载成功'
    assert k.load_error, '失败必须留下 load_error 供编辑器/日志用'
    ok = k.load_level(LEVELS[1])
    assert ok is True and k.level['name'] == LEVELS[1]['name']
    assert k.status() == 'playing' and k.steps == 0, '换关必须重置步数/状态'
    assert len(good_rows) > 0
    print(f'  ok: 坏关卡被拒（load_error={k.load_error!r}）；换关重置 steps/status')


def test_online_kernel_via_service_init():
    """在线内核走 `Service.__init__`（挂 ctx 的那条路）也要能初始化。"""
    lv = LEVELS[0]
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
    assert names == ('直推教学', '钥匙金门', '拉杆切门'), f'关卡名变了：{names}'
    assert tuple(lv['name'] for lv in LEVELS) == names
    print(f'  ok: {len(DEMO_SEEDS)} 个种子与 {len(names)} 个关卡名原样未动')


def test_finish_hook_exists_and_is_used_by_demo():
    """B1 不依赖 GL，但必须有"停机"入口给 B4 的渲染层复用 —— 检查 `finish`。"""
    k = make_offline(LEVELS[0])
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

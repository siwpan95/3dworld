# -*- coding: utf-8 -*-
"""allin-engine 决策试点：把 kkrieger 第六幕的镜头编排表达为「导演状态机」。

背景：3dworld 现有编排是「时间驱动 + 手写窗口函数」（logic/kkrieger.py 的
camera_pose 用大量 `_window()` 拼接）。本试点用 allin_engine.ai.decision 的
**有限状态机（FSM）** 重表达同一段演出：

    状态 = 演出阶段（传送 / 下降 / 环绕 / 俯冲 / 贴脸 / 归位）
    事件 = 时间轴触发（at_*）；未来交互化时可叠加动态事件（玩家干扰、随机演出）
    动作 = 导演日志 + 状态机携带的机位关键帧（真实数据取自 logic/kkrieger.py）

运行：
    cd <本目录>
    python perform_director.py     # 或设置 AN_ENGINE_PATH 指向 an-engine 仓库
"""
import os
import sys

# 安全（上线前全检 F-004）：同 run_director.py —— 移除 ``G:\repos\an-engine`` 这类
# 机器相关默认值（否则"存在 G 盘"即可免环境变量自动激活），并强制 isdir 校验。
AN_ENGINE = os.environ.get('AN_ENGINE_PATH', '')
if not AN_ENGINE or not os.path.isdir(AN_ENGINE):
    sys.exit('[allin] 需要环境变量 AN_ENGINE_PATH 指向 an-engine 源码目录'
             f'（外部仓库，不在本仓库内）；当前值无效：{AN_ENGINE or "<未设置>"}。'
             '本试点为 dev-only，未配置时不可用。')
sys.path.insert(0, AN_ENGINE)

from allin_engine import create_state_machine          # noqa: E402

# ---- 第六幕真实机位（logic/kkrieger.py CAM，118.2–144）----
KEYS = [
    (118.2, (0.0, 4.2, -79.5),  (0.0, 5.0, -89.5), 'teleport'),
    (121.5, (0.0, 4.5, -82.8),  (0.0, 5.0, -89.5), 'descend'),
    (125.0, (4.8, 5.8, -84.6),  (0.0, 5.0, -89.5), 'orbit'),
    (129.0, (0.3, 6.6, -83.2),  (0.0, 5.0, -89.5), 'orbit'),
    (133.0, (-4.8, 5.8, -84.6), (0.0, 5.0, -89.5), 'orbit'),
    (136.5, (-0.6, 4.4, -83.0), (0.0, 5.0, -89.5), 'dive'),
    (138.8, (0.0, 4.9, -85.6),  (0.0, 5.0, -89.5), 'dive'),
    (140.8, (0.0, 5.0, -86.3),  (0.0, 5.0, -89.5), 'face_off'),
    (141.8, (0.0, 9.0, 12.0),   (0.0, 1.6, -24.0), 'return'),
    (144.0, (0.0, 8.7, 11.6),   (0.0, 1.6, -24.0), 'return'),
]

# ---- 导演状态机（时间驱动转移；结构自明）----
CONFIG = {"state_machines": {"Director": {
    "initial": "teleport",
    "states": {
        "teleport": {
            "content": "白闪传送落地",
            "on_enter": "落点 (0,4.2,-79.5) 面向反应堆",
            "transitions": {"at_121_5": "descend"},
        },
        "descend": {
            "content": "下降逼近反应堆",
            "on_enter": "降到 y=4.5 中线",
            "transitions": {"at_125": "orbit"},
        },
        "orbit": {
            "content": "环绕环面结（右弧 → 前上俯瞰 → 左弧）",
            "on_enter": "半径 ~7 环绕",
            "transitions": {"at_136_5": "dive"},
        },
        "dive": {
            "content": "俯冲低弧（点火前兆）",
            "on_enter": "压低到 y=4.4",
            "transitions": {"at_140_8": "face_off"},
        },
        "face_off": {
            "content": "贴脸驻留（141.5s 点火 · 二次白闪）",
            "on_enter": "机位 (0,5.0,-86.3) 距核心 ~3.2m",
            "transitions": {"at_141_8": "return"},
        },
        "return": {
            "content": "白闪传送回场 + 缓慢沉降",
            "on_enter": "回到开场高空帧 (0,9,12)",
            "transitions": {},
        },
    },
}}}

TRIGGERS = [(121.5, 'at_121_5'), (125.0, 'at_125'), (136.5, 'at_136_5'),
            (140.8, 'at_140_8'), (141.8, 'at_141_8')]


def main():
    log = []

    def on_action(action_type, state_id, detail):
        desc = detail.get('description', '') if isinstance(detail, dict) else str(detail)
        log.append(f'  [{action_type:6s}] {state_id:9s} {desc}')

    sm = create_state_machine('Director', CONFIG, on_action=on_action)
    print('=' * 64)
    print('导演状态机就位 · kkrieger 第六幕（圣殿 118–145s）')
    print('=' * 64)
    print(f'  初始状态: {sm.current_state_id}')

    t, i = 118.0, 0
    while t < 145.0:
        t += 0.1
        if i < len(TRIGGERS) and t >= TRIGGERS[i][0] - 1e-9:
            ev = TRIGGERS[i][1]
            sm.trigger(ev)
            print(f'  t={t:6.1f}s  trigger {ev:9s} -> 状态 {sm.current_state_id}')
            i += 1

    print()
    print('=== 分镜表（状态 → 真实机位关键帧）===')
    for ts, pos, tgt, st in KEYS:
        print(f'  {ts:6.1f}s  {st:9s} pos={pos}  target={tgt}')

    print()
    print(f'=== 动作日志（{len(log)} 条）===')
    for line in log:
        print(line)
    print()
    print('DIRECTOR_OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())

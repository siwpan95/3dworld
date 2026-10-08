# -*- coding: utf-8 -*-
"""zhuangtaiji 编排试点：kkrieger 第六幕「多引擎协同演出」。

用 zhuangtaiji 的**编排层**（事件总线 + 声明式绑定 when→then）把两个独立
范式连成演出系统：

    SM_Director（状态机）——演出阶段（传送/下降/环绕/俯冲/贴脸/归位）
    MC_Embers（马尔可夫链）——圣殿反应堆余烬的随机闪烁模式

绑定：状态机每次 `enter` → **级联**推进一步马尔可夫链
（**同一时间轴，余烬序列随机**——演出「随机变体」的最简实现）。

全部逻辑在 `director_config.json`（单一数据源）；本脚本只负责：
加载配置 → 按真实幕时刻驱动 → 展示级联与两次运行的变体差异。

运行：
    python run_director.py     # ZHUANGTAIJI_PATH 可覆盖源码路径
"""
import json
import os
import sys

Z = os.environ.get('ZHUANGTAIJI_PATH', r'G:\repos\zhuangtaiji')
sys.path.insert(0, Z)

from core.orchestrator import build_orchestrator_from_config  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
TIMELINE = [(121.5, 'at_121_5'), (125.0, 'at_125'), (136.5, 'at_136_5'),
            (140.8, 'at_140_8'), (141.8, 'at_141_8')]


def _states(o):
    """从 engine_status 提取 {别名: 状态摘要}（summary 取首行冒号后一段）。"""
    out = {}
    for e in o.engine_status():
        s = str(e.get('summary', '?')).splitlines()[0]
        out[e.get('alias', '?')] = s.split(':', 1)[-1].strip() if ':' in s else s
    return out


def run_once(cfg, tag):
    print('=' * 66)
    print(f'演出运行 [{tag}]')
    print('=' * 66)
    o = build_orchestrator_from_config(cfg, 'kkrieger_act6')
    o.start()
    st = _states(o)
    print(f'  start 级联完成；引擎: {st}')
    embers = [st.get('mc')]
    for t, ev in TIMELINE:
        o.step_engine('sm', {'event': ev})
        st = _states(o)
        embers.append(st.get('mc'))
        print(f'  t={t:6.1f}s  trigger {ev:9s} → sm={st.get("sm"):9s} mc={st.get("mc")}')
    print()
    print('  余烬序列:', ' → '.join(str(x) for x in embers))
    print()
    print(o.report())
    print(f'[{tag}] 完成')
    print()
    return embers


def main():
    with open(os.path.join(HERE, 'director_config.json'), encoding='utf-8') as f:
        cfg = json.load(f)
    a = run_once(cfg, '变体 A')
    b = run_once(cfg, '变体 B')
    print('=' * 66)
    print('ORCHESTRATION_OK')
    print(f'  变体 A 余烬: {" → ".join(str(x) for x in a)}')
    print(f'  变体 B 余烬: {" → ".join(str(x) for x in b)}')
    print(f'  序列是否相同: {a == b}（同一时间轴驱动，余烬为随机变体）')
    return 0


if __name__ == '__main__':
    sys.exit(main())

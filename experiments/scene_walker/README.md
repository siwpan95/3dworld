# experiments/scene_walker —— 可漫步场景雏形（交互化第一小步）

> 目标：把既有成果组合成「可交互场景」的模板——**S1 场景 JSON（scene_build）
> + FixedStepper（帧率稳健物理）+ 物理漫步（场景几何自动碰撞）+ 发光呼吸**。
> 2026-10-09。

## 组合方式

```
docs/llm_scene_demo.json ──scene_build──▶ 可渲染场景
        │（几何/材质/环境）                    │
        ▼                                      ▼
物理世界（每个 drawable 的 world_aabb 自动注册为静态碰撞）
        │
FixedStepper（1/60 固定步长驱动物理；卡帧不穿透——见 fixed_timestep 试点）
        │
第一人称漫游（WASD 走 / SPACE 跳 / SHIFT 加速；FlyCamera 作位置+朝向容器）
```

## 运行

```bat
:: 需 pyglet 环境（binaries 事故期间可用 tmp\lab-venv）
<venv>\Scripts\python.exe experiments\scene_walker\walk.py
<venv>\Scripts\python.exe experiments\scene_walker\walk.py --file docs\llm_scene_demo.json --flag walk
```

键位：**V** 轨道/漫游切换；**WASD** 走；**SPACE** 跳（漫游态）；**SHIFT** 加速；
鼠标拖动（轨道=环绕 / 漫游=转视角）。

## 验证记录（2026-10-09）

- 轨道模式：`--smoke 3` → `EXIT_OK` ✓
- 漫游模式：`--smoke 4 --flag walk` → **`[walk] landed at (9.50, 0.90, 0.00)`**
  （从空中下落到站地——**物理 + 固定步长工作正常**）+ `EXIT_OK` ✓

## 已知边界（v1）

- 场景碰撞为 AABB 近似（圆柱柱子=方盒；台阶 0.5m 高需跳上，无自动踏步）；
- 未接场景热重载 / 拾取（engine_showcase 已有，可按需移植）；
- 发光呼吸为时间驱动的最小活性；**马尔可夫编排（zhuangtaiji 试点）可替换之**——
  即下一步「演出随机变体进交互场景」的接入点；
- SFX/自适应音乐（bloodmusic）为下一层接入点。
